"""Read model for GET /context/{id} (§4.3, §15)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.extraction import ContextAttributes, ContextType


class ProvenanceOut(BaseModel):
    kind: str
    stage: str | None
    source_ts: datetime
    provenance: dict[str, Any] | None = None  # redacted to {kind, stage, source_ts} per §14


class ContextObjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    entity_id: uuid.UUID
    type: ContextType
    subject_key: str
    content: str
    attributes: ContextAttributes
    actor_label: str
    actor_role: str
    stage: str | None
    authority: int
    confidence: float
    status: str
    valid_from: datetime
    valid_to: datetime | None
    source_id: uuid.UUID
    evidence_quote: str
    evidence_span: list[int]
    version: int
    created_at: datetime
    updated_at: datetime
    source: ProvenanceOut | None = None


class ContextVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    status: str
    content: str
    attributes: dict[str, Any]
    changed_by: uuid.UUID | None
    reason: str | None
    created_at: datetime


class RelationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_id: uuid.UUID
    to_id: uuid.UUID
    relation: str
    created_by: str | None
    confidence: float | None
    created_at: datetime
    resolved_at: datetime | None


class ContextHistoryOut(BaseModel):
    context_id: uuid.UUID
    versions: list[ContextVersionOut]
    supersession_chain: list[RelationOut]


class LineageNodeOut(BaseModel):
    object_id: uuid.UUID
    type: str
    subject_key: str
    stage: str | None
    content: str
    status: str
    authority: int
    valid_from: datetime
    depth: int


class ContextLineageOut(BaseModel):
    context_id: uuid.UUID
    upstream: list[LineageNodeOut]
    downstream: list[LineageNodeOut]


class EntityOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: str
    source_counts: dict[str, int]
    open_conflicts: int


class EntityContextGroupOut(BaseModel):
    type: str
    objects: list[ContextObjectOut]


class EntityContextOut(BaseModel):
    entity: EntityOut
    current: list[EntityContextGroupOut]
    conflicts: list[ContextObjectOut]
    source_counts: dict[str, int]


class ReviewRequest(BaseModel):
    action: Literal["confirm", "edit", "ignore", "mark_stale"]
    reviewer_id: uuid.UUID | None = None
    note: str | None = None
    content: str | None = None  # 'edit' only
    attributes: dict[str, Any] | None = None  # 'edit' only, merged onto existing attributes


class ConflictResolveRequest(BaseModel):
    resolution: Literal["winner", "both_superseded"]
    winner_id: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    note: str | None = None
