"""GET /gaps, POST /gaps/{id}/review — dashboard additions matching the endpoints already
named in §15's API table. `context_gaps` rows are produced by the Day 3 handoff validator
(`app/pipeline/handoff.py`, wired through `POST /handoffs/validate` in `app/api/handoffs.py`).
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


def gap_to_out(db: Session, gap: ContextGaps, principal: list[str] | None) -> GapOut:
    """Shared serializer — also used by `app/api/handoffs.py`'s report endpoint so a gap
    looks identical whether read from `/gaps` or `/handoffs/{id}`."""
    upstream = db.get(ContextObjects, gap.upstream_id) if gap.upstream_id else None
    downstream = db.get(ContextObjects, gap.downstream_id) if gap.downstream_id else None
    return GapOut(
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
        upstream_conflict=gap.upstream_conflict,
        explanation=gap.explanation,
        status=gap.status,
        upstream=context_object_to_out(upstream, db.get(Sources, upstream.source_id), principal)
        if upstream
        else None,
        downstream=context_object_to_out(downstream, db.get(Sources, downstream.source_id), principal)
        if downstream
        else None,
    )


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
        # `upstream_id` is nullable (present-check gaps have no upstream counterpart, see
        # migration 0004), so scope to entity via a subquery over both sides rather than an
        # inner join on upstream_id alone, which would silently drop those rows.
        entity_object_ids = db.query(ContextObjects.id).filter(
            ContextObjects.entity_id == entity_id
        )
        query = query.filter(
            ContextGaps.upstream_id.in_(entity_object_ids)
            | ContextGaps.downstream_id.in_(entity_object_ids)
        )
    gaps = query.order_by(ContextGaps.severity.desc()).all()
    return [gap_to_out(db, gap, principal) for gap in gaps]


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
