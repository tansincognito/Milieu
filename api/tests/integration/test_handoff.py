"""Handoff validator (§10) + Context Contracts (§9), driven directly against real Postgres
with hand-built ContextObjects — no live LLM call needed, following the pattern in
`test_lifecycle.py`: "Dedup/lifecycle/lineage logic must be unit/integration-testable
WITHOUT live LLM calls." This is the equivalent proof for §10's seven outcome kinds,
§10.2 severity, and §10.4 chain attribution (origin vs. inherited), pinned against the
three §19.1 golden scenarios (Acme SSO, Globex onboarding, INC-2311).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from psycopg.types.range import Range
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import engine
from app.models.orm import ContextGaps, ContextObjects, Entities, People, Sources
from app.pipeline.contracts import load_contracts
from app.pipeline.handoff import validate_handoff
from app.pipeline.lifecycle import resolve_object_state
from app.pipeline.lineage import create_lineage_links

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 2, 0, 14, 32, tzinfo=UTC)
_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


@pytest.fixture(scope="module", autouse=True)
def _contracts_seeded() -> None:
    """Loads the real /contracts YAML into context_contracts once per module — proves the
    loader end to end rather than hand-inserting rows. context_contracts has no tenant_id
    (§16), so this is a shared, idempotent seed, same as capability_vocab."""
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = session_factory()
    load_contracts(db, _CONTRACTS_DIR)
    db.close()


@pytest.fixture
def db_session() -> Session:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    tenant_id = uuid.uuid4()
    yield session, tenant_id  # type: ignore[misc]
    session.rollback()
    session.execute(
        text(
            "DELETE FROM context_gaps WHERE validation_id IN "
            "(SELECT id FROM handoff_validations WHERE entity_id IN "
            "(SELECT id FROM entities WHERE tenant_id = :t))"
        ),
        {"t": tenant_id},
    )
    session.execute(
        text(
            "DELETE FROM handoff_validations WHERE entity_id IN "
            "(SELECT id FROM entities WHERE tenant_id = :t)"
        ),
        {"t": tenant_id},
    )
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


def _make_entity(db: Session, tenant_id: uuid.UUID, name: str = "Acme", slug: str = "acme") -> uuid.UUID:
    entity = Entities(id=uuid.uuid4(), tenant_id=tenant_id, name=name, slug=slug, kind="customer")
    db.add(entity)
    db.flush()
    return entity.id


def _make_person(db: Session, tenant_id: uuid.UUID, name: str, team: str) -> uuid.UUID:
    person = People(
        id=uuid.uuid4(), tenant_id=tenant_id, email=f"{name.lower()}@ourcompany.com",
        name=name, team=team, is_external=False,
    )
    db.add(person)
    db.flush()
    return person.id


def _make_source(
    db: Session, tenant_id: uuid.UUID, stage: str, text_body: str, ts: datetime, kind: str = "call"
) -> uuid.UUID:
    source = Sources(
        id=uuid.uuid4(), tenant_id=tenant_id, kind=kind, external_id=str(uuid.uuid4()),
        content_hash=uuid.uuid4().hex, stage=stage, acl=["*"], provenance={"kind": kind},
        text=text_body, source_ts=ts,
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
    subject_key: str,
    stage: str,
    authority: int,
    actor_role: str = "sales",
    confidence: float = 0.9,
    status: str = "active",
    valid_from: datetime,
    attributes: dict | None = None,
    actor_person_id: uuid.UUID | None = None,
    content: str = "statement",
    evidence_quote: str = "evidence",
    evidence_span: tuple[int, int] = (0, 8),
) -> ContextObjects:
    obj = ContextObjects(
        id=uuid.uuid4(), tenant_id=tenant_id, entity_id=entity_id, type=type_,
        subject_key=subject_key, content=content, attributes=dict(attributes or {}),
        actor_label="test", actor_role=actor_role, actor_person_id=actor_person_id,
        stage=stage, authority=authority, confidence=confidence, status=status,
        valid_from=valid_from, source_id=source_id, evidence_quote=evidence_quote,
        evidence_span=Range(*evidence_span), embedding=[0.1] * 384, version=1,
        created_at=valid_from, updated_at=valid_from,
    )
    db.add(obj)
    db.flush()
    return obj


def _link(db: Session, tenant_id: uuid.UUID, obj: ContextObjects) -> None:
    create_lineage_links(db, tenant_id, obj)
    db.flush()  # session is autoflush=False; the validator's own queries need to see this


def _gaps_for(db: Session, validation_id: uuid.UUID) -> list[ContextGaps]:
    return db.query(ContextGaps).filter(ContextGaps.validation_id == validation_id).all()


def _by(gaps: list[ContextGaps], **kwargs: object) -> ContextGaps:
    for g in gaps:
        if all(getattr(g, k) == v for k, v in kwargs.items()):
            return g
    raise AssertionError(f"no gap matching {kwargs} in {[(g.contract_field, g.slot, g.outcome) for g in gaps]}")


# ---------------------------------------------------------------------------
# Golden 1: Acme SSO (sales -> product -> engineering) — §19.1 G1-G9
# ---------------------------------------------------------------------------


def test_golden1_sales_to_product_origin_losses_g1_g2_g3(db_session: tuple[Session, uuid.UUID]) -> None:
    """G1 protocol generalized, G2 idp missing, G3 due_date generalized — all `origin`
    (sales is the first stage in the chain, so nothing to walk up to)."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    call_text = 'CUSTOMER: "We need SAML through Okta by Dec 15."'
    call_source = _make_source(db, tenant_id, "sales", call_text, T0, kind="call")
    prd_source = _make_source(
        db, tenant_id, "product", "## Requirements\nSupport SSO. Target: December.", T0 + timedelta(days=2)
    )

    quote = "We need SAML through Okta by Dec 15."
    span = (call_text.index(quote), call_text.index(quote) + len(quote))
    sales_obj = _make_object(
        db, tenant_id, entity_id, call_source,
        subject_key="acme:sso", stage="sales", authority=4, actor_role="customer",
        valid_from=T0, content="Customer requires SAML through Okta by Dec 15",
        attributes={
            "protocol": "SAML", "idp": "Okta", "priority": "P0",
            "due_date": date(2026, 12, 15).isoformat(), "due_date_precision": "day",
        },
        evidence_quote=quote, evidence_span=span,
    )
    _link(db, tenant_id, sales_obj)

    product_obj = _make_object(
        db, tenant_id, entity_id, prd_source,
        subject_key="acme:sso", stage="product", authority=3, actor_role="product",
        valid_from=T0 + timedelta(days=2), content="Support SSO",
        attributes={
            "priority": "P0", "due_date": date(2026, 12, 1).isoformat(), "due_date_precision": "month",
        },
    )
    _link(db, tenant_id, product_obj)

    validation = validate_handoff(db, tenant_id, entity_id, "sales_to_product")
    gaps = _gaps_for(db, validation.id)

    protocol_gap = _by(gaps, contract_field="requirements", slot="protocol")
    assert protocol_gap.outcome == "generalized"
    assert protocol_gap.inherited is False
    assert protocol_gap.upstream_conflict is False
    assert float(protocol_gap.severity) == 1.8
    assert protocol_gap.severity_band == "medium"
    assert protocol_gap.upstream_id == sales_obj.id

    idp_gap = _by(gaps, contract_field="requirements", slot="idp")
    assert idp_gap.outcome == "missing"
    assert idp_gap.inherited is False
    assert float(idp_gap.severity) == 2.7
    assert idp_gap.severity_band == "high"

    due_date_gap = _by(gaps, contract_field="requirements", slot="due_date")
    assert due_date_gap.outcome == "generalized"
    assert due_date_gap.inherited is False
    assert float(due_date_gap.severity) == 1.8

    # priority was preserved (P0 == P0) -> no gap for it.
    assert not [g for g in gaps if g.slot == "priority"]

    # G8: the upstream evidence resolves to a real substring of the call transcript.
    upstream_obj = db.get(ContextObjects, protocol_gap.upstream_id)
    assert upstream_obj is not None
    source = db.get(Sources, upstream_obj.source_id)
    assert source is not None
    lo, hi = upstream_obj.evidence_span.lower, upstream_obj.evidence_span.upper
    assert source.text[lo:hi] == upstream_obj.evidence_quote == quote

    # the "evidence" present-check field: product's SSO object has a derived_from link.
    assert not [g for g in gaps if g.contract_field == "evidence"]


def test_golden1_product_to_engineering_inherited_and_origin_g4_g5_g6(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """G4 protocol generalized (inherited), G5 idp missing (inherited), G6 due_date missing
    (origin) — due_date was still present (if generalized) at product, so its disappearance
    at engineering is a *new* loss, unlike protocol/idp which product never carried."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    call_source = _make_source(db, tenant_id, "sales", "SAML via Okta needed", T0, kind="call")
    prd_source = _make_source(db, tenant_id, "product", "Support SSO", T0 + timedelta(days=2))
    design_source = _make_source(
        db, tenant_id, "engineering", "Implement SSO", T0 + timedelta(days=5)
    )

    sales_obj = _make_object(
        db, tenant_id, entity_id, call_source,
        subject_key="acme:sso", stage="sales", authority=4, actor_role="customer",
        valid_from=T0, content="Customer requires SAML through Okta",
        attributes={"protocol": "SAML", "idp": "Okta", "priority": "P0"},
    )
    _link(db, tenant_id, sales_obj)

    product_obj = _make_object(
        db, tenant_id, entity_id, prd_source,
        subject_key="acme:sso", stage="product", authority=3, actor_role="product",
        valid_from=T0 + timedelta(days=2), content="Support SSO",
        attributes={
            "priority": "P0", "due_date": date(2026, 12, 1).isoformat(), "due_date_precision": "month",
        },
    )
    _link(db, tenant_id, product_obj)

    engineering_obj = _make_object(
        db, tenant_id, entity_id, design_source,
        subject_key="acme:sso", stage="engineering", authority=3, actor_role="engineering",
        valid_from=T0 + timedelta(days=5), content="Implement SSO",
        attributes={"priority": "P0"},
    )
    _link(db, tenant_id, engineering_obj)

    validation = validate_handoff(db, tenant_id, entity_id, "product_to_engineering")
    gaps = _gaps_for(db, validation.id)

    protocol_gap = _by(gaps, contract_field="requirements", slot="protocol")
    assert protocol_gap.outcome == "generalized"
    assert protocol_gap.inherited is True  # G4
    assert protocol_gap.upstream_id == sales_obj.id  # walked up past product's own None

    idp_gap = _by(gaps, contract_field="requirements", slot="idp")
    assert idp_gap.outcome == "missing"
    assert idp_gap.inherited is True  # G5
    assert idp_gap.upstream_id == sales_obj.id

    due_date_gap = _by(gaps, contract_field="requirements", slot="due_date")
    assert due_date_gap.outcome == "missing"
    assert due_date_gap.inherited is False  # G6 — origin: lost right at this handoff
    assert due_date_gap.upstream_id == product_obj.id
    assert float(due_date_gap.severity) == 2.16
    assert due_date_gap.severity_band == "high"

    # present-check: engineering's SSO object has no acceptance_criteria.
    acceptance_gap = _by(gaps, contract_field="acceptance_criteria", slot="acceptance_criteria")
    assert acceptance_gap.outcome == "missing"
    assert acceptance_gap.upstream_id is None
    assert acceptance_gap.downstream_id == engineering_obj.id

    assert len(gaps) == 4


def test_golden1_lineage_derived_from_and_no_gap_on_paraphrase_g7_g9(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """G7: the product SSO object's derived_from link points at the *customer's* call
    requirement, not an unrelated sales requirement. G9: a Slack paraphrase whose slots were
    extracted correctly produces no gap (preserved, despite different wording in `content`).
    """
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    call_source = _make_source(db, tenant_id, "sales", "SAML via Okta", T0, kind="call")
    unrelated_source = _make_source(db, tenant_id, "sales", "audit logs 1y", T0, kind="drive")
    slack_source = _make_source(
        db, tenant_id, "product", "SAML-based SSO with Okta", T0 + timedelta(days=1), kind="slack"
    )

    customer_req = _make_object(
        db, tenant_id, entity_id, call_source,
        subject_key="acme:sso", stage="sales", authority=4, actor_role="customer",
        valid_from=T0, content="Customer requires SAML through Okta",
        attributes={"protocol": "SAML", "idp": "Okta"},
    )
    _link(db, tenant_id, customer_req)

    unrelated_req = _make_object(
        db, tenant_id, entity_id, unrelated_source,
        subject_key="acme:audit_logs", stage="sales", authority=4, actor_role="customer",
        valid_from=T0, content="Customer requires 1y audit log retention",
        attributes={},
    )
    _link(db, tenant_id, unrelated_req)

    # G9: a differently-worded product-stage paraphrase that nonetheless captured the same
    # slot values ("SAML-based SSO with Okta") must not be flagged.
    paraphrase = _make_object(
        db, tenant_id, entity_id, slack_source,
        subject_key="acme:sso", stage="product", authority=3, actor_role="product",
        valid_from=T0 + timedelta(days=1), content="SAML-based SSO with Okta",
        attributes={"protocol": "SAML", "idp": "Okta"},
    )
    _link(db, tenant_id, paraphrase)

    # G7: derived_from points to the customer's SSO requirement, not the unrelated one.
    from app.models.orm import ContextRelations

    edges = (
        db.query(ContextRelations)
        .filter(ContextRelations.relation == "derived_from", ContextRelations.from_id == paraphrase.id)
        .all()
    )
    assert {e.to_id for e in edges} == {customer_req.id}

    validation = validate_handoff(db, tenant_id, entity_id, "sales_to_product")
    gaps = _gaps_for(db, validation.id)
    sso_gaps = [g for g in gaps if g.upstream_id == customer_req.id]
    assert sso_gaps == []  # G9: no gap for the correctly-captured paraphrase


# ---------------------------------------------------------------------------
# Golden 2: Globex onboarding with a sales error — §19.1 O1-O6
# ---------------------------------------------------------------------------


def test_golden2_globex_within_sales_conflict_and_handoff_contradictions(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """O1/O2: customer vs. AE disagreement within sales -> conflicting (R2), never
    superseded by the lower-authority AE note. O3/O4: the sales->CS handoff references the
    *customer's* value (not the AE's) and flags contradicted + upstream_conflict. O5:
    Salesforce integration entirely dropped -> object_missing. O6: go-live date generalized
    day -> quarter."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id, name="Globex", slug="globex")
    call_source = _make_source(db, tenant_id, "sales", "customer call", T0, kind="call")
    email_source = _make_source(db, tenant_id, "sales", "AE handoff email", T0 + timedelta(days=1), kind="email")
    plan_source = _make_source(
        db, tenant_id, "customer_success", "onboarding plan", T0 + timedelta(days=3)
    )

    # globex:data_residency — customer (EU, authority 4) vs AE (US, authority 2)
    customer_region = _make_object(
        db, tenant_id, entity_id, call_source,
        subject_key="globex:data_residency", stage="sales", authority=4, actor_role="customer",
        valid_from=T0, content="Customer requires EU data residency",
        attributes={"region": "eu-west-1"},
    )
    resolve_object_state(db, tenant_id, customer_region)
    ae_region = _make_object(
        db, tenant_id, entity_id, email_source,
        subject_key="globex:data_residency", stage="sales", authority=2, actor_role="sales",
        valid_from=T0 + timedelta(days=1), content="AE: US region fine",
        attributes={"region": "us-east-1"},
    )
    resolve_object_state(db, tenant_id, ae_region)

    # globex:seats — customer (500, authority 4) vs AE (50, authority 2)
    customer_seats = _make_object(
        db, tenant_id, entity_id, call_source,
        subject_key="globex:seats", stage="sales", authority=4, actor_role="customer",
        valid_from=T0, content="Customer requires 500 seats",
        attributes={"quantity": 500},
    )
    resolve_object_state(db, tenant_id, customer_seats)
    ae_seats = _make_object(
        db, tenant_id, entity_id, email_source,
        subject_key="globex:seats", stage="sales", authority=2, actor_role="sales",
        valid_from=T0 + timedelta(days=1), content="AE: 50 seats",
        attributes={"quantity": 50},
    )
    resolve_object_state(db, tenant_id, ae_seats)
    db.commit()

    # O1/O2: within-sales — both conflicting, never silently superseded.
    for obj in (customer_region, ae_region, customer_seats, ae_seats):
        db.refresh(obj)
    assert customer_region.status == "conflicting" and ae_region.status == "conflicting"
    assert customer_seats.status == "conflicting" and ae_seats.status == "conflicting"

    # globex:integration — customer only, no CS counterpart at all.
    integration_req = _make_object(
        db, tenant_id, entity_id, call_source,
        subject_key="globex:integration", stage="sales", authority=4, actor_role="customer",
        valid_from=T0, content="Customer requires Salesforce integration",
        attributes={"integrations": ["Salesforce"]},
    )

    # globex:onboarding — customer go-live Jan 10 (day precision).
    onboarding_req = _make_object(
        db, tenant_id, entity_id, call_source,
        subject_key="globex:onboarding", stage="sales", authority=4, actor_role="customer",
        valid_from=T0, content="Customer requires go-live by Jan 10",
        attributes={"due_date": date(2027, 1, 10).isoformat(), "due_date_precision": "day"},
    )

    # Downstream CS plan: us-east-1 / 50 seats / Q1 go-live, no Salesforce mention.
    cs_region = _make_object(
        db, tenant_id, entity_id, plan_source,
        subject_key="globex:data_residency", stage="customer_success", authority=3,
        actor_role="customer_success", valid_from=T0 + timedelta(days=3),
        content="Provisioning us-east-1 tenant", attributes={"region": "us-east-1"},
    )
    cs_seats = _make_object(
        db, tenant_id, entity_id, plan_source,
        subject_key="globex:seats", stage="customer_success", authority=3,
        actor_role="customer_success", valid_from=T0 + timedelta(days=3),
        content="50 seats provisioned", attributes={"quantity": 50},
    )
    cs_onboarding = _make_object(
        db, tenant_id, entity_id, plan_source,
        subject_key="globex:onboarding", stage="customer_success", authority=3,
        actor_role="customer_success", valid_from=T0 + timedelta(days=3),
        content="Go-live targeted for Q1", attributes={"due_date_precision": "quarter"},
    )
    for obj in (integration_req, onboarding_req, cs_region, cs_seats, cs_onboarding):
        _link(db, tenant_id, obj)

    validation = validate_handoff(db, tenant_id, entity_id, "sales_to_customer_success")
    gaps = _gaps_for(db, validation.id)
    assert len(gaps) == 4

    region_gap = _by(gaps, contract_field="requirements", slot="region")
    assert region_gap.outcome == "contradicted"  # O3
    assert region_gap.upstream_conflict is True
    assert region_gap.upstream_id == customer_region.id  # the customer's value, not the AE's
    assert float(region_gap.severity) == 3.0
    assert region_gap.severity_band == "high"

    seats_gap = _by(gaps, contract_field="requirements", slot="quantity")
    assert seats_gap.outcome == "contradicted"  # O4
    assert seats_gap.upstream_conflict is True
    assert seats_gap.upstream_id == customer_seats.id

    integration_gap = _by(gaps, contract_field="requirements", outcome="object_missing")
    assert integration_gap.upstream_id == integration_req.id  # O5
    assert integration_gap.slot is None

    onboarding_gap = _by(gaps, contract_field="requirements", slot="due_date")
    assert onboarding_gap.outcome == "generalized"  # O6
    assert onboarding_gap.upstream_id == onboarding_req.id


# ---------------------------------------------------------------------------
# Golden 3: cloud outage at peak hours (INC-2311) — §19.1 I1-I6
# ---------------------------------------------------------------------------


def test_golden3_incident_stale_reference_and_engineering_to_sales_losses(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """I1 (setup): the on-call's corrected root_cause supersedes the pre-rollback guess
    (R3). I2 time_window missing, I3 sla_impact missing (severity high), I4 impact
    contradicted, I5 the AE's Slack message still cites the *superseded* root cause ->
    stale_reference, I6 the remediation resolution has no sales counterpart at all ->
    object_missing."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    oncall = _make_person(db, tenant_id, "OnCall", "engineering")
    incident_source = _make_source(db, tenant_id, "engineering", "#incidents", T0, kind="slack")
    postmortem_source = _make_source(
        db, tenant_id, "engineering", "postmortem-INC-2311.md", T0 + timedelta(hours=1)
    )
    email_source = _make_source(
        db, tenant_id, "sales", "Sales -> Acme email", T0 + timedelta(hours=2), kind="email"
    )

    v1 = _make_object(
        db, tenant_id, entity_id, incident_source,
        type_="problem", subject_key="acme:incident:INC-2311", stage="engineering", authority=3,
        actor_role="engineering", valid_from=T0, content="checkout API 5xx spike, our bad deploy",
        attributes={"root_cause": "our bad deploy"}, actor_person_id=oncall,
    )

    v2 = _make_object(
        db, tenant_id, entity_id, incident_source,
        type_="problem", subject_key="acme:incident:INC-2311", stage="engineering", authority=3,
        actor_role="engineering", valid_from=T0 + timedelta(minutes=30),
        content="rolled back, no change; AWS us-east-1 ELB degraded",
        attributes={
            "root_cause": "AWS us-east-1 ELB degradation",
            "impact": {"customers": ["Acme", "Globex"], "data_loss": "~300 writes queued, replaying"},
            "time_window": {"start": "18:05", "end": "18:52", "timezone": "IST"},
            "sla_impact": {"breached": True, "contract_uptime": 99.95, "credit_owed": True},
            "extra": {"corrects": True},
        },
        confidence=0.9, actor_person_id=oncall,
    )
    resolve_object_state(db, tenant_id, v2)
    db.commit()
    db.refresh(v1)
    assert v1.status == "superseded"  # I1

    resolution_obj = _make_object(
        db, tenant_id, entity_id, postmortem_source,
        type_="resolution", subject_key="acme:incident:INC-2311", stage="engineering", authority=3,
        actor_role="engineering", valid_from=T0 + timedelta(hours=1),
        content="multi-AZ failover remediation",
        attributes={"remediation": "multi-AZ failover", "due_date": date(2026, 12, 15).isoformat(), "due_date_precision": "day"},
    )

    ae_email = _make_object(
        db, tenant_id, entity_id, email_source,
        type_="problem", subject_key="acme:incident:INC-2311", stage="sales", authority=3,
        actor_role="sales", valid_from=T0 + timedelta(hours=2),
        content="brief blip this evening, fully resolved, no data impact",
        attributes={"root_cause": "our bad deploy", "impact": {"data_loss": "no data impact"}},
    )

    for obj in (v1, v2, resolution_obj, ae_email):
        _link(db, tenant_id, obj)

    validation = validate_handoff(db, tenant_id, entity_id, "engineering_to_customer_facing::sales")
    gaps = _gaps_for(db, validation.id)
    assert len(gaps) == 5

    time_window_gap = _by(gaps, contract_field="incident", slot="time_window")
    assert time_window_gap.outcome == "missing"  # I2
    assert time_window_gap.upstream_id == v2.id

    sla_gap = _by(gaps, contract_field="incident", slot="sla_impact")
    assert sla_gap.outcome == "missing"  # I3
    assert sla_gap.severity_band == "high"
    assert float(sla_gap.severity) == 2.16

    impact_gap = _by(gaps, contract_field="incident", slot="impact")
    assert impact_gap.outcome == "contradicted"  # I4

    root_cause_gap = _by(gaps, contract_field="incident", slot="root_cause")
    assert root_cause_gap.outcome == "stale_reference"  # I5
    assert root_cause_gap.upstream_id == v2.id  # the *current* authoritative version
    assert root_cause_gap.downstream_id == ae_email.id

    resolution_gap = _by(gaps, contract_field="resolution", outcome="object_missing")
    assert resolution_gap.upstream_id == resolution_obj.id  # I6


# ---------------------------------------------------------------------------
# Outcome coverage not exercised by the golden tables: equivalent + preserved
# ---------------------------------------------------------------------------


def test_equivalent_slot_value_produces_no_gap(db_session: tuple[Session, uuid.UUID]) -> None:
    """`equivalent` (§10.1: "different wording, same meaning") — no LLM judge call is
    available (dispatch: OpenRouter free tier saturated), so this exercises the
    deterministic synonym table instead of live paraphrase judging: "SAML" and "SAML 2.0"
    are the same protocol, differently worded, and must not be flagged. Contrast with
    test_golden2's `region_gap`/`seats_gap`, which pin genuinely `contradicted` differences."""
    db, tenant_id = db_session
    entity_id = _make_entity(db, tenant_id)
    call_source = _make_source(db, tenant_id, "sales", "SAML 2.0 required", T0, kind="call")
    prd_source = _make_source(db, tenant_id, "product", "SAML support", T0 + timedelta(days=1))

    sales_obj = _make_object(
        db, tenant_id, entity_id, call_source,
        subject_key="acme:sso", stage="sales", authority=4, actor_role="customer",
        valid_from=T0, content="Customer requires SAML 2.0",
        attributes={"protocol": "SAML 2.0"},
    )
    _link(db, tenant_id, sales_obj)
    product_obj = _make_object(
        db, tenant_id, entity_id, prd_source,
        subject_key="acme:sso", stage="product", authority=3, actor_role="product",
        valid_from=T0 + timedelta(days=1), content="Support SAML",
        attributes={"protocol": "SAML"},
    )
    _link(db, tenant_id, product_obj)

    validation = validate_handoff(db, tenant_id, entity_id, "sales_to_product")
    gaps = _gaps_for(db, validation.id)
    assert gaps == []  # equivalent, like preserved, produces no gap at all (§10.1 point 4)
