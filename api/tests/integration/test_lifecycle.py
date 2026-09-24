"""Dedup (§7.2) + lifecycle/supersession/conflict (§7.3 R1-R4) + lineage (§8), driven
directly against real Postgres (pgvector cosine, int4range, jsonb) with hand-built
ContextObjects — no live LLM call needed; this is the primary proof of the R1-R5 rule
table, per the dispatch: "Dedup/lifecycle/lineage logic must be unit/integration-testable
WITHOUT live LLM calls."
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.range import Range
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import engine
from app.models.orm import ContextObjects, ContextRelations, Entities, People, Sources
from app.pipeline.lifecycle import resolve_object_state
from app.pipeline.lineage import create_lineage_links, downstream_chain, upstream_chain

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def db_session() -> Session:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    tenant_id = uuid.uuid4()
    yield session, tenant_id  # type: ignore[misc]
    session.rollback()
    session.execute(
        text(
            "DELETE FROM context_relations WHERE from_id IN "
            "(SELECT id FROM context_objects WHERE tenant_id = :t)"
        ),
        {"t": tenant_id},
    )
    session.execute(
        text(
            "DELETE FROM context_versions WHERE context_id IN "
            "(SELECT id FROM context_objects WHERE tenant_id = :t)"
        ),
        {"t": tenant_id},
    )
    session.execute(text("DELETE FROM context_objects WHERE tenant_id = :t"), {"t": tenant_id})
    session.execute(text("DELETE FROM sources WHERE tenant_id = :t"), {"t": tenant_id})
    session.execute(text("DELETE FROM people WHERE tenant_id = :t"), {"t": tenant_id})
    session.execute(text("DELETE FROM entities WHERE tenant_id = :t"), {"t": tenant_id})
    session.commit()
    session.close()


def _make_entity(db: Session, tenant_id: uuid.UUID) -> uuid.UUID:
    entity = Entities(id=uuid.uuid4(), tenant_id=tenant_id, name="Acme", slug="acme", kind="customer")
    db.add(entity)
    db.flush()
    return entity.id


def _make_person(db: Session, tenant_id: uuid.UUID, name: str) -> uuid.UUID:
    person = People(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=f"{name.lower()}@ourcompany.com",
        name=name,
        team="product",
        is_external=False,
    )
    db.add(person)
    db.flush()
    return person.id


def _make_source(db: Session, tenant_id: uuid.UUID, stage: str, text_body: str, ts: datetime) -> uuid.UUID:
    source = Sources(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind="slack",
        external_id=str(uuid.uuid4()),
        content_hash=uuid.uuid4().hex,
        stage=stage,
        acl=["*"],
        provenance={"kind": "slack"},
        text=text_body,
        source_ts=ts,
    )
    db.add(source)
    db.flush()
    return source.id


def _make_object(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    type_: str = "requirement",
    subject_key: str = "acme:scim",
    stage: str = "product",
    authority: int = 4,
    confidence: float = 0.9,
    valid_from: datetime,
    stance: str | None = None,
    attributes: dict | None = None,
    actor_person_id: uuid.UUID | None = None,
    embedding: list[float] | None = None,
    content: str = "SCIM stance statement",
) -> ContextObjects:
    attrs = dict(attributes or {})
    if stance is not None:
        attrs["stance"] = stance
    obj = ContextObjects(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        entity_id=entity_id,
        type=type_,
        subject_key=subject_key,
        content=content,
        attributes=attrs,
        actor_label="test",
        actor_role="product",
        actor_person_id=actor_person_id,
        stage=stage,
        authority=authority,
        confidence=confidence,
        status="active",
        valid_from=valid_from,
        source_id=source_id,
        evidence_quote="evidence",
        evidence_span=Range(0, 8),
        embedding=embedding or [0.1] * 384,
        version=1,
        created_at=valid_from,
        updated_at=valid_from,
    )
    db.add(obj)
    db.flush()
    return obj


def _persist(db: Session, tenant_id: uuid.UUID, obj: ContextObjects) -> None:
    """Mirrors process.py's persist step: flush, resolve lifecycle, then leave status as
    whatever resolve_object_state decided."""
    resolve_object_state(db, tenant_id, obj)


def test_r3_supersession_chain_required_deferred_required(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """§7.3 supersession example: SCIM required -> deferred -> required, same PM, same
    stage, authority 4 throughout. Each supersedes the previous. Final: exactly one active
    (required), two superseded, no conflict."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    sam = _make_person(db, tenant_id, "Sam")
    source_id = _make_source(db, tenant_id, "product", "scim thread", T0)

    o1 = _make_object(
        db, tenant_id, entity_id, source_id,
        valid_from=T0, stance="required", actor_person_id=sam, content="SCIM required",
    )
    _persist(db, tenant_id, o1)

    o2 = _make_object(
        db, tenant_id, entity_id, source_id,
        valid_from=T0 + timedelta(days=3), stance="deferred", actor_person_id=sam,
        content="SCIM deferred",
    )
    _persist(db, tenant_id, o2)

    o3 = _make_object(
        db, tenant_id, entity_id, source_id,
        valid_from=T0 + timedelta(days=12), stance="required", actor_person_id=sam,
        content="SCIM back in scope",
    )
    _persist(db, tenant_id, o3)
    db.commit()

    db.refresh(o1)
    db.refresh(o2)
    db.refresh(o3)
    assert o1.status == "superseded"
    assert o2.status == "superseded"
    assert o3.status == "active"

    relations = (
        db.query(ContextRelations)
        .filter(ContextRelations.relation == "supersedes")
        .all()
    )
    assert {(r.from_id, r.to_id) for r in relations} == {(o2.id, o1.id), (o3.id, o2.id)}

    conflicting = (
        db.query(ContextObjects)
        .filter(ContextObjects.tenant_id == tenant_id, ContextObjects.status == "conflicting")
        .count()
    )
    assert conflicting == 0


def test_r4_same_stage_different_author_disagreement_not_silently_superseded(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """§7.3 same-stage disagreement example: PM A 'deferred', PM B 'in the Dec release'
    (i.e. required-ish/incompatible) a day later, no correction language, different
    authors. Both -> conflicting; recency alone never wins."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    pm_a = _make_person(db, tenant_id, "PmA")
    pm_b = _make_person(db, tenant_id, "PmB")
    source_id = _make_source(db, tenant_id, "product", "scim thread", T0)

    o1 = _make_object(
        db, tenant_id, entity_id, source_id,
        valid_from=T0, stance="deferred", actor_person_id=pm_a, content="SCIM deferred to Q1",
    )
    _persist(db, tenant_id, o1)

    o2 = _make_object(
        db, tenant_id, entity_id, source_id,
        valid_from=T0 + timedelta(days=1), stance="required", actor_person_id=pm_b,
        content="SCIM is in the Dec release",
    )
    _persist(db, tenant_id, o2)
    db.commit()

    db.refresh(o1)
    db.refresh(o2)
    assert o1.status == "conflicting"
    assert o2.status == "conflicting"

    contradicts = (
        db.query(ContextRelations)
        .filter(ContextRelations.relation == "contradicts", ContextRelations.from_id == o2.id)
        .all()
    )
    assert len(contradicts) == 1
    assert contradicts[0].to_id == o1.id


def test_r1_cross_stage_incompatible_stance_conflicts(db_session: tuple[Session, uuid.UUID]) -> None:
    """§7.3 R1 / stance matrix: product 'deferred' vs engineering 'in_progress' — different
    stages, incompatible stances -> both conflicting, contradicts relation."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    source_id = _make_source(db, tenant_id, "product", "scim thread", T0)

    product_obj = _make_object(
        db, tenant_id, entity_id, source_id,
        stage="product", valid_from=T0, stance="deferred", authority=4,
        content="SCIM deferred (product)",
    )
    _persist(db, tenant_id, product_obj)

    eng_obj = _make_object(
        db, tenant_id, entity_id, source_id,
        stage="engineering", valid_from=T0 + timedelta(days=2), stance="in_progress",
        authority=4, content="SCIM in progress (engineering)",
    )
    _persist(db, tenant_id, eng_obj)
    db.commit()

    db.refresh(product_obj)
    db.refresh(eng_obj)
    assert product_obj.status == "conflicting"
    assert eng_obj.status == "conflicting"

    contradicts = (
        db.query(ContextRelations)
        .filter(ContextRelations.relation == "contradicts", ContextRelations.from_id == eng_obj.id)
        .all()
    )
    assert len(contradicts) == 1
    assert contradicts[0].created_by == "system:lifecycle:R1"


def test_r2_lower_authority_never_wins_globex_sales_error(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """Globex sales-error scenario: the customer's '500 seats / eu-west-1' statement
    (authority 4) is already active; the AE's '50 seats / US region' restatement
    (authority 2) arrives later and must NOT supersede it -> both conflicting via R2, and
    the customer's higher-authority version remains queryable as `active`... except R2
    marks BOTH conflicting per the rule table, so we assert the customer object keeps its
    higher authority as the reference even once both are `conflicting`."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    source_id = _make_source(db, tenant_id, "sales", "globex thread", T0)

    customer_obj = _make_object(
        db, tenant_id, entity_id, source_id,
        type_="requirement", subject_key="globex:seats", stage="sales",
        authority=4, valid_from=T0,
        attributes={"quantity": 500, "region": "eu-west-1"},
        content="Customer: 500 seats, eu-west-1",
    )
    _persist(db, tenant_id, customer_obj)

    ae_obj = _make_object(
        db, tenant_id, entity_id, source_id,
        type_="requirement", subject_key="globex:seats", stage="sales",
        authority=2, valid_from=T0 + timedelta(days=1),
        attributes={"quantity": 50, "region": "us-east-1"},
        content="AE: 50 seats, US region",
    )
    _persist(db, tenant_id, ae_obj)
    db.commit()

    db.refresh(customer_obj)
    db.refresh(ae_obj)
    assert customer_obj.status == "conflicting"
    assert ae_obj.status == "conflicting"
    assert customer_obj.authority == 4  # still the higher-authority reference
    assert ae_obj.authority == 2

    contradicts = (
        db.query(ContextRelations)
        .filter(ContextRelations.relation == "contradicts", ContextRelations.from_id == ae_obj.id)
        .all()
    )
    assert len(contradicts) == 1
    assert contradicts[0].created_by == "system:lifecycle:R2"


def test_dedup_auto_links_near_identical_compatible_statements(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """§7.2: compatible slots + cosine >= 0.92 -> automatic duplicate_of + supported_by,
    canonical takes max authority."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    source_id = _make_source(db, tenant_id, "product", "dup thread", T0)

    first = _make_object(
        db, tenant_id, entity_id, source_id,
        valid_from=T0, stance="deferred", authority=3,
        embedding=[1.0] + [0.0] * 383, content="SCIM deferred to Q1 (slack)",
    )
    _persist(db, tenant_id, first)

    second = _make_object(
        db, tenant_id, entity_id, source_id,
        valid_from=T0 + timedelta(hours=1), stance="deferred", authority=4,
        embedding=[0.99] + [0.01] * 383, content="SCIM deferred to Q1 (email)",
    )
    _persist(db, tenant_id, second)
    db.commit()

    db.refresh(first)
    db.refresh(second)
    # compatible + auto-dedup threshold -> not a conflict, both stay active
    assert first.status == "active"
    assert second.status == "active"

    dup_relations = db.query(ContextRelations).filter(ContextRelations.relation == "duplicate_of").all()
    assert len(dup_relations) == 1
    assert dup_relations[0].resolved_at is not None  # auto-resolved
    assert dup_relations[0].from_id == first.id  # lower authority is the duplicate
    assert dup_relations[0].to_id == second.id  # higher authority is canonical

    supported = db.query(ContextRelations).filter(ContextRelations.relation == "supported_by").all()
    assert len(supported) == 1
    assert supported[0].from_id == second.id

    assert second.authority == 4  # max across the group


def test_dedup_candidate_band_routes_to_review(db_session: tuple[Session, uuid.UUID]) -> None:
    """§7.2: 0.80-0.92 cosine on compatible slots -> unresolved duplicate_of, no status
    change on either object."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    source_id = _make_source(db, tenant_id, "product", "dup thread", T0)

    first = _make_object(
        db, tenant_id, entity_id, source_id,
        valid_from=T0, stance="deferred",
        embedding=[1.0, 1.0] + [0.0] * 382, content="SCIM deferred (a)",
    )
    _persist(db, tenant_id, first)

    second = _make_object(
        db, tenant_id, entity_id, source_id,
        valid_from=T0 + timedelta(hours=1), stance="deferred",
        embedding=[1.0, 0.3] + [0.0] * 382, content="SCIM deferred (b)",
    )
    _persist(db, tenant_id, second)
    db.commit()

    db.refresh(first)
    db.refresh(second)
    assert first.status == "active"
    assert second.status == "active"

    dup_relations = db.query(ContextRelations).filter(ContextRelations.relation == "duplicate_of").all()
    assert len(dup_relations) == 1
    assert dup_relations[0].resolved_at is None  # pending review


def test_lineage_derived_from_created_across_stages(db_session: tuple[Session, uuid.UUID]) -> None:
    """§8: a downstream-stage object sharing subject_key with an earlier upstream-stage
    object gets a derived_from edge to it, regardless of which was persisted first."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    source_id = _make_source(db, tenant_id, "sales", "lineage thread", T0)

    upstream = _make_object(
        db, tenant_id, entity_id, source_id,
        subject_key="acme:sso", stage="sales", valid_from=T0, content="Customer requires SSO",
    )
    create_lineage_links(db, tenant_id, upstream)

    downstream = _make_object(
        db, tenant_id, entity_id, source_id,
        subject_key="acme:sso", stage="product", valid_from=T0 + timedelta(days=8),
        content="Support SSO for Acme",
    )
    create_lineage_links(db, tenant_id, downstream)
    db.commit()

    edges = (
        db.query(ContextRelations)
        .filter(ContextRelations.relation == "derived_from", ContextRelations.from_id == downstream.id)
        .all()
    )
    assert len(edges) == 1
    assert edges[0].to_id == upstream.id

    up_chain = upstream_chain(db, downstream.id)
    assert len(up_chain) == 1
    assert up_chain[0]["object_id"] == upstream.id

    down_chain = downstream_chain(db, upstream.id)
    assert len(down_chain) == 1
    assert down_chain[0]["object_id"] == downstream.id


def test_lineage_backfills_when_upstream_arrives_after_downstream(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """Processing order doesn't track content chronology (§13.1 jobs are enqueued
    per-connector), so a downstream object persisted first must still get linked once its
    upstream counterpart shows up later."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    source_id = _make_source(db, tenant_id, "sales", "lineage thread", T0)

    downstream = _make_object(
        db, tenant_id, entity_id, source_id,
        subject_key="acme:sso", stage="product", valid_from=T0 + timedelta(days=8),
        content="Support SSO for Acme",
    )
    create_lineage_links(db, tenant_id, downstream)

    upstream = _make_object(
        db, tenant_id, entity_id, source_id,
        subject_key="acme:sso", stage="sales", valid_from=T0, content="Customer requires SSO",
    )
    create_lineage_links(db, tenant_id, upstream)
    db.commit()

    edges = (
        db.query(ContextRelations)
        .filter(ContextRelations.relation == "derived_from", ContextRelations.from_id == downstream.id)
        .all()
    )
    assert len(edges) == 1
    assert edges[0].to_id == upstream.id
