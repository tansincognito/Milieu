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
from app.models.orm import ContextGaps, Entities, HandoffValidations
from app.pipeline.contracts import get_contract
from app.pipeline.handoff import validate_handoff
from app.schemas.handoff import HandoffReportOut, HandoffValidateRequest, HandoffValidationOut

router = APIRouter()


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
