"""Pydantic models for LLM extraction output (§4.2, §11)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

ContextType = Literal[
    "requirement",
    "decision",
    "constraint",
    "commitment",
    "problem",
    "open_question",
    "resolution",
    "dependency",
]

DueDatePrecision = Literal["day", "month", "quarter"]
Priority = Literal["P0", "P1", "P2"]
Stance = Literal["required", "deferred", "in_progress", "done", "dropped", "rejected"]


class Impact(BaseModel):
    customers: list[str] = Field(default_factory=list)
    error_rate: float | None = None
    data_loss: str | None = None


class TimeWindow(BaseModel):
    start: str
    end: str
    timezone: str


class SlaImpact(BaseModel):
    breached: bool | None = None
    contract_uptime: float | None = None
    credit_owed: bool | None = None


class ContextAttributes(BaseModel):
    """Slots (§4.2). All optional — an extraction only sets the slots present in the text."""

    protocol: str | None = None
    idp: str | None = None
    due_date: date | None = None
    due_date_precision: DueDatePrecision | None = None
    priority: Priority | None = None
    stance: Stance | None = None
    acceptance_criteria: list[str] | None = None
    rationale: str | None = None
    region: str | None = None
    quantity: int | None = None
    quantity_unit: str | None = None
    integrations: list[str] | None = None
    plan: str | None = None
    # incident slots
    impact: Impact | None = None
    time_window: TimeWindow | None = None
    root_cause: str | None = None
    sla_impact: SlaImpact | None = None
    remediation: str | None = None
    # free key-values that don't fit another slot
    extra: dict[str, Any] = Field(default_factory=dict)


class ExtractedContext(BaseModel):
    """One extracted Context Object candidate, before entity resolution/authority (§13.1)."""

    type: ContextType
    subject_capability: str  # capability_vocab slug, or a new slug (routed to review)
    entity_hint: str | None = None  # free-text entity name the extractor saw, e.g. "Acme"
    content: str
    attributes: ContextAttributes = Field(default_factory=ContextAttributes)
    actor_label: str
    evidence_quote: str
    confidence: float = Field(ge=0.0, le=1.0)
    corrects: bool = False
    corrects_hint: str | None = None
    # Not in the spec's field list verbatim, but required to implement §5/§11's authority
    # split between a sales restatement (authority 2) and a hedged sales suggestion/
    # speculation (authority 1) — the extractor is the only stage that reads the hedging
    # language. Flagged as an interpretation gap in the dispatch report.
    speculative: bool = False


class ExtractionResult(BaseModel):
    items: list[ExtractedContext] = Field(default_factory=list)
