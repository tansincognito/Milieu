"""Integration tests for the dashboard-only API additions: GET /sources/{id},
GET /conflicts, GET /gaps, POST /gaps/{id}/review, and EntityOut.open_gaps
(none of these existed before this build — see web/README.md and the commit message
for why each was added)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from psycopg.types.range import Range
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.db import engine
from app.main import app
from app.models.orm import (
    ContextContracts,
    ContextGaps,
    ContextObjects,
    ContextRelations,
    Entities,
    HandoffValidations,
    Sources,
)

pytestmark = pytest.mark.integration

SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def seeded() -> dict:
    db = SessionFactory()
    tenant_id = uuid.uuid4()
    entity = Entities(
        id=uuid.uuid4(), tenant_id=tenant_id, name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:6]}", kind="customer",
    )
    db.add(entity)
    db.flush()

    source = Sources(
        id=uuid.uuid4(), tenant_id=tenant_id, kind="drive", external_id=uuid.uuid4().hex,
        content_hash=uuid.uuid4().hex, stage="sales", acl=["*"], provenance={"kind": "drive"},
        text="Acme needs SSO via SAML", source_ts=T0,
    )
    private_source = Sources(
        id=uuid.uuid4(), tenant_id=tenant_id, kind="email", external_id=uuid.uuid4().hex,
        content_hash=uuid.uuid4().hex, stage="sales", acl=["team:sales-private"],
        provenance={"kind": "email"}, text="private thread body", source_ts=T0,
    )
    db.add_all([source, private_source])
    db.flush()

    obj_a = ContextObjects(
        id=uuid.uuid4(), tenant_id=tenant_id, entity_id=entity.id, type="requirement",
        subject_key="acme:sso", content="Acme requires SSO", attributes={"protocol": "SAML"},
        actor_label="Dana Kim", actor_role="customer", stage="sales", authority=4,
        confidence=0.95, status="conflicting", valid_from=T0, source_id=source.id,
        evidence_quote="Acme needs SSO", evidence_span=Range(0, 14), embedding=[0.1] * 384,
        version=1, created_at=T0, updated_at=T0,
    )
    obj_b = ContextObjects(
        id=uuid.uuid4(), tenant_id=tenant_id, entity_id=entity.id, type="requirement",
        subject_key="acme:sso", content="SSO not required", attributes={"protocol": "OIDC"},
        actor_label="Someone", actor_role="sales", stage="sales", authority=2, confidence=0.8,
        status="conflicting", valid_from=T0 + timedelta(days=1), source_id=source.id,
        evidence_quote="via SAML", evidence_span=Range(15, 24), embedding=[0.2] * 384,
        version=1, created_at=T0, updated_at=T0,
    )
    db.add_all([obj_a, obj_b])
    db.flush()

    relation = ContextRelations(
        id=uuid.uuid4(), from_id=obj_a.id, to_id=obj_b.id, relation="contradicts",
        created_by="system:lifecycle:R4", confidence=None, created_at=T0, resolved_at=None,
    )
    db.add(relation)

    contract = ContextContracts(
        id=f"test_contract_{uuid.uuid4().hex[:8]}", from_stage="sales", to_stage="product",
        spec={}, version=1,
    )
    db.add(contract)
    db.flush()

    validation = HandoffValidations(
        id=uuid.uuid4(), entity_id=entity.id, contract_id=contract.id, as_of=T0,
        input_hash="x", summary={},
    )
    db.add(validation)
    db.flush()

    gap = ContextGaps(
        id=uuid.uuid4(), validation_id=validation.id, contract_field="requirements",
        upstream_id=obj_a.id, downstream_id=None, slot="protocol", outcome="object_missing",
        severity=3.0, severity_band="high", inherited=False,
        explanation="test gap", status="open",
    )
    db.add(gap)
    db.commit()

    yield {
        "db": db, "tenant_id": tenant_id, "entity": entity, "source": source,
        "private_source": private_source, "obj_a": obj_a, "obj_b": obj_b,
        "relation": relation, "gap": gap,
    }

    db.rollback()
    db.execute(text("DELETE FROM reviews WHERE target_id IN "
                     "(SELECT id FROM context_objects WHERE tenant_id = :t) "
                     "OR target_id = :gap_id"), {"t": tenant_id, "gap_id": gap.id})
    db.execute(text("DELETE FROM context_gaps WHERE id = :g"), {"g": gap.id})
    db.execute(text("DELETE FROM handoff_validations WHERE id = :v"), {"v": validation.id})
    db.execute(text("DELETE FROM context_contracts WHERE id = :c"), {"c": contract.id})
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


def test_get_source_returns_text_for_public_acl(client: TestClient, seeded: dict) -> None:
    resp = client.get(f"/sources/{seeded['source'].id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Acme needs SSO via SAML"
    assert body["provenance"] is not None


def test_get_source_redacts_text_without_matching_principal(client: TestClient, seeded: dict) -> None:
    resp = client.get(f"/sources/{seeded['private_source'].id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] is None
    assert body["provenance"] is None


def test_get_source_404(client: TestClient) -> None:
    resp = client.get(f"/sources/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_list_conflicts_pairs_relation_with_objects(client: TestClient, seeded: dict) -> None:
    resp = client.get("/conflicts", params={"entity": seeded["entity"].slug})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    pair = body[0]
    assert pair["relation_id"] == str(seeded["relation"].id)
    ids = {pair["from_object"]["id"], pair["to_object"]["id"]}
    assert ids == {str(seeded["obj_a"].id), str(seeded["obj_b"].id)}


def test_conflicts_resolve_marks_relation_and_excludes_from_list(client: TestClient, seeded: dict) -> None:
    resp = client.post(
        f"/conflicts/{seeded['relation'].id}/resolve",
        json={"resolution": "winner", "winner_id": str(seeded["obj_a"].id)},
    )
    assert resp.status_code == 200

    resp = client.get("/conflicts", params={"entity": seeded["entity"].slug})
    assert resp.json() == []


def test_list_gaps_filters_by_entity_and_status(client: TestClient, seeded: dict) -> None:
    resp = client.get("/gaps", params={"entity": seeded["entity"].slug})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(seeded["gap"].id)
    assert body[0]["upstream"]["id"] == str(seeded["obj_a"].id)

    resp = client.get("/gaps", params={"entity": seeded["entity"].slug, "status": "resolved"})
    assert resp.json() == []


def test_gap_review_confirm_marks_resolved(client: TestClient, seeded: dict) -> None:
    resp = client.post(f"/gaps/{seeded['gap'].id}/review", json={"action": "confirm"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"

    resp = client.get("/gaps", params={"entity": seeded["entity"].slug})
    assert resp.json() == []


def test_gap_review_404(client: TestClient) -> None:
    resp = client.post(f"/gaps/{uuid.uuid4()}/review", json={"action": "ignore"})
    assert resp.status_code == 404


def test_entities_list_reports_open_gaps(client: TestClient) -> None:
    # GET /entities is scoped to the real default tenant (no auth/session layer yet), so
    # this seeds there directly with explicit-id cleanup -- same reasoning as
    # test_context_api.py::test_entities_list_and_entity_context.
    db = SessionFactory()
    tenant_id = uuid.UUID(get_settings().tenant_id)
    entity = Entities(
        id=uuid.uuid4(), tenant_id=tenant_id, name="Test Entity",
        slug=f"test-entity-{uuid.uuid4().hex[:8]}", kind="customer",
    )
    db.add(entity)
    db.flush()
    source = Sources(
        id=uuid.uuid4(), tenant_id=tenant_id, kind="drive", external_id=uuid.uuid4().hex,
        content_hash=uuid.uuid4().hex, stage="sales", acl=["*"], provenance={"kind": "drive"},
        text="Acme needs SSO via SAML", source_ts=T0,
    )
    db.add(source)
    db.flush()
    obj_a = ContextObjects(
        id=uuid.uuid4(), tenant_id=tenant_id, entity_id=entity.id, type="requirement",
        subject_key="test:sso", content="Acme requires SSO", attributes={"protocol": "SAML"},
        actor_label="Dana Kim", actor_role="customer", stage="sales", authority=4,
        confidence=0.95, status="conflicting", valid_from=T0, source_id=source.id,
        evidence_quote="Acme needs SSO", evidence_span=Range(0, 14), embedding=[0.1] * 384,
        version=1, created_at=T0, updated_at=T0,
    )
    obj_b = ContextObjects(
        id=uuid.uuid4(), tenant_id=tenant_id, entity_id=entity.id, type="requirement",
        subject_key="test:sso", content="SSO not required", attributes={"protocol": "OIDC"},
        actor_label="Someone", actor_role="sales", stage="sales", authority=2, confidence=0.8,
        status="conflicting", valid_from=T0 + timedelta(days=1), source_id=source.id,
        evidence_quote="via SAML", evidence_span=Range(15, 24), embedding=[0.2] * 384,
        version=1, created_at=T0, updated_at=T0,
    )
    db.add_all([obj_a, obj_b])
    db.flush()
    contract = ContextContracts(
        id=f"test_contract_{uuid.uuid4().hex[:8]}", from_stage="sales", to_stage="product",
        spec={}, version=1,
    )
    db.add(contract)
    db.flush()
    validation = HandoffValidations(
        id=uuid.uuid4(), entity_id=entity.id, contract_id=contract.id, as_of=T0,
        input_hash="x", summary={},
    )
    db.add(validation)
    db.flush()
    gap = ContextGaps(
        id=uuid.uuid4(), validation_id=validation.id, contract_field="requirements",
        upstream_id=obj_a.id, downstream_id=None, slot="protocol", outcome="object_missing",
        severity=3.0, severity_band="high", inherited=False,
        explanation="test gap", status="open",
    )
    db.add(gap)
    db.commit()

    try:
        resp = client.get("/entities")
        assert resp.status_code == 200
        row = next(r for r in resp.json() if r["slug"] == entity.slug)
        assert row["open_gaps"] == 1
        assert row["open_conflicts"] == 2
    finally:
        db.rollback()
        db.execute(text("DELETE FROM context_gaps WHERE id = :id"), {"id": gap.id})
        db.execute(text("DELETE FROM handoff_validations WHERE id = :id"), {"id": validation.id})
        db.execute(text("DELETE FROM context_contracts WHERE id = :id"), {"id": contract.id})
        db.execute(
            text("DELETE FROM context_objects WHERE id = ANY(:ids)"),
            {"ids": [obj_a.id, obj_b.id]},
        )
        db.execute(text("DELETE FROM sources WHERE id = :id"), {"id": source.id})
        db.execute(text("DELETE FROM entities WHERE id = :id"), {"id": entity.id})
        db.commit()
        db.close()
