"""Runs the real §10 handoff validator against the eval tenant's ingested context objects
for the four (entity, contract) pairs the §19.1 golden tables (and the §19.2 degradation
set drawn from them) pin, and adapts the resulting `context_gaps` rows into
`evals.scoring.Prediction`s so they can be scored with the same rubric as the retrieval
baseline (`evals/retrieval_baseline.py`).

Deliberately does not import anything from `app.pipeline.handoff` beyond `validate_handoff`
itself -- the dispatch is explicit that module is off limits to modify, and this file only
ever calls it, never reimplements or patches its logic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.orm import ContextGaps, ContextObjects, Entities, HandoffValidations
from app.pipeline.contracts import load_contracts
from app.pipeline.handoff import validate_handoff
from evals.scoring import GoldCase, Prediction

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"

# (entity_slug, contract_id) pairs needed to reproduce §19.1 Golden 1/2/3 and the §19.2
# degradation set drawn from them (G1-G6, O3-O6, I2-I6). `contract_id` values match
# `context_contracts.id` exactly, including the `::<stage>` suffix `app.pipeline.contracts`
# gives a multi-`to_stage` contract (see that module's docstring).
REQUIRED_VALIDATIONS: list[tuple[str, str]] = [
    ("acme", "sales_to_product"),
    ("acme", "product_to_engineering"),
    ("globex", "sales_to_customer_success"),
    ("acme", "engineering_to_customer_facing::sales"),
]


@dataclass
class HandoffEvalReport:
    ran: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_required_handoff_validations(db: Session, tenant_id: uuid.UUID) -> HandoffEvalReport:
    """Loads `/contracts` (idempotent — see `load_contracts`) and runs `validate_handoff`
    for every pair in `REQUIRED_VALIDATIONS`, so the pass/fail `handoff_gap_outcome` checks
    in `evals/checks.py` and the precision/recall scoring below both have a fresh
    `handoff_validations` row (+ its `context_gaps`) to read. Safe to call once per `make
    eval` run, after the seeded ingest -- re-running is safe per `validate_handoff`'s own
    docstring (each call is a fresh validation, history is never deleted)."""
    load_contracts(db, CONTRACTS_DIR)
    report = HandoffEvalReport()
    for entity_slug, contract_id in REQUIRED_VALIDATIONS:
        entity = (
            db.query(Entities)
            .filter(Entities.tenant_id == tenant_id, Entities.slug == entity_slug)
            .first()
        )
        if entity is None:
            report.errors.append(
                f"entity {entity_slug!r} not found for tenant {tenant_id} -- directory seed "
                "or ingest is incomplete, no validation could run"
            )
            continue
        try:
            validate_handoff(db, tenant_id, entity.id, contract_id)
        except Exception as exc:  # noqa: BLE001 - recorded, not fatal to the rest of the run
            report.errors.append(
                f"validate_handoff(entity={entity_slug!r}, contract={contract_id!r}) failed: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        report.ran.append((entity_slug, contract_id))
    return report


def latest_validation(
    db: Session, tenant_id: uuid.UUID, entity_slug: str, contract_id: str
) -> HandoffValidations | None:
    entity = (
        db.query(Entities)
        .filter(Entities.tenant_id == tenant_id, Entities.slug == entity_slug)
        .first()
    )
    if entity is None:
        return None
    return (
        db.query(HandoffValidations)
        .filter(
            HandoffValidations.entity_id == entity.id,
            HandoffValidations.contract_id == contract_id,
        )
        .order_by(HandoffValidations.created_at.desc())
        .first()
    )


def gap_subject_key(db: Session, gap: ContextGaps) -> str | None:
    """A gap's "subject" is whichever side of it exists — `upstream_id` for every outcome
    except `missing` on a `present`-check field (no upstream comparison, §9), where only
    `downstream_id` is set."""
    obj_id = gap.upstream_id or gap.downstream_id
    if obj_id is None:
        return None
    obj = db.get(ContextObjects, obj_id)
    return obj.subject_key if obj is not None else None


def engine_predictions(db: Session, tenant_id: uuid.UUID) -> list[Prediction]:
    """One `Prediction` per `context_gaps` row across every `REQUIRED_VALIDATIONS` pair's
    *latest* validation — the engine's full output on the scenarios the degradation set
    pins, normalized for `evals.scoring.score`."""
    predictions: list[Prediction] = []
    for entity_slug, contract_id in REQUIRED_VALIDATIONS:
        validation = latest_validation(db, tenant_id, entity_slug, contract_id)
        if validation is None:
            continue
        gaps = db.query(ContextGaps).filter(ContextGaps.validation_id == validation.id).all()
        for gap in gaps:
            subject_key = gap_subject_key(db, gap)
            if subject_key is None:
                continue
            predictions.append(
                Prediction(
                    contract_id=contract_id,
                    entity_slug=entity_slug,
                    subject=subject_key,
                    slot=gap.slot,
                    outcome=gap.outcome,
                    source=f"context_gaps:{gap.id}",
                )
            )
    return predictions


def load_degradation_gold_cases(cases: list[dict]) -> list[GoldCase]:
    """Reads the §19.2 degradation set's gold triples straight out of the case dicts
    `evals.runner.load_cases()` already parsed from `evals/cases/degradation.yaml` — one
    source of truth for "what a case asserts" (used by the pass/fail `handoff_gap_outcome`
    check) and "what the aggregate scorer expects" (used here)."""
    gold: list[GoldCase] = []
    for case in cases:
        if case.get("category") != "degradation":
            continue
        checks = case.get("checks", [])
        if len(checks) != 1 or checks[0]["kind"] != "handoff_gap_outcome":
            raise ValueError(
                f"degradation case {case['id']!r} must have exactly one `handoff_gap_outcome` "
                "check (evals/handoff_eval.py reads gold data from it directly)"
            )
        params = checks[0]["params"]
        subject_match = params.get("subject_key_contains")
        if subject_match is None:
            suffix = params.get("subject_key_suffix", "")
            subject_match = suffix.lstrip(":")
        gold.append(
            GoldCase(
                id=case["id"],
                scenario=case.get("scenario", ""),
                contract_id=params["contract_id"],
                entity_slug=params["entity_slug"],
                subject_match=subject_match,
                slot=params.get("slot"),
                outcome=params["outcome"],
            )
        )
    return gold
