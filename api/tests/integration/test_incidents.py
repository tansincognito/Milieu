"""Incident Context Pack, backed by the first-class `incidents` table (migration 0009) —
needs real Postgres. Every endpoint in api/app/api/incidents.py reads `get_settings()
.tenant_id`, the one fixed tenant the whole API runs against (same pattern as
org_setup.py/dashboard.py/simulation.py) — so unlike test_handoff.py's per-test random
tenant, fixtures here create data under that SAME fixed tenant and isolate by a unique
incident_id/entity-slug prefix instead, mirroring test_capability_resolution.py's approach
to the other tenant-less/single-tenant tables.
"""

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
from app.models.orm import ContextObjects, Entities, Sources

pytestmark = pytest.mark.integration

SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
T0 = datetime(2026, 9, 22, 19, 0, 0, tzinfo=UTC)


def _make_source(db, tenant_id: uuid.UUID, ts: datetime) -> Sources:
    source = Sources(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind="drive",
        external_id=uuid.uuid4().hex,
        content_hash=uuid.uuid4().hex,
        stage="engineering",
        acl=["*"],
        provenance={"kind": "drive"},
        text="incident narrative text",
        source_ts=ts,
    )
    db.add(source)
    db.flush()
    return source


def _make_object(
    db, tenant_id: uuid.UUID, entity_id: uuid.UUID, source: Sources, subject_key: str, extra: dict
) -> ContextObjects:
    obj = ContextObjects(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        entity_id=entity_id,
        type="problem",
        subject_key=subject_key,
        content="incident-related statement",
        attributes={"extra": extra} if extra else {},
        actor_label="On-call",
        actor_role="engineering",
        stage="engineering",
        authority=3,
        confidence=0.9,
        status="active",
        valid_from=source.source_ts,
        source_id=source.id,
        evidence_quote="incident narrative text",
        evidence_span=Range(0, 10),
        embedding=[0.1] * 384,
        version=1,
        created_at=source.source_ts,
        updated_at=source.source_ts,
    )
    db.add(obj)
    db.flush()
    return obj


@pytest.fixture
def seeded():
    tenant_id = uuid.UUID(get_settings().tenant_id)
    db = SessionFactory()
    prefix = uuid.uuid4().hex[:8]
    incident_id = f"TEST-{prefix}"

    entity = Entities(
        id=uuid.uuid4(), tenant_id=tenant_id, name=f"TestCo {prefix}", slug=f"testco-{prefix}", kind="customer"
    )
    db.add(entity)
    db.flush()

    confirmed_source = _make_source(db, tenant_id, T0)
    confirmed_obj = _make_object(
        db, tenant_id, entity.id, confirmed_source, f"{entity.slug}:incident:{incident_id}",
        {"incident_id": incident_id},
    )

    candidate_source = _make_source(db, tenant_id, T0 + timedelta(hours=2))
    candidate_obj = _make_object(db, tenant_id, entity.id, candidate_source, "unknown:incident", {})

    far_source = _make_source(db, tenant_id, T0 + timedelta(days=30))
    far_obj = _make_object(db, tenant_id, entity.id, far_source, "unknown:incident", {})

    db.commit()

    yield {
        "incident_id": incident_id,
        "entity_id": str(entity.id),
        "confirmed_obj_id": str(confirmed_obj.id),
        "candidate_obj_id": str(candidate_obj.id),
        "far_obj_id": str(far_obj.id),
    }

    # Delete by incident_id (join), not by our own 3 object ids: the candidate-discovery
    # window can legitimately sweep in real dev-data objects too (an "Unknown"-entity
    # object is always a valid candidate by design — see incidents.py) if this test's
    # anchor happens to land near one, which it does (2026-09-22 collides with the real
    # INC-2311 postmortem's own Unknown-entity summary object). This only removes the
    # EDGE for our test incidents, never the real object itself.
    db.execute(text("DELETE FROM incident_context_objects WHERE incident_id IN "
                     "(SELECT id FROM incidents WHERE tenant_id = :t AND incident_id LIKE :p)"),
               {"t": tenant_id, "p": f"TEST-{prefix}%"})
    db.execute(text("DELETE FROM incident_entities WHERE entity_id = :e"), {"e": entity.id})
    db.execute(text("DELETE FROM incidents WHERE tenant_id = :t AND incident_id LIKE :p"),
               {"t": tenant_id, "p": f"TEST-{prefix}%"})
    db.execute(text("DELETE FROM context_objects WHERE id IN (:c, :cand, :f)"),
               {"c": confirmed_obj.id, "cand": candidate_obj.id, "f": far_obj.id})
    db.execute(text("DELETE FROM sources WHERE id IN (:s1, :s2, :s3)"),
               {"s1": confirmed_source.id, "s2": candidate_source.id, "s3": far_source.id})
    db.execute(text("DELETE FROM entities WHERE id = :e"), {"e": entity.id})
    db.commit()
    db.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_declare_links_confirmed_object_and_nearby_candidate_not_far_one(client, seeded):
    resp = client.post(
        "/incidents",
        json={
            "incident_id": seeded["incident_id"],
            "title": "Test incident",
            "severity": "P0",
            "entity_ids": [seeded["entity_id"]],
            "anchor_at": T0.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object_count"] == 1  # only the confirmed object counts here

    pack = client.get(f"/incidents/{seeded['incident_id']}").json()
    linked_ids = {e["object"]["id"]: e["linked"] for e in pack["timeline"]}
    assert linked_ids[seeded["confirmed_obj_id"]] is True
    assert linked_ids[seeded["candidate_obj_id"]] is False
    assert seeded["far_obj_id"] not in linked_ids  # 30 days out, outside the 72h window


def test_declare_is_idempotent_by_incident_id(client, seeded):
    body = {
        "incident_id": seeded["incident_id"],
        "title": "Test incident",
        "severity": "P0",
        "entity_ids": [seeded["entity_id"]],
    }
    first = client.post("/incidents", json=body)
    assert first.status_code == 200
    second = client.post("/incidents", json=body)
    assert second.status_code == 409


def test_incident_can_span_multiple_entities(client, seeded):
    """I7 (§19.1 Golden 3): an incident must be able to link to more than one customer."""
    tenant_id = uuid.UUID(get_settings().tenant_id)
    db = SessionFactory()
    second_entity = Entities(
        id=uuid.uuid4(), tenant_id=tenant_id, name="Second Co", slug=f"secondco-{seeded['incident_id']}",
        kind="customer",
    )
    db.add(second_entity)
    db.commit()
    second_entity_id = str(second_entity.id)
    db.close()

    resp = client.post(
        "/incidents",
        json={
            "incident_id": seeded["incident_id"],
            "title": "Multi-customer incident",
            "severity": "P0",
            "entity_ids": [seeded["entity_id"], second_entity_id],
        },
    )
    assert resp.status_code == 200
    assert set(resp.json()["entities"]) == {f"TestCo {seeded['incident_id'].removeprefix('TEST-')}", "Second Co"}

    db = SessionFactory()
    db.execute(text("DELETE FROM incident_entities WHERE entity_id = :e"), {"e": second_entity.id})
    db.execute(text("DELETE FROM entities WHERE id = :e"), {"e": second_entity.id})
    db.commit()
    db.close()


def test_link_object_promotes_candidate_to_confirmed(client, seeded):
    client.post(
        "/incidents",
        json={
            "incident_id": seeded["incident_id"],
            "title": "Test incident",
            "severity": "P1",
            "entity_ids": [seeded["entity_id"]],
            "anchor_at": T0.isoformat(),
        },
    )
    resp = client.post(
        f"/incidents/{seeded['incident_id']}/link",
        json={"context_object_id": seeded["candidate_obj_id"], "linked": True},
    )
    assert resp.status_code == 200
    assert resp.json()["object_count"] == 2  # confirmed + the now-promoted candidate


def test_resolve_sets_status_and_timestamp(client, seeded):
    client.post(
        "/incidents",
        json={
            "incident_id": seeded["incident_id"],
            "title": "Test incident",
            "severity": "P1",
            "entity_ids": [seeded["entity_id"]],
        },
    )
    resp = client.post(f"/incidents/{seeded['incident_id']}/resolve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None


def test_similar_past_incidents_only_returns_resolved_ones_sharing_an_entity(client, seeded):
    first_id = seeded["incident_id"]
    second_id = f"{first_id}-B"

    client.post(
        "/incidents",
        json={"incident_id": first_id, "title": "First", "severity": "P0", "entity_ids": [seeded["entity_id"]]},
    )
    client.post(
        "/incidents",
        json={"incident_id": second_id, "title": "Second", "severity": "P1", "entity_ids": [seeded["entity_id"]]},
    )

    pack_before_resolve = client.get(f"/incidents/{first_id}").json()
    assert pack_before_resolve["similar_past_incidents"] == []  # second incident still open

    client.post(f"/incidents/{second_id}/resolve")

    pack_after_resolve = client.get(f"/incidents/{first_id}").json()
    similar_ids = [s["incident_id"] for s in pack_after_resolve["similar_past_incidents"]]
    assert similar_ids == [second_id]

    db = SessionFactory()
    db.execute(text("DELETE FROM incident_context_objects WHERE incident_id IN "
                     "(SELECT id FROM incidents WHERE incident_id = :s)"), {"s": second_id})
    db.execute(text("DELETE FROM incident_entities WHERE incident_id IN "
                     "(SELECT id FROM incidents WHERE incident_id = :s)"), {"s": second_id})
    db.execute(text("DELETE FROM incidents WHERE incident_id = :s"), {"s": second_id})
    db.commit()
    db.close()
