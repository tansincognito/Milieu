"""§15 /context/* — object read, search, history, lineage, and the human review write path
(§7.3 R5, §4.3's "a human edit creates a new version... keeps original source_id/evidence").
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast as type_cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.permissions import PUBLIC_PRINCIPAL, acl_visible
from app.core.db import get_db
from app.models.orm import (
    ContextObjects,
    ContextRelations,
    ContextVersions,
    Entities,
    Reviews,
    Sources,
)
from app.pipeline.lineage import downstream_chain, upstream_chain
from app.schemas.context import (
    ContextHistoryOut,
    ContextLineageOut,
    ContextObjectOut,
    ContextVersionOut,
    LineageNodeOut,
    ProvenanceOut,
    RelationOut,
    ReviewRequest,
)
from app.schemas.extraction import ContextAttributes, ContextType

router = APIRouter()


def context_object_to_out(obj: ContextObjects, source: Sources | None, principals: list[str] | None) -> ContextObjectOut:
    source_out = None
    if source is not None:
        visible = source.acl and (
            PUBLIC_PRINCIPAL in source.acl or set(source.acl) & set(principals or [])
        )
        source_out = ProvenanceOut(
            kind=source.kind,
            stage=source.stage,
            source_ts=source.source_ts,
            provenance=source.provenance if visible else None,  # §14 redaction
        )
    span = obj.evidence_span
    assert span.lower is not None and span.upper is not None
    return ContextObjectOut(
        id=obj.id,
        tenant_id=obj.tenant_id,
        entity_id=obj.entity_id,
        type=type_cast(ContextType, obj.type),
        subject_key=obj.subject_key,
        content=obj.content,
        attributes=ContextAttributes.model_validate(obj.attributes),
        actor_label=obj.actor_label,
        actor_role=obj.actor_role,
        stage=obj.stage,
        authority=obj.authority,
        confidence=float(obj.confidence),
        status=obj.status,
        valid_from=obj.valid_from,
        valid_to=obj.valid_to,
        source_id=obj.source_id,
        evidence_quote=obj.evidence_quote,
        evidence_span=[span.lower, span.upper],
        version=obj.version,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
        source=source_out,
    )


@router.get("/context/search", response_model=list[ContextObjectOut])
def search_context(
    entity: str | None = Query(default=None, description="entity id or slug"),
    q: str | None = Query(default=None, description="ILIKE match on content"),
    type: str | None = Query(default=None),
    status: str | None = Query(default=None, description="defaults to 'active' (current state)"),
    principal: list[str] | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
) -> list[ContextObjectOut]:
    query = db.query(ContextObjects).join(Sources, ContextObjects.source_id == Sources.id)
    query = query.filter(acl_visible(principal))

    if entity is not None:
        try:
            entity_uuid = uuid.UUID(entity)
            query = query.filter(ContextObjects.entity_id == entity_uuid)
        except ValueError:
            entity_row = db.query(Entities).filter(Entities.slug == entity).first()
            if entity_row is None:
                return []
            query = query.filter(ContextObjects.entity_id == entity_row.id)
    if q is not None:
        query = query.filter(ContextObjects.content.ilike(f"%{q}%"))
    if type is not None:
        query = query.filter(ContextObjects.type == type)
    query = query.filter(ContextObjects.status == (status or "active"))

    objects = (
        query.order_by(ContextObjects.authority.desc(), ContextObjects.valid_from.desc())
        .limit(limit)
        .all()
    )
    source_ids = {o.source_id for o in objects}
    sources_by_id = {
        s.id: s for s in db.query(Sources).filter(Sources.id.in_(source_ids)).all()
    }
    return [context_object_to_out(o, sources_by_id.get(o.source_id), principal) for o in objects]


@router.get("/context/{context_id}", response_model=ContextObjectOut)
def get_context_object(
    context_id: uuid.UUID,
    principal: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ContextObjectOut:
    obj = db.get(ContextObjects, context_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="context object not found")
    source = db.get(Sources, obj.source_id)
    return context_object_to_out(obj, source, principal)


def _supersession_chain(db: Session, context_id: uuid.UUID) -> list[ContextRelations]:
    """Walks `supersedes` edges in both directions from `context_id` (small, linear
    chains in practice — a plain Python walk instead of a recursive CTE keeps this simple).
    """
    seen: set[uuid.UUID] = set()
    chain: list[ContextRelations] = []
    frontier = [context_id]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        edges = (
            db.query(ContextRelations)
            .filter(
                ContextRelations.relation == "supersedes",
                or_(ContextRelations.from_id == current, ContextRelations.to_id == current),
            )
            .all()
        )
        for edge in edges:
            chain.append(edge)
            frontier.extend([edge.from_id, edge.to_id])
    # de-dup (an edge can be reached from both endpoints)
    unique = {e.id: e for e in chain}
    return sorted(unique.values(), key=lambda e: e.created_at)


@router.get("/context/{context_id}/history", response_model=ContextHistoryOut)
def get_context_history(context_id: uuid.UUID, db: Session = Depends(get_db)) -> ContextHistoryOut:
    obj = db.get(ContextObjects, context_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="context object not found")
    versions = (
        db.query(ContextVersions)
        .filter(ContextVersions.context_id == context_id)
        .order_by(ContextVersions.version.asc())
        .all()
    )
    chain = _supersession_chain(db, context_id)
    return ContextHistoryOut(
        context_id=context_id,
        versions=[ContextVersionOut.model_validate(v) for v in versions],
        supersession_chain=[RelationOut.model_validate(r) for r in chain],
    )


@router.get("/context/{context_id}/lineage", response_model=ContextLineageOut)
def get_context_lineage(context_id: uuid.UUID, db: Session = Depends(get_db)) -> ContextLineageOut:
    obj = db.get(ContextObjects, context_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="context object not found")
    return ContextLineageOut(
        context_id=context_id,
        upstream=[LineageNodeOut(**row) for row in upstream_chain(db, context_id)],
        downstream=[LineageNodeOut(**row) for row in downstream_chain(db, context_id)],
    )


@router.post("/context/{context_id}/review", response_model=ContextObjectOut)
def review_context_object(
    context_id: uuid.UUID, body: ReviewRequest, db: Session = Depends(get_db)
) -> ContextObjectOut:
    obj = db.get(ContextObjects, context_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="context object not found")

    now = datetime.now(UTC)
    before = {"status": obj.status, "content": obj.content, "attributes": obj.attributes}

    if body.action == "confirm":
        obj.status = "active"
        obj.confirmed_by = body.reviewer_id
        obj.confirmed_at = now
    elif body.action == "ignore":
        obj.status = "ignored"
    elif body.action == "mark_stale":
        obj.status = "stale"
    elif body.action == "edit":
        # §4.3: an edit creates a new version and keeps the original source_id/evidence.
        if body.content is not None:
            obj.content = body.content
        if body.attributes is not None:
            obj.attributes = {**obj.attributes, **body.attributes}
    obj.version += 1
    obj.updated_at = now
    db.add(
        ContextVersions(
            id=uuid.uuid4(),
            context_id=obj.id,
            version=obj.version,
            status=obj.status,
            attributes=obj.attributes,
            content=obj.content,
            changed_by=body.reviewer_id,
            reason=body.note or f"review: {body.action}",
            created_at=now,
        )
    )
    db.add(
        Reviews(
            id=uuid.uuid4(),
            reviewer_id=body.reviewer_id,
            target_kind="context_object",
            target_id=obj.id,
            action=body.action,
            before=before,
            after={"status": obj.status, "content": obj.content, "attributes": obj.attributes},
            note=body.note,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(obj)
    source = db.get(Sources, obj.source_id)
    return context_object_to_out(obj, source, None)
