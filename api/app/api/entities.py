"""§15 GET /entities, GET /entities/{id}/context — §17's "Entity picker" and "Context
Explorer" backends, permission-filtered per §14."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.context import context_object_to_out
from app.api.permissions import acl_visible
from app.core.db import get_db
from app.models.orm import ContextObjects, Entities, Sources
from app.schemas.context import (
    ContextObjectOut,
    EntityContextGroupOut,
    EntityContextOut,
    EntityOut,
)

router = APIRouter()


def _source_counts(db: Session, entity_id: uuid.UUID, principals: list[str] | None) -> dict[str, int]:
    rows = (
        db.query(Sources.kind, func.count(func.distinct(ContextObjects.source_id)))
        .join(ContextObjects, ContextObjects.source_id == Sources.id)
        .filter(ContextObjects.entity_id == entity_id, acl_visible(principals))
        .group_by(Sources.kind)
        .all()
    )
    return {kind: count for kind, count in rows}


@router.get("/entities", response_model=list[EntityOut])
def list_entities(
    principal: list[str] | None = Query(default=None), db: Session = Depends(get_db)
) -> list[EntityOut]:
    entities = db.query(Entities).order_by(Entities.name.asc()).all()
    out = []
    for entity in entities:
        open_conflicts = (
            db.query(ContextObjects)
            .join(Sources, ContextObjects.source_id == Sources.id)
            .filter(
                ContextObjects.entity_id == entity.id,
                ContextObjects.status == "conflicting",
                acl_visible(principal),
            )
            .count()
        )
        out.append(
            EntityOut(
                id=entity.id,
                name=entity.name,
                slug=entity.slug,
                kind=entity.kind,
                source_counts=_source_counts(db, entity.id, principal),
                open_conflicts=open_conflicts,
            )
        )
    return out


@router.get("/entities/{entity_id}/context", response_model=EntityContextOut)
def get_entity_context(
    entity_id: uuid.UUID,
    principal: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> EntityContextOut:
    entity = db.get(Entities, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")

    base = (
        db.query(ContextObjects)
        .join(Sources, ContextObjects.source_id == Sources.id)
        .filter(ContextObjects.entity_id == entity_id, acl_visible(principal))
    )
    sources_cache: dict[uuid.UUID, Sources | None] = {}

    def _out(obj: ContextObjects) -> ContextObjectOut:
        if obj.source_id not in sources_cache:
            sources_cache[obj.source_id] = db.get(Sources, obj.source_id)
        return context_object_to_out(obj, sources_cache[obj.source_id], principal)

    current_objects = (
        base.filter(ContextObjects.status == "active")
        .order_by(ContextObjects.type.asc(), ContextObjects.authority.desc())
        .all()
    )
    grouped: dict[str, list[ContextObjectOut]] = {}
    for obj in current_objects:
        grouped.setdefault(obj.type, []).append(_out(obj))

    conflicting_objects = base.filter(ContextObjects.status == "conflicting").all()

    return EntityContextOut(
        entity=EntityOut(
            id=entity.id,
            name=entity.name,
            slug=entity.slug,
            kind=entity.kind,
            source_counts=_source_counts(db, entity.id, principal),
            open_conflicts=len(conflicting_objects),
        ),
        current=[EntityContextGroupOut(type=t, objects=objs) for t, objs in grouped.items()],
        conflicts=[_out(o) for o in conflicting_objects],
        source_counts=_source_counts(db, entity.id, principal),
    )
