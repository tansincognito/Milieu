"""Integration tests for `evals/handoff_eval.py` and the `handoff_gap_outcome` check kind
(`evals/checks.py`) added for the §19.2 degradation set. Hand-builds one small G1-shaped
scenario (protocol generalized, sales -> product), the same pattern
`tests/integration/test_handoff.py` uses for the full golden scenarios, then proves:

  - `run_required_handoff_validations` runs the real (LLM-free) §10 validator and persists
    `handoff_validations`/`context_gaps` the check kind and the scorer both read.
  - `evals.checks.check_handoff_gap_outcome` (the pass/fail side) passes/fails correctly.
  - `evals.handoff_eval.engine_predictions` (the scoring side) normalizes the same gap into
    a `Prediction` that `evals.scoring.score` can grade.
  - `evals/cases/degradation.yaml` itself parses into exactly the 15 gold cases §19.2 and
    the dispatch require (>=10 total, >=3 per golden scenario).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from psycopg.types.range import Range
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import engine
from app.models.orm import ContextObjects, Entities, Sources
from app.pipeline.contracts import load_contracts
from app.pipeline.lineage import create_lineage_links
from evals.checks import check_handoff_gap_outcome
from evals.handoff_eval import (
    REQUIRED_VALIDATIONS,
    engine_predictions,
    gap_subject_key,
    latest_validation,
    load_degradation_gold_cases,
    run_required_handoff_validations,
)
from evals.runner import load_cases

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 2, 0, 14, 32, tzinfo=UTC)
_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


@pytest.fixture(scope="module", autouse=True)
def _contracts_seeded() -> None:
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
    session.execute(text("DELETE FROM entities WHERE tenant_id = :t"), {"t": tenant_id})
    session.commit()
    session.close()


def _make_entity(db: Session, tenant_id: uuid.UUID, slug: str = "acme") -> uuid.UUID:
    entity = Entities(id=uuid.uuid4(), tenant_id=tenant_id, name=slug.title(), slug=slug, kind="customer")
    db.add(entity)
    db.flush()
    return entity.id


def _make_source(db: Session, tenant_id: uuid.UUID, stage: str, text_body: str, ts: datetime) -> uuid.UUID:
    source = Sources(
        id=uuid.uuid4(), tenant_id=tenant_id, kind="call", external_id=str(uuid.uuid4()),
        content_hash=uuid.uuid4().hex, stage=stage, acl=["*"], provenance={"kind": "call"},
        text=text_body, source_ts=ts,
    )
    db.add(source)
    db.flush()
    return source.id


def _make_object(
    db: Session, tenant_id: uuid.UUID, entity_id: uuid.UUID, source_id: uuid.UUID, *,
    subject_key: str, stage: str, authority: int, valid_from: datetime,
    attributes: dict | None = None,
) -> ContextObjects:
    obj = ContextObjects(
        id=uuid.uuid4(), tenant_id=tenant_id, entity_id=entity_id, type="requirement",
        subject_key=subject_key, content="statement", attributes=dict(attributes or {}),
        actor_label="test", actor_role="sales", actor_person_id=None,
        stage=stage, authority=authority, confidence=0.9, status="active",
        valid_from=valid_from, source_id=source_id, evidence_quote="evidence",
        evidence_span=Range(0, 8), embedding=[0.1] * 384, version=1,
        created_at=valid_from, updated_at=valid_from,
    )
    db.add(obj)
    db.flush()
    return obj


def _seed_g1_shaped_scenario(db: Session, tenant_id: uuid.UUID) -> uuid.UUID:
    """One (sales -> product, acme:sso, protocol generalized SAML -> SSO) gap, the same
    shape as §19.1's G1 — enough to exercise the check kind and the scorer without
    reproducing the entire golden fixture `test_handoff.py` already owns."""
    entity_id = _make_entity(db, tenant_id, "acme")
    call_source = _make_source(db, tenant_id, "sales", "SAML via Okta", T0)
    prd_source = _make_source(db, tenant_id, "product", "Support SSO", T0 + timedelta(days=2))

    sales_obj = _make_object(
        db, tenant_id, entity_id, call_source, subject_key="acme:sso", stage="sales",
        authority=4, valid_from=T0, attributes={"protocol": "SAML", "idp": "Okta"},
    )
    create_lineage_links(db, tenant_id, sales_obj)

    product_obj = _make_object(
        db, tenant_id, entity_id, prd_source, subject_key="acme:sso", stage="product",
        authority=3, valid_from=T0 + timedelta(days=2), attributes={},
    )
    create_lineage_links(db, tenant_id, product_obj)
    db.flush()
    return entity_id


def test_run_required_handoff_validations_runs_for_every_seeded_entity(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    db, tenant_id = db_session
    _seed_g1_shaped_scenario(db, tenant_id)

    report = run_required_handoff_validations(db, tenant_id)

    # acme has objects for 3 of the 4 required pairs (sales_to_product has real data; the
    # other two acme contracts run against no upstream data, which is a valid empty
    # validation, not an error); globex doesn't exist in this tenant at all.
    assert ("acme", "sales_to_product") in report.ran
    assert len(report.ran) == 3
    assert any("globex" in e for e in report.errors)
    assert len(report.errors) == 1


def test_run_required_handoff_validations_persists_a_gap_the_checks_can_read(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    db, tenant_id = db_session
    _seed_g1_shaped_scenario(db, tenant_id)
    run_required_handoff_validations(db, tenant_id)

    validation = latest_validation(db, tenant_id, "acme", "sales_to_product")
    assert validation is not None
    assert validation.summary["total"] >= 1


def test_check_handoff_gap_outcome_passes_for_the_real_expected_outcome(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    db, tenant_id = db_session
    _seed_g1_shaped_scenario(db, tenant_id)
    run_required_handoff_validations(db, tenant_id)

    result = check_handoff_gap_outcome(
        db, tenant_id,
        {
            "contract_id": "sales_to_product", "entity_slug": "acme",
            "subject_key_suffix": ":sso", "slot": "protocol", "outcome": "generalized",
            "inherited": False,
        },
    )
    assert result.passed, result.detail


def test_check_handoff_gap_outcome_fails_for_the_wrong_expected_outcome(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    db, tenant_id = db_session
    _seed_g1_shaped_scenario(db, tenant_id)
    run_required_handoff_validations(db, tenant_id)

    result = check_handoff_gap_outcome(
        db, tenant_id,
        {
            "contract_id": "sales_to_product", "entity_slug": "acme",
            "subject_key_suffix": ":sso", "slot": "protocol", "outcome": "missing",
        },
    )
    assert not result.passed
    assert "missing" in result.detail


def test_check_handoff_gap_outcome_fails_when_no_validation_ran(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    db, tenant_id = db_session
    result = check_handoff_gap_outcome(
        db, tenant_id,
        {"contract_id": "sales_to_product", "entity_slug": "acme", "slot": "protocol", "outcome": "generalized"},
    )
    assert not result.passed
    assert "no handoff_validations row" in result.detail


def test_gap_subject_key_reads_the_upstream_object(db_session: tuple[Session, uuid.UUID]) -> None:
    db, tenant_id = db_session
    _seed_g1_shaped_scenario(db, tenant_id)
    run_required_handoff_validations(db, tenant_id)

    validation = latest_validation(db, tenant_id, "acme", "sales_to_product")
    assert validation is not None
    from app.models.orm import ContextGaps

    gap = db.query(ContextGaps).filter(ContextGaps.validation_id == validation.id).first()
    assert gap is not None
    assert gap_subject_key(db, gap) == "acme:sso"


def test_engine_predictions_normalizes_the_gap_for_scoring(db_session: tuple[Session, uuid.UUID]) -> None:
    db, tenant_id = db_session
    _seed_g1_shaped_scenario(db, tenant_id)
    run_required_handoff_validations(db, tenant_id)

    preds = engine_predictions(db, tenant_id)
    matching = [
        p for p in preds
        if p.contract_id == "sales_to_product" and p.slot == "protocol" and p.subject == "acme:sso"
    ]
    assert len(matching) == 1
    assert matching[0].outcome == "generalized"
    assert matching[0].entity_slug == "acme"


def test_required_validations_cover_all_four_golden_contracts() -> None:
    contract_ids = {c for _, c in REQUIRED_VALIDATIONS}
    assert contract_ids == {
        "sales_to_product",
        "product_to_engineering",
        "sales_to_customer_success",
        "engineering_to_customer_facing::sales",
    }


def test_degradation_yaml_has_at_least_ten_cases_and_three_per_scenario() -> None:
    """§19.2: 'Degradation: >=10 (at least 3 per scenario)'."""
    cases = load_cases()
    gold = load_degradation_gold_cases(cases)
    assert len(gold) >= 10

    by_scenario: dict[str, int] = {}
    for g in gold:
        by_scenario[g.scenario] = by_scenario.get(g.scenario, 0) + 1
    assert by_scenario == {"golden1": 6, "golden2": 4, "golden3": 5}

    valid_outcomes = {"missing", "contradicted", "generalized", "stale_reference", "object_missing"}
    valid_contracts = {c for _, c in REQUIRED_VALIDATIONS}
    for g in gold:
        assert g.outcome in valid_outcomes, g.id
        assert g.contract_id in valid_contracts, g.id
        assert g.entity_slug in ("acme", "globex"), g.id
