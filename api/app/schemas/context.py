"""Read model for GET /context/{id} (§4.3, §15)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

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
