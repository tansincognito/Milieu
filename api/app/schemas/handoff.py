"""§15 POST /handoffs/validate, GET /handoffs/{id} — read/write models for the Day 3
handoff validator (§10)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.context import GapOut


class HandoffValidateRequest(BaseModel):
    entity_id: uuid.UUID
    contract_id: str
    as_of: datetime | None = None


class HandoffValidationOut(BaseModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    contract_id: str
    as_of: datetime
    created_at: datetime
    summary: dict[str, Any]


class HandoffReportOut(HandoffValidationOut):
    gaps: list[GapOut]
