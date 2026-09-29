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


class ContractOut(BaseModel):
    """One loaded contract, for the dashboard's "which handoffs can I run?" picker."""

    id: str
    from_stage: str
    to_stage: str
    field_count: int


class HandoffValidateAllRequest(BaseModel):
    """Run every loaded contract for one entity in a single call.

    §17.4's chain view spans the whole sales -> product -> engineering path, so the screen
    needs every handoff at once; making the client fire one request per contract would let
    it render a half-populated chain if any single call failed.
    """

    entity_id: uuid.UUID
    as_of: datetime | None = None
