"""§15 API surface added in Day 2a: /context/search, /context/{id}/history,
/context/{id}/lineage, /entities, /entities/{id}/context, /context/{id}/review,
/conflicts/{relation_id}/resolve — against real compose Postgres via TestClient.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from psycopg.types.range import Range
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.db import engine
from app.main import app
from app.models.orm import ContextObjects, ContextRelations, Entities, Sources

pytestmark = pytest.mark.integration

SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def seeded() -> dict:
    db = SessionFactory()
    tenant_id = uuid.uuid4()
    entity = Entities(id=uuid.uuid4(), tenant_id=tenant_id, name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}", kind="customer")
    db.add(entity)
    db.flush()

    source = Sources(
        id=uuid.uuid4(), tenant_id=tenant_id, kind="drive", external_id=uuid.uuid4().hex,
        content_hash=uuid.uuid4().hex, stage="sales", acl=["*"], provenance={"kind": "drive"},
        text="Acme needs SSO", source_ts=T0,
    )
    db.add(source)
    db.flush()

    active_obj = ContextObjects(
        id=uuid.uuid4(), tenant_id=tenant_id, entity_id=entity.id, type="requirement",
        subject_key="acme:sso", content="Acme requires SSO", attributes={"protocol": "SAML"},
        actor_label="Dana Kim", actor_role="customer", stage="sales", authority=4,
        confidence=0.95, status="active", valid_from=T0, source_id=source.id,
        evidence_quote="Acme needs SSO", evidence_span=Range(0, 14), embedding=[0.1] * 384,
        version=1, created_at=T0, updated_at=T0,
    )
    db.add(active_obj)
    db.flush()

    candidate_obj = ContextObjects(
        id=uuid.uuid4(), tenant_id=tenant_id, entity_id=entity.id, type="requirement",
        subject_key="acme:audit_logs", content="Acme wants audit logs", attributes={},
        actor_label="Dana Kim", actor_role="customer", stage="sales", authority=1,
        confidence=0.5, status="candidate", valid_from=T0, source_id=source.id,
        evidence_quote="Acme needs SSO", evidence_span=Range(0, 14), embedding=[0.2] * 384,
        version=1, created_at=T0, updated_at=T0,
    )
    db.add(candidate_obj)
    db.commit()

    yield {
        "db": db, "tenant_id": tenant_id, "entity": entity, "source": source,
        "active_obj": active_obj, "candidate_obj": candidate_obj,
    }

    db.rollback()
    db.execute(text("DELETE FROM reviews WHERE target_id IN "
                     "(SELECT id FROM context_objects WHERE tenant_id = :t)"), {"t": tenant_id})
    db.execute(text("DELETE FROM context_relations WHERE from_id IN "
                     "(SELECT id FROM context_objects WHERE tenant_id = :t)"), {"t": tenant_id})
    db.execute(text("DELETE FROM context_versions WHERE context_id IN "
                     "(SELECT id FROM context_objects WHERE tenant_id = :t)"), {"t": tenant_id})
    db.execute(text("DELETE FROM context_objects WHERE tenant_id = :t"), {"t": tenant_id})
    db.execute(text("DELETE FROM sources WHERE tenant_id = :t"), {"t": tenant_id})
    db.execute(text("DELETE FROM entities WHERE tenant_id = :t"), {"t": tenant_id})
    db.commit()
    db.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_search_defaults_to_active_status(client: TestClient, seeded: dict) -> None:
    resp = client.get("/context/search", params={"entity": seeded["entity"].slug})
    assert resp.status_code == 200
    body = resp.json()
    ids = {row["id"] for row in body}
    assert str(seeded["active_obj"].id) in ids
    assert str(seeded["candidate_obj"].id) not in ids


def test_search_filters_by_explicit_status(client: TestClient, seeded: dict) -> None:
    resp = client.get(
        "/context/search", params={"entity": seeded["entity"].slug, "status": "candidate"}
    )
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert str(seeded["candidate_obj"].id) in ids


def test_get_context_object(client: TestClient, seeded: dict) -> None:
    resp = client.get(f"/context/{seeded['active_obj'].id}")
    assert resp.status_code == 200
    assert resp.json()["subject_key"] == "acme:sso"


def test_history_returns_initial_version(client: TestClient, seeded: dict) -> None:
    resp = client.get(f"/context/{seeded['active_obj'].id}/history")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["versions"]) == 0  # this fixture inserts the object directly, no
    # context_versions row — history endpoint must still 200 with an empty list, not error.
    assert body["supersession_chain"] == []


def test_lineage_empty_when_no_relations(client: TestClient, seeded: dict) -> None:
    resp = client.get(f"/context/{seeded['active_obj'].id}/lineage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["upstream"] == []
    assert body["downstream"] == []


def test_review_confirm_promotes_candidate_to_active(client: TestClient, seeded: dict) -> None:
    resp = client.post(
        f"/context/{seeded['candidate_obj'].id}/review",
        json={"action": "confirm", "reviewer_id": None, "note": "looks right"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    assert body["version"] == 2

    history = client.get(f"/context/{seeded['candidate_obj'].id}/history").json()
    assert len(history["versions"]) == 1
    assert history["versions"][0]["status"] == "active"


def test_review_edit_keeps_source_and_evidence(client: TestClient, seeded: dict) -> None:
    original_source_id = str(seeded["active_obj"].source_id)
    original_evidence = seeded["active_obj"].evidence_quote
    resp = client.post(
        f"/context/{seeded['active_obj'].id}/review",
        json={"action": "edit", "content": "Acme requires SAML SSO (edited)"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "Acme requires SAML SSO (edited)"
    assert body["source_id"] == original_source_id
    assert body["evidence_quote"] == original_evidence


def test_entities_list_and_entity_context(client: TestClient, seeded: dict) -> None:
    resp = client.get("/entities")
    assert resp.status_code == 200
    slugs = {row["slug"] for row in resp.json()}
    assert seeded["entity"].slug in slugs

    resp = client.get(f"/entities/{seeded['entity'].id}/context")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity"]["slug"] == seeded["entity"].slug
    types_present = {group["type"] for group in body["current"]}
    assert "requirement" in types_present


def test_conflicts_resolve_winner(client: TestClient, seeded: dict) -> None:
    db = seeded["db"]
    tenant_id = seeded["tenant_id"]
    other_obj = ContextObjects(
        id=uuid.uuid4(), tenant_id=tenant_id, entity_id=seeded["entity"].id, type="requirement",
        subject_key="acme:sso", content="SSO not needed", attributes={"protocol": "OIDC"},
        actor_label="Someone", actor_role="sales", stage="sales", authority=2, confidence=0.8,
        status="conflicting", valid_from=T0 + timedelta(days=1), source_id=seeded["source"].id,
        evidence_quote="Acme needs SSO", evidence_span=Range(0, 14), embedding=[0.3] * 384,
        version=1, created_at=T0, updated_at=T0,
    )
    db.add(other_obj)
    seeded["active_obj"].status = "conflicting"
    relation = ContextRelations(
        id=uuid.uuid4(), from_id=other_obj.id, to_id=seeded["active_obj"].id,
        relation="contradicts", created_by="system:lifecycle:R4", confidence=None,
        created_at=T0, resolved_at=None,
    )
    db.add(relation)
    db.commit()

    resp = client.post(
        f"/conflicts/{relation.id}/resolve",
        json={"resolution": "winner", "winner_id": str(seeded["active_obj"].id)},
    )
    assert resp.status_code == 200

    winner = client.get(f"/context/{seeded['active_obj'].id}").json()
    loser = client.get(f"/context/{other_obj.id}").json()
    assert winner["status"] == "active"
    assert loser["status"] == "superseded"
