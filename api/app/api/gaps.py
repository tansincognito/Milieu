"""GET /gaps, POST /gaps/{id}/review — dashboard additions matching the endpoints already
named (but not yet built) in §15's API table. `context_gaps` rows are only ever written by
the Day 3 handoff validator (§10), which is out of scope here, so these endpoints will
return an empty queue until that validator ships — the ORM table and its `open` status
already exist (§16), so this is a read/write surface on top of it, not new pipeline logic.
Used by the dashboard's Gaps tab (§17.2) and review queue (§17.5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.context import context_object_to_out
from app.core.db import get_db
from app.models.orm import ContextGaps, ContextObjects, Entities, Reviews, Sources
from app.schemas.context import GapOut, GapReviewRequest

router = APIRouter()


@router.get("/gaps", response_model=list[GapOut])
def list_gaps(
    entity: str | None = Query(default=None, description="entity id or slug"),
    status: str | None = Query(default=None, description="defaults to 'open'"),
    principal: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[GapOut]:
    entity_id: uuid.UUID | None = None
    if entity is not None:
        try:
            entity_id = uuid.UUID(entity)
        except ValueError:
            entity_row = db.query(Entities).filter(Entities.slug == entity).first()
            if entity_row is None:
                return []
            entity_id = entity_row.id

    query = db.query(ContextGaps).filter(ContextGaps.status == (status or "open"))
    if entity_id is not None:
        query = query.join(ContextObjects, ContextGaps.upstream_id == ContextObjects.id).filter(
            ContextObjects.entity_id == entity_id
        )
    gaps = query.order_by(ContextGaps.severity.desc()).all()

    out: list[GapOut] = []
    for gap in gaps:
        upstream = db.get(ContextObjects, gap.upstream_id)
        downstream = db.get(ContextObjects, gap.downstream_id) if gap.downstream_id else None
        out.append(
            GapOut(
                id=gap.id,
                validation_id=gap.validation_id,
                contract_field=gap.contract_field,
                upstream_id=gap.upstream_id,
                downstream_id=gap.downstream_id,
                slot=gap.slot,
                outcome=gap.outcome,
                severity=float(gap.severity),
                severity_band=gap.severity_band,
                inherited=gap.inherited,
                explanation=gap.explanation,
                status=gap.status,
                upstream=context_object_to_out(upstream, db.get(Sources, upstream.source_id), principal)
                if upstream
                else None,
                downstream=context_object_to_out(
                    downstream, db.get(Sources, downstream.source_id), principal
                )
                if downstream
                else None,
            )
        )
    return out


@router.post("/gaps/{gap_id}/review")
def review_gap(gap_id: uuid.UUID, body: GapReviewRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    gap = db.get(ContextGaps, gap_id)
    if gap is None:
        raise HTTPException(status_code=404, detail="gap not found")

    now = datetime.now(UTC)
    before = {"status": gap.status}
    gap.status = "resolved" if body.action == "confirm" else "ignored"
    db.add(
        Reviews(
            id=uuid.uuid4(),
            reviewer_id=body.reviewer_id,
            target_kind="context_gap",
            target_id=gap.id,
            action=body.action,
            before=before,
            after={"status": gap.status},
            note=body.note,
            created_at=now,
        )
    )
    db.commit()
    return {"gap_id": str(gap.id), "status": gap.status}
