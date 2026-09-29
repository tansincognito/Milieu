"""§15 POST /handoffs/validate, GET /handoffs/{id} — runs and reads the Day 3 handoff
validator (§10). This is the `context_gaps` producer: the dashboard's Gaps tab and
`GET /gaps` (app/api/gaps.py) were built against the table before anything wrote to it."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.gaps import gap_to_out
from app.core.config import get_settings
from app.core.db import get_db
from app.models.orm import ContextContracts, ContextGaps, Entities, HandoffValidations
from app.pipeline.contracts import get_contract
from app.pipeline.handoff import validate_handoff
from app.schemas.handoff import (
    ContractOut,
    HandoffReportOut,
    HandoffValidateAllRequest,
    HandoffValidateRequest,
    HandoffValidationOut,
)

router = APIRouter()


@router.get("/contracts", response_model=list[ContractOut])
def list_contracts(db: Session = Depends(get_db)) -> list[ContractOut]:
    """Every loaded contract, ordered by the stage path they form."""
    rows = db.query(ContextContracts).order_by(ContextContracts.id).all()
    return [
        ContractOut(
            id=row.id,
            from_stage=row.from_stage,
            to_stage=row.to_stage,
            field_count=len(row.spec.get("fields", [])),
        )
        for row in rows
    ]


@router.post("/handoffs/validate", response_model=HandoffValidationOut)
def run_handoff_validation(
    body: HandoffValidateRequest, db: Session = Depends(get_db)
) -> HandoffValidationOut:
    if db.get(Entities, body.entity_id) is None:
        raise HTTPException(status_code=404, detail="entity not found")
    if get_contract(db, body.contract_id) is None:
        raise HTTPException(status_code=404, detail="contract not found")

    settings = get_settings()
    validation = validate_handoff(
        db,
        tenant_id=uuid.UUID(settings.tenant_id),
        entity_id=body.entity_id,
        contract_id=body.contract_id,
        as_of=body.as_of,
    )
    return HandoffValidationOut(
        id=validation.id,
        entity_id=validation.entity_id,
        contract_id=validation.contract_id,
        as_of=validation.as_of,
        created_at=validation.created_at,
        summary=validation.summary,
    )


@router.post("/handoffs/validate-all", response_model=list[HandoffValidationOut])
def run_all_handoff_validations(
    body: HandoffValidateAllRequest, db: Session = Depends(get_db)
) -> list[HandoffValidationOut]:
    """Run every loaded contract for one entity (§17.4's chain view needs the whole path)."""
    if db.get(Entities, body.entity_id) is None:
        raise HTTPException(status_code=404, detail="entity not found")

    contract_ids = [row.id for row in db.query(ContextContracts.id).order_by(ContextContracts.id)]
    if not contract_ids:
        raise HTTPException(
            status_code=409,
            detail="no contracts loaded — seed them from /contracts before validating",
        )

    settings = get_settings()
    tenant_id = uuid.UUID(settings.tenant_id)
    out: list[HandoffValidationOut] = []
    for contract_id in contract_ids:
        validation = validate_handoff(
            db,
            tenant_id=tenant_id,
            entity_id=body.entity_id,
            contract_id=contract_id,
            as_of=body.as_of,
        )
        out.append(
            HandoffValidationOut(
                id=validation.id,
                entity_id=validation.entity_id,
                contract_id=validation.contract_id,
                as_of=validation.as_of,
                created_at=validation.created_at,
                summary=validation.summary,
            )
        )
    return out


@router.get("/entities/{entity_id}/handoffs", response_model=list[HandoffReportOut])
def list_entity_handoff_reports(
    entity_id: uuid.UUID,
    principal: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[HandoffReportOut]:
    """The latest report per contract for one entity — the chain view's data source.

    Only the most recent validation of each contract is returned: re-running a handoff
    writes a new `handoff_validations` row with its own gaps, and the grid must show the
    current state, not every historical run stacked on top of itself.
    """
    if db.get(Entities, entity_id) is None:
        raise HTTPException(status_code=404, detail="entity not found")

    validations = (
        db.query(HandoffValidations)
        .filter(HandoffValidations.entity_id == entity_id)
        .order_by(HandoffValidations.created_at.desc())
        .all()
    )

    latest: dict[str, HandoffValidations] = {}
    for validation in validations:
        latest.setdefault(validation.contract_id, validation)

    reports: list[HandoffReportOut] = []
    for contract_id in sorted(latest):
        validation = latest[contract_id]
        gaps = (
            db.query(ContextGaps)
            .filter(ContextGaps.validation_id == validation.id)
            .order_by(ContextGaps.severity.desc())
            .all()
        )
        reports.append(
            HandoffReportOut(
                id=validation.id,
                entity_id=validation.entity_id,
                contract_id=validation.contract_id,
                as_of=validation.as_of,
                created_at=validation.created_at,
                summary=validation.summary,
                gaps=[gap_to_out(db, gap, principal) for gap in gaps],
            )
        )
    return reports


@router.get("/handoffs/{validation_id}", response_model=HandoffReportOut)
def get_handoff_report(
    validation_id: uuid.UUID,
    principal: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HandoffReportOut:
    validation = db.get(HandoffValidations, validation_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="handoff validation not found")

    gaps = (
        db.query(ContextGaps)
        .filter(ContextGaps.validation_id == validation_id)
        .order_by(ContextGaps.severity.desc())
        .all()
    )
    return HandoffReportOut(
        id=validation.id,
        entity_id=validation.entity_id,
        contract_id=validation.contract_id,
        as_of=validation.as_of,
        created_at=validation.created_at,
        summary=validation.summary,
        gaps=[gap_to_out(db, gap, principal) for gap in gaps],
    )
