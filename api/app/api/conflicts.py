"""§15 POST /conflicts/{relation_id}/resolve — §7.3 R5 human resolution: pick a winner
(loser -> superseded) or supersede both (neither statement stands, e.g. a bad duplicate
pairing). Every resolution writes an append-only `reviews` row.

GET /conflicts is a dashboard addition: the review queue (§17.5) needs the `relation_id`
to call the resolve endpoint above, but /context/search and /entities/{id}/context only
return a flat list of `conflicting` objects with no relation id. This pairs each open
`contradicts` relation with both of its objects."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.context import context_object_to_out
from app.api.permissions import acl_visible
from app.core.db import get_db
from app.models.orm import (
    ContextObjects,
    ContextRelations,
    ContextVersions,
    Entities,
    Reviews,
    Sources,
)
from app.schemas.context import ConflictPairOut, ConflictResolveRequest

router = APIRouter()


@router.get("/conflicts", response_model=list[ConflictPairOut])
def list_conflicts(
    entity: str | None = Query(default=None, description="entity id or slug"),
    principal: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[ConflictPairOut]:
    query = db.query(ContextRelations).filter(
        ContextRelations.relation == "contradicts", ContextRelations.resolved_at.is_(None)
    )
    relations = query.order_by(ContextRelations.created_at.desc()).all()

    entity_id: uuid.UUID | None = None
    if entity is not None:
        try:
            entity_id = uuid.UUID(entity)
        except ValueError:
            entity_row = db.query(Entities).filter(Entities.slug == entity).first()
            if entity_row is None:
                return []
            entity_id = entity_row.id

    out: list[ConflictPairOut] = []
    for relation in relations:
        a = db.get(ContextObjects, relation.from_id)
        b = db.get(ContextObjects, relation.to_id)
        if a is None or b is None:
            continue
        if entity_id is not None and entity_id not in (a.entity_id, b.entity_id):
            continue
        a_source = db.get(Sources, a.source_id)
        b_source = db.get(Sources, b.source_id)
        # ACL-filter: only surface the pair if at least one side is visible to the caller.
        visible = (
            db.query(ContextObjects)
            .join(Sources, ContextObjects.source_id == Sources.id)
            .filter(ContextObjects.id.in_([a.id, b.id]), acl_visible(principal))
            .count()
        )
        if visible == 0:
            continue
        out.append(
            ConflictPairOut(
                relation_id=relation.id,
                from_object=context_object_to_out(a, a_source, principal),
                to_object=context_object_to_out(b, b_source, principal),
                created_at=relation.created_at,
            )
        )
    return out


def _transition(db: Session, obj: ContextObjects, status: str, reason: str, now: datetime) -> None:
    if obj.status == status:
        return
    obj.status = status
    obj.version += 1
    obj.updated_at = now
    db.add(
        ContextVersions(
            id=uuid.uuid4(),
            context_id=obj.id,
            version=obj.version,
            status=status,
            attributes=obj.attributes,
            content=obj.content,
            changed_by=None,
            reason=reason,
            created_at=now,
        )
    )


@router.post("/conflicts/{relation_id}/resolve")
def resolve_conflict(
    relation_id: uuid.UUID, body: ConflictResolveRequest, db: Session = Depends(get_db)
) -> dict[str, str]:
    relation = db.get(ContextRelations, relation_id)
    if relation is None:
        raise HTTPException(status_code=404, detail="relation not found")
    if relation.relation not in ("contradicts", "duplicate_of"):
        raise HTTPException(
            status_code=400, detail=f"relation {relation.relation!r} is not a resolvable conflict"
        )
    if relation.resolved_at is not None:
        raise HTTPException(status_code=400, detail="relation already resolved")

    a = db.get(ContextObjects, relation.from_id)
    b = db.get(ContextObjects, relation.to_id)
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="one or both context objects not found")

    now = datetime.now(UTC)
    before = {"a_status": a.status, "b_status": b.status}

    if body.resolution == "winner":
        if body.winner_id not in (a.id, b.id):
            raise HTTPException(
                status_code=400, detail="winner_id must be one of the relation's two objects"
            )
        winner, loser = (a, b) if body.winner_id == a.id else (b, a)
        _transition(db, winner, "active", "R5: human-resolved winner", now)
        _transition(db, loser, "superseded", f"R5: superseded by human resolution ({winner.id})", now)
        loser.valid_to = now
        db.add(
            ContextRelations(
                id=uuid.uuid4(),
                from_id=winner.id,
                to_id=loser.id,
                relation="supersedes",
                created_by="human:review",
                confidence=None,
                created_at=now,
                resolved_at=now,
            )
        )
    else:  # both_superseded
        _transition(db, a, "superseded", "R5: both superseded by human resolution", now)
        _transition(db, b, "superseded", "R5: both superseded by human resolution", now)
        a.valid_to = now
        b.valid_to = now

    relation.resolved_at = now
    db.add(
        Reviews(
            id=uuid.uuid4(),
            reviewer_id=body.reviewer_id,
            target_kind="context_relation",
            target_id=relation.id,
            action="resolve_conflict",
            before=before,
            after={"resolution": body.resolution, "winner_id": str(body.winner_id) if body.winner_id else None},
            note=body.note,
            created_at=now,
        )
    )
    db.commit()
    return {"relation_id": str(relation.id), "resolution": body.resolution}
