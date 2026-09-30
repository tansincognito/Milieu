"""Organizational Flows screen — read models. Aggregates the existing handoff-validator
data (§10) across every entity per contract, so "12 active handoffs" reads as a real number
instead of a mockup label."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.handoff import ContractOut


class FlowSummaryOut(BaseModel):
    contract: ContractOut
    entities_validated: int  # how many accounts have ever had this handoff checked
    open_gaps: int
    gaps_by_severity_band: dict[str, int]
    last_validated_at: str | None
