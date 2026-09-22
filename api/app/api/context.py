"""GET /context/{id} — object + evidence + provenance (§15)."""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.orm import ContextObjects, Sources
from app.schemas.context import ContextObjectOut, ProvenanceOut
from app.schemas.extraction import ContextAttributes, ContextType

router = APIRouter()


@router.get("/context/{context_id}", response_model=ContextObjectOut)
def get_context_object(context_id: uuid.UUID, db: Session = Depends(get_db)) -> ContextObjectOut:
    obj = db.get(ContextObjects, context_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="context object not found")

    source = db.get(Sources, obj.source_id)
    source_out = (
        ProvenanceOut(kind=source.kind, stage=source.stage, source_ts=source.source_ts,
                       provenance=source.provenance)
        if source is not None
        else None
    )

    span = obj.evidence_span
    assert span.lower is not None and span.upper is not None
    return ContextObjectOut(
        id=obj.id,
        tenant_id=obj.tenant_id,
        entity_id=obj.entity_id,
        type=cast(ContextType, obj.type),
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
