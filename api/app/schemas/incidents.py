"""Incident Context Pack — read/write models.

Backed by a first-class `incidents` table since migration 0009 (see its docstring): an
incident is now a real row, not a subject_key pattern reconstructed at read time. The
`linked`/`unconfirmed` distinction the pack shows is now `IncidentContextObjects.linked` —
a durable, reviewable fact — rather than a heuristic recomputed on every request.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.context import ContextObjectOut

Severity = Literal["P0", "P1", "P2"]
IncidentStatus = Literal["open", "resolved"]


class DeclareIncidentRequest(BaseModel):
    incident_id: str
    title: str
    severity: Severity = "P1"
    entity_ids: list[uuid.UUID]
    # When this incident actually started, for backfilling a historical incident into a
    # static demo dataset (e.g. from a postmortem's own date) rather than using wall-clock
    # "now" — which would be wrong for anything that isn't a live, happening-right-now P0.
    # Sets both `declared_at` and the fallback candidate-window anchor. Omit for a real
    # live declare, where "now" is correct.
    anchor_at: datetime | None = None


class LinkObjectRequest(BaseModel):
    context_object_id: uuid.UUID
    linked: bool = True


class IncidentSummaryOut(BaseModel):
    id: uuid.UUID
    incident_id: str
    title: str
    severity: Severity
    status: IncidentStatus
    entities: list[str]
    declared_at: datetime
    resolved_at: datetime | None
    object_count: int


class IncidentTimelineEntryOut(BaseModel):
    object: ContextObjectOut
    at: datetime  # source_ts when known, else the object's created_at
    linked: bool  # True: a confirmed tie. False: an unreviewed correlation candidate.


class IncidentPeopleOut(BaseModel):
    actor_label: str
    actor_role: str
    object_count: int


class IncidentContextPackOut(BaseModel):
    id: uuid.UUID
    incident_id: str
    title: str
    severity: Severity
    status: IncidentStatus
    entities: list[str]
    timeline: list[IncidentTimelineEntryOut]
    people: list[IncidentPeopleOut]
    # One current (active) object per slot-bearing type, when present — the "what do we
    # know right now" summary at the top of the pack.
    impact: ContextObjectOut | None
    sla_impact: ContextObjectOut | None
    root_cause: ContextObjectOut | None
    remediation: ContextObjectOut | None
    # §10's existing gap data for this incident's outbound handoff (engineering -> sales/
    # customer_success), if that contract has ever been validated for an affected entity.
    open_gaps: list[uuid.UUID]
    # Other RESOLVED incidents sharing at least one affected entity, most recent first.
    # Deterministic (shared entity), not embedding-similarity-ranked — see
    # api/app/api/incidents.py's module docstring for why that's a deliberate, stated
    # simplification rather than the full feature.
    similar_past_incidents: list[IncidentSummaryOut]
