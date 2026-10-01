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
from app.schemas.flows import FlowSummaryOut
from app.schemas.handoff import (
    ContractDetailOut,
    ContractFieldOut,
    ContractOut,
    HandoffReportOut,
    HandoffValidateAllRequest,
    HandoffValidateRequest,
    HandoffValidationOut,
)

router = APIRouter()


@router.get("/flows", response_model=list[FlowSummaryOut])
def list_flows(db: Session = Depends(get_db)) -> list[FlowSummaryOut]:
    """Organizational Flows screen: every loaded contract with real counts aggregated
    across ALL entities -- "how many accounts does this handoff apply to, and how many
    open losses does it currently have" -- rather than one entity's report.

    Only the LATEST validation per (entity, contract) counts, matching
    `list_entity_handoff_reports`'s reasoning: re-running a handoff produces a new
    `handoff_validations` row, and this must reflect current state, not every historical
    run stacked on top of itself.
    """
    contracts = db.query(ContextContracts).order_by(ContextContracts.id).all()

    # Latest validation id per (entity_id, contract_id), computed once for every contract.
    all_validations = (
        db.query(HandoffValidations)
        .order_by(HandoffValidations.created_at.desc())
        .all()
    )
    latest_by_pair: dict[tuple[uuid.UUID, str], HandoffValidations] = {}
    for v in all_validations:
        key = (v.entity_id, v.contract_id)
        latest_by_pair.setdefault(key, v)

    latest_by_contract: dict[str, list[HandoffValidations]] = {}
    for v in latest_by_pair.values():
        latest_by_contract.setdefault(v.contract_id, []).append(v)

    out: list[FlowSummaryOut] = []
    for contract in contracts:
        validations = latest_by_contract.get(contract.id, [])
        validation_ids = [v.id for v in validations]
        gaps = (
            db.query(ContextGaps)
            .filter(ContextGaps.validation_id.in_(validation_ids), ContextGaps.status == "open")
            .all()
            if validation_ids
            else []
        )
        by_band: dict[str, int] = {}
        for g in gaps:
            by_band[g.severity_band] = by_band.get(g.severity_band, 0) + 1
        last_at = max((v.created_at for v in validations), default=None)

        out.append(
            FlowSummaryOut(
                contract=ContractOut(
                    id=contract.id,
                    from_stage=contract.from_stage,
                    to_stage=contract.to_stage,
                    field_count=len(contract.spec.get("fields", [])),
                ),
                entities_validated=len(validations),
                open_gaps=len(gaps),
                gaps_by_severity_band=by_band,
                last_validated_at=last_at.isoformat() if last_at else None,
            )
        )
    return out


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


@router.get("/contracts/{contract_id}", response_model=ContractDetailOut)
def get_contract_detail(contract_id: str, db: Session = Depends(get_db)) -> ContractDetailOut:
    """§9's typed field checklist for one contract — the Organizational Flows screen's
    "click a flow to see its Context Contract"."""
    spec = get_contract(db, contract_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="contract not found")
    return ContractDetailOut(
        id=spec.id,
        from_stage=spec.from_stage,
        to_stage=spec.to_stage,
        field_count=len(spec.fields),
        fields=[
            ContractFieldOut(
                name=f.name,
                check=f.check,
                types=f.types,
                slots=f.slots,
                importance=f.importance,
                min_upstream_authority=f.min_upstream_authority,
                rule=f.rule,
            )
            for f in spec.fields
        ],
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
