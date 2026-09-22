"""Slack endpoints (§12, §15) against real compose Postgres — TestClient with dependency
overrides, locally-signed requests (no real Slack workspace is available, §12.4). Not
exhaustive: covers the paths named in the dispatch (url_verification, event_callback
dedupe, bot-message ignore, the shortcut path, and ACL-filtered `@context` retrieval).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from psycopg.types.range import Range
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_app_settings, get_queue, get_slack_client
from app.core.config import Settings, get_settings
from app.core.db import engine, get_db
from app.main import app
from app.models.orm import ContextObjects, Entities, EntityAliases, Sources
from app.queue.postgres import PostgresJobQueue

pytestmark = pytest.mark.integration

SIGNING_SECRET = "test-signing-secret"
SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class FakeSlackClient:
    """In-memory SlackClient test double (§12: 'a small interface... with an in-memory
    fake used in tests')."""

    def __init__(self) -> None:
        self.posted_messages: list[dict] = []
        self.posted_ephemeral: list[dict] = []
        self.members: dict[str, list[str]] = {}
        self.permalinks: dict[tuple[str, str], str] = {}
        self.messages: dict[tuple[str, str], str] = {}

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> None:
        self.posted_messages.append({"channel": channel, "text": text, "thread_ts": thread_ts})

    def post_ephemeral(self, *, channel: str, user: str, text: str) -> None:
        self.posted_ephemeral.append({"channel": channel, "user": user, "text": text})

    def conversations_members(self, channel: str) -> list[str]:
        return self.members.get(channel, [])

    def get_permalink(self, *, channel: str, message_ts: str) -> str | None:
        return self.permalinks.get((channel, message_ts))

    def fetch_message(self, *, channel: str, ts: str) -> str | None:
        return self.messages.get((channel, ts))


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
    return "v0=" + hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()


def _headers(body: bytes, secret: str = SIGNING_SECRET) -> dict[str, str]:
    ts = str(int(time.time()))
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": _sign(secret, ts, body)}


@pytest.fixture
def slack_env():  # type: ignore[no-untyped-def]
    db = SessionFactory()
    tenant_id = uuid.uuid4()
    fake_slack = FakeSlackClient()

    def override_settings() -> Settings:
        base = get_settings()
        return base.model_copy(
            update={"slack_signing_secret": SIGNING_SECRET, "tenant_id": str(tenant_id)}
        )

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_app_settings] = override_settings
    app.dependency_overrides[get_queue] = lambda: PostgresJobQueue(SessionFactory)
    app.dependency_overrides[get_slack_client] = lambda: fake_slack

    client = TestClient(app)
    yield client, db, tenant_id, fake_slack

    app.dependency_overrides.clear()
    db.rollback()
    db.execute(
        text(
            "DELETE FROM context_versions WHERE context_id IN "
            "(SELECT id FROM context_objects WHERE tenant_id = :t)"
        ),
        {"t": tenant_id},
    )
    db.execute(text("DELETE FROM context_objects WHERE tenant_id = :t"), {"t": tenant_id})
    db.execute(
        text("DELETE FROM processing_jobs WHERE payload->>'tenant_id' = :t"), {"t": str(tenant_id)}
    )
    db.execute(text("DELETE FROM sources WHERE tenant_id = :t"), {"t": tenant_id})
    db.execute(text("DELETE FROM entity_aliases WHERE tenant_id = :t"), {"t": tenant_id})
    db.execute(text("DELETE FROM entities WHERE tenant_id = :t"), {"t": tenant_id})
    db.commit()
    db.close()


def test_url_verification_echoes_challenge(slack_env) -> None:  # type: ignore[no-untyped-def]
    client, *_ = slack_env
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    resp = client.post("/sources/slack/events", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc123"}


def test_bad_signature_is_rejected() -> None:
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    client = TestClient(app)
    ts = str(int(time.time()))
    resp = client.post(
        "/sources/slack/events",
        content=body,
        headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": "v0=" + "0" * 64},
    )
    assert resp.status_code == 401


def test_event_callback_message_creates_one_source_and_job_and_dedupes(
    slack_env,  # type: ignore[no-untyped-def]
) -> None:
    client, db, tenant_id, _ = slack_env
    event_id = f"Ev{uuid.uuid4().hex[:10]}"
    body_dict = {
        "type": "event_callback",
        "event_id": event_id,
        "team_id": "T_TEST",
        "event": {
            "type": "message",
            "channel": "C_ACME_DEAL",
            "channel_type": "channel",
            "user": "U_AE_JORDAN",
            "ts": "1700000000.000100",
            "text": "Acme wants SSO live before their board meeting.",
        },
    }
    body = json.dumps(body_dict).encode()

    resp = client.post("/sources/slack/events", content=body, headers=_headers(body))
    assert resp.status_code == 200

    sources = db.query(Sources).filter(Sources.tenant_id == tenant_id).all()
    assert len(sources) == 1
    assert sources[0].stage == "sales"  # C_ACME_DEAL -> sales
    assert sources[0].external_id == event_id

    job_count = db.execute(
        text("SELECT count(*) FROM processing_jobs WHERE payload->>'tenant_id' = :t"),
        {"t": str(tenant_id)},
    ).scalar_one()
    assert job_count == 1

    # Redelivery of the same event_id must not create a second source or job.
    resp2 = client.post("/sources/slack/events", content=body, headers=_headers(body))
    assert resp2.status_code == 200
    assert db.query(Sources).filter(Sources.tenant_id == tenant_id).count() == 1
    job_count2 = db.execute(
        text("SELECT count(*) FROM processing_jobs WHERE payload->>'tenant_id' = :t"),
        {"t": str(tenant_id)},
    ).scalar_one()
    assert job_count2 == 1


def test_bot_message_is_ignored(slack_env) -> None:  # type: ignore[no-untyped-def]
    client, db, tenant_id, _ = slack_env
    body_dict = {
        "type": "event_callback",
        "event_id": f"Ev{uuid.uuid4().hex[:10]}",
        "team_id": "T_TEST",
        "event": {
            "type": "message",
            "channel": "C_ACME_DEAL",
            "channel_type": "channel",
            "bot_id": "B_BOT",
            "ts": "1700000001.000100",
            "text": "automated notice",
        },
    }
    body = json.dumps(body_dict).encode()
    resp = client.post("/sources/slack/events", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert db.query(Sources).filter(Sources.tenant_id == tenant_id).count() == 0


def test_save_as_context_shortcut_creates_source_and_acks(
    slack_env,  # type: ignore[no-untyped-def]
) -> None:
    client, db, tenant_id, fake_slack = slack_env
    interaction_payload = {
        "type": "message_action",
        "callback_id": "save_as_context",
        "user": {"id": "U_PM_SAM"},
        "channel": {"id": "C_PRODUCT", "is_private": False},
        "team": {"id": "T_TEST"},
        "message": {
            "user": "U_PM_SAM",
            "ts": "1700000002.000100",
            "text": "Support SSO by December.",
        },
    }
    raw_body = f"payload={quote(json.dumps(interaction_payload))}".encode()
    resp = client.post(
        "/sources/slack/interactions",
        content=raw_body,
        headers={**_headers(raw_body), "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200

    sources = db.query(Sources).filter(Sources.tenant_id == tenant_id).all()
    assert len(sources) == 1
    assert sources[0].stage == "product"  # C_PRODUCT -> product
    assert len(fake_slack.posted_ephemeral) == 1


def test_block_actions_gap_ignore_replies_not_available_yet(
    slack_env,  # type: ignore[no-untyped-def]
) -> None:
    client, _db, _tenant_id, fake_slack = slack_env
    interaction_payload = {
        "type": "block_actions",
        "user": {"id": "U_PM_SAM"},
        "channel": {"id": "C_PRODUCT"},
        "actions": [{"action_id": "gap_ignore", "value": str(uuid.uuid4())}],
    }
    raw_body = f"payload={quote(json.dumps(interaction_payload))}".encode()
    resp = client.post(
        "/sources/slack/interactions",
        content=raw_body,
        headers={**_headers(raw_body), "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert len(fake_slack.posted_ephemeral) == 1
    assert "isn't available yet" in fake_slack.posted_ephemeral[0]["text"]


def test_context_mention_filters_by_acl(slack_env) -> None:  # type: ignore[no-untyped-def]
    client, db, tenant_id, fake_slack = slack_env

    entity = Entities(id=uuid.uuid4(), tenant_id=tenant_id, name="Acme Corp", slug="acme", kind="customer")
    db.add(entity)
    db.flush()
    db.add(EntityAliases(id=uuid.uuid4(), entity_id=entity.id, tenant_id=tenant_id, alias_normalized="acme"))

    now = datetime.now(UTC)
    visible_source = Sources(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind="slack",
        external_id=f"vis-{uuid.uuid4()}",
        version=1,
        content_hash="h1",
        stage="sales",
        acl=["*"],
        provenance={
            "kind": "slack",
            "workspace_id": "T_TEST",
            "channel_id": "C_ACME_DEAL",
            "message_ts": "1700000003.000100",
            "author_id": "U_AE_JORDAN",
        },
        text="Acme needs SAML SSO through Okta.",
        source_ts=now,
    )
    hidden_source = Sources(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind="slack",
        external_id=f"hid-{uuid.uuid4()}",
        version=1,
        content_hash="h2",
        stage="sales",
        acl=["U_OTHER_ONLY"],
        provenance={
            "kind": "slack",
            "workspace_id": "T_TEST",
            "channel_id": "C_ACME_DEAL",
            "message_ts": "1700000004.000100",
            "author_id": "U_AE_JORDAN",
        },
        text="Private note about Acme pricing.",
        source_ts=now,
    )
    db.add_all([visible_source, hidden_source])
    db.flush()

    db.add(
        ContextObjects(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            entity_id=entity.id,
            type="requirement",
            subject_key="acme:sso",
            content="Acme requires SAML SSO through Okta.",
            attributes={},
            actor_label="Dana Kim",
            actor_role="customer",
            stage="sales",
            authority=4,
            confidence=0.95,
            status="active",
            valid_from=now,
            source_id=visible_source.id,
            evidence_quote="SAML SSO through Okta",
            evidence_span=Range(0, 10),
            version=1,
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        ContextObjects(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            entity_id=entity.id,
            type="constraint",
            subject_key="acme:pricing",
            content="Private pricing note.",
            attributes={},
            actor_label="Jordan Lee",
            actor_role="sales",
            stage="sales",
            authority=2,
            confidence=0.9,
            status="active",
            valid_from=now,
            source_id=hidden_source.id,
            evidence_quote="Private note",
            evidence_span=Range(0, 8),
            version=1,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()

    body_dict = {
        "type": "event_callback",
        "event_id": f"Ev{uuid.uuid4().hex[:10]}",
        "team_id": "T_TEST",
        "event": {
            "type": "app_mention",
            "channel": "C_PRODUCT",
            "channel_type": "channel",
            "user": "U_SOME_CALLER",
            "ts": "1700000005.000100",
            "text": "<@U_BOT> acme sso",
        },
    }
    body = json.dumps(body_dict).encode()
    resp = client.post("/sources/slack/events", content=body, headers=_headers(body))
    assert resp.status_code == 200

    assert len(fake_slack.posted_messages) == 1
    reply = fake_slack.posted_messages[0]["text"]
    assert "SAML SSO through Okta" in reply
    assert "Private pricing note" not in reply
