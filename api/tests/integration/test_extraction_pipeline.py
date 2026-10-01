"""Extraction pipeline test with a test-double LLMClient (no live LLM call — no API key is
available in this environment). Verifies the plumbing: cache -> validate -> evidence-span
check -> entity resolution -> authority -> persist, using canned output for the Acme call.

This is marked integration because it writes through a real Postgres session (context
objects use Postgres-only column types — vector/int4range/jsonb — that don't work on
SQLite), matching the dispatch's "needs compose Postgres" grouping for DB-backed tests.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.connectors.mock_call import MockCallConnector
from app.connectors.mock_drive import MockDriveConnector
from app.core.config import get_settings
from app.core.db import engine
from app.models.orm import ContextObjects, ContextVersions, Entities, EntityAliases, Sources
from app.pipeline.entity_resolution import resolve_entity
from app.pipeline.process import process_extraction_job
from app.pipeline.seed_simulation_sources import import_calls, import_drive
from app.schemas.extraction import (
    ContextAttributes,
    ExtractedContext,
    ExtractionResult,
)

MOCK_DATA_DIR = Path(__file__).resolve().parents[3] / "mock-data"

pytestmark = pytest.mark.integration


class CannedLLMClient:
    """Test double: always returns the same canned ExtractionResult, regardless of prompt."""

    def __init__(self, result: ExtractionResult) -> None:
        self._result = result
        self.calls = 0

    def extract(self, schema, system, content):
        self.calls += 1
        return self._result

    def judge(self, schema, prompt):
        raise NotImplementedError


class FakeEmbeddingClient:
    dimensions = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.01] * 384 for _ in texts]


@pytest.fixture
def db_session() -> Session:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    tenant_id = uuid.uuid4()
    yield session, tenant_id  # type: ignore[misc]
    session.rollback()
    # Clean up everything this test created, scoped by tenant_id. context_relations must go
    # first -- it FKs to context_objects, and a test whose items share a subject_key can
    # trigger a lifecycle relation (§7.3) between them.
    session.execute(text("DELETE FROM context_relations WHERE from_id IN "
                          "(SELECT id FROM context_objects WHERE tenant_id = :t)"),
                     {"t": tenant_id})
    session.execute(text("DELETE FROM context_versions WHERE context_id IN "
                          "(SELECT id FROM context_objects WHERE tenant_id = :t)"),
                     {"t": tenant_id})
    session.execute(text("DELETE FROM context_objects WHERE tenant_id = :t"), {"t": tenant_id})
    session.execute(text("DELETE FROM sources WHERE tenant_id = :t"), {"t": tenant_id})
    session.execute(text("DELETE FROM entity_aliases WHERE tenant_id = :t"), {"t": tenant_id})
    session.execute(text("DELETE FROM entities WHERE tenant_id = :t"), {"t": tenant_id})
    session.execute(
        text("DELETE FROM simulation_seed_sources WHERE tenant_id = :t"), {"t": tenant_id}
    )
    session.commit()
    session.close()


def _canned_acme_call_extraction(call_text: str) -> ExtractionResult:
    saml_quote = "We need SAML through Okta"
    assert saml_quote in call_text
    return ExtractionResult(
        items=[
            ExtractedContext(
                type="requirement",
                subject_capability="sso",
                entity_hint="Acme",
                content="Acme requires SAML SSO through Okta by December 15th.",
                attributes=ContextAttributes(
                    protocol="SAML", idp="Okta", due_date="2026-12-15", due_date_precision="day"
                ),
                actor_label="CUSTOMER",
                evidence_quote=saml_quote,
                confidence=0.95,
            ),
            ExtractedContext(
                type="commitment",
                subject_capability="sso",
                entity_hint="Acme",
                content="Sales committed to a beta by December 1st.",
                attributes=ContextAttributes(due_date="2026-12-01", due_date_precision="day"),
                actor_label="SALES",
                evidence_quote="We can have a beta ready by December 1st",
                confidence=0.9,
            ),
            ExtractedContext(
                type="requirement",
                subject_capability="scim",
                entity_hint="Acme",
                content="Sales speculatively suggested SCIM provisioning.",
                attributes=ContextAttributes(),
                actor_label="SALES",
                evidence_quote="you'd probably want SCIM too",
                confidence=0.7,
                speculative=True,
            ),
        ]
    )


def test_acme_call_extraction_end_to_end(db_session: tuple[Session, uuid.UUID]) -> None:
    db, tenant_id = db_session
    import_calls(db, tenant_id, MOCK_DATA_DIR)
    db.commit()
    connector = MockCallConnector(db, tenant_id)
    raw = next(r for r in connector.fetch(None) if r.external_id == "acme-discovery-2026-10-02")
    normalized = connector.normalize(raw)

    source = Sources(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind=normalized.kind,
        external_id=normalized.external_id,
        version=1,
        content_hash=normalized.content_hash,
        stage=normalized.stage,
        acl=normalized.acl,
        provenance=normalized.provenance.model_dump(mode="json"),
        text=normalized.text,
        source_ts=normalized.source_ts,
    )
    db.add(source)
    db.commit()

    llm = CannedLLMClient(_canned_acme_call_extraction(normalized.text))
    embedder = FakeEmbeddingClient()
    fake_redis = MagicMock()
    fake_redis.get.return_value = None  # force a cache miss so the canned LLM is called
    settings = get_settings()

    result = process_extraction_job(
        db, fake_redis, llm, embedder, settings, tenant_id, source.id
    )

    assert result.created == 3
    assert result.rejected == 0
    assert llm.calls == 1
    fake_redis.set.assert_called_once()

    objects = db.query(ContextObjects).filter(ContextObjects.source_id == source.id).all()
    assert len(objects) == 3

    requirement = next(o for o in objects if o.type == "requirement" and not o.confidence < 0.8)
    assert requirement.authority == 4  # customer statement
    assert requirement.actor_role == "customer"
    assert requirement.attributes["protocol"] == "SAML"
    assert requirement.attributes["idp"] == "Okta"
    assert requirement.evidence_quote in normalized.text
    assert normalized.text[requirement.evidence_span.lower : requirement.evidence_span.upper] == (
        requirement.evidence_quote
    )

    commitment = next(o for o in objects if o.type == "commitment")
    assert commitment.authority == 3  # sales owns its own commitment
    assert commitment.actor_role == "sales"

    speculative = next(o for o in objects if o.subject_key.endswith(":scim"))
    assert speculative.authority == 1  # sales speculation, capped
    assert speculative.actor_role == "sales"

    entity = db.get(Entities, requirement.entity_id)
    assert entity is not None
    assert entity.slug == "acme"

    versions = db.query(ContextVersions).filter(
        ContextVersions.context_id.in_([o.id for o in objects])
    ).all()
    assert len(versions) == 3
    assert all(v.version == 1 for v in versions)


def test_entity_resolution_reuses_seeded_acme_alias(db_session: tuple[Session, uuid.UUID]) -> None:
    db, tenant_id = db_session
    entity = Entities(id=uuid.uuid4(), tenant_id=tenant_id, name="Acme Corp", slug="acme", kind="customer")
    db.add(entity)
    db.flush()
    db.add(
        EntityAliases(
            id=uuid.uuid4(), entity_id=entity.id, tenant_id=tenant_id, alias_normalized="acme"
        )
    )
    db.commit()

    resolved_id = resolve_entity(db, tenant_id, "Acme Corp")
    db.commit()

    assert resolved_id == entity.id
    # No duplicate entity was created for a known alias.
    assert db.query(Entities).filter(Entities.tenant_id == tenant_id).count() == 1


def test_drive_section_with_markdown_and_em_dash_quotes_is_not_rejected(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """Regression test for the 2026-09-28 eval run: `sales/acme-requirements.md`'s
    "Requirements" section produced 0 context objects. An LLM reading the rendered markdown
    naturally drops the `**bold**` markers and reads the em-dash as a hyphen when it quotes
    the text, so `evidence_quote` was no longer a literal substring of `source.text` and
    every item in the section was silently rejected by the old bare-`str.find` evidence-span
    check. This uses the real mock document text and canned quotes shaped exactly like that
    failure pattern to prove the fix end to end (extraction accepted, §4.3 invariant holds
    on the persisted, original-text span)."""
    db, tenant_id = db_session
    import_drive(db, tenant_id, MOCK_DATA_DIR)
    db.commit()
    connector = MockDriveConnector(db, tenant_id)
    raw = next(
        r
        for r in connector.fetch(None)
        if r.payload["path"] == "sales/acme-requirements.md"
        and r.payload["heading"] == "Requirements"
    )
    normalized = connector.normalize(raw)
    assert "**December 15, 2026**" in normalized.text
    assert "—" in normalized.text  # em-dash, read by the model as a hyphen

    source = Sources(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind=normalized.kind,
        external_id=normalized.external_id,
        version=1,
        content_hash=normalized.content_hash,
        stage=normalized.stage,
        acl=normalized.acl,
        provenance=normalized.provenance.model_dump(mode="json"),
        text=normalized.text,
        source_ts=normalized.source_ts,
    )
    db.add(source)
    db.commit()

    canned = ExtractionResult(
        items=[
            ExtractedContext(
                type="requirement",
                subject_capability="sso",
                entity_hint="Acme",
                content="Acme requires SAML SSO through Okta by December 15, 2026.",
                attributes=ContextAttributes(
                    protocol="SAML", idp="Okta", due_date="2026-12-15", due_date_precision="day"
                ),
                actor_label="SALES",
                # No asterisks -- the model "saw through" the bold markdown.
                evidence_quote="Target date: December 15, 2026",
                confidence=0.9,
            ),
            ExtractedContext(
                type="requirement",
                subject_capability="sso",
                entity_hint="Acme",
                content="SAML/Okta is a hard requirement for Acme, not a nice-to-have.",
                attributes=ContextAttributes(),
                actor_label="SALES",
                # Hyphen, not the source's real em-dash.
                evidence_quote=(
                    "a hard requirement, not a preference - their InfoSec team already runs Okta"
                ),
                confidence=0.85,
            ),
        ]
    )
    llm = CannedLLMClient(canned)
    embedder = FakeEmbeddingClient()
    fake_redis = MagicMock()
    fake_redis.get.return_value = None
    settings = get_settings()

    result = process_extraction_job(db, fake_redis, llm, embedder, settings, tenant_id, source.id)

    # Before the fix: both items failed the bare-substring evidence-span check and were
    # silently discarded -- created == 0 with no way to tell that apart from "the LLM
    # returned nothing". After the fix: both are accepted, and rejected is observable.
    assert result.created == 2
    assert result.rejected == 0

    objects = db.query(ContextObjects).filter(ContextObjects.source_id == source.id).all()
    assert len(objects) == 2
    for obj in objects:
        # §4.3 invariant holds byte-for-byte on the persisted, original-text span, even
        # though the LLM's own evidence_quote (normalized markdown/em-dash) never appeared
        # literally in source.text.
        span_text = normalized.text[obj.evidence_span.lower : obj.evidence_span.upper]
        assert span_text == obj.evidence_quote
