"""Incident Context Pack — read models.

Honesty note carried through every field here (see `api/app/api/incidents.py`'s module
docstring for the full reasoning): most incident-related Slack messages carry no structured
`incident_id` at all, only `subject_capability="incident"`. Only objects that DO carry one
(today: the postmortem document's own sections, via `process._sibling_subject_key`) are
`linked`. Everything else that matches by capability and time-window is `unconfirmed` — a
real signal, but not a proven one, and the UI must show that distinction rather than hide it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.context import ContextObjectOut


class IncidentSummaryOut(BaseModel):
    incident_id: str
    entities: list[str]  # entity names this incident's linked objects mention
    first_seen_at: datetime | None
    object_count: int


class IncidentTimelineEntryOut(BaseModel):
    object: ContextObjectOut
    at: datetime  # source_ts when known, else the object's created_at
    linked: bool  # True: carries this incident_id. False: same capability/time window only.


class IncidentPeopleOut(BaseModel):
    actor_label: str
    actor_role: str
    object_count: int


class IncidentContextPackOut(BaseModel):
    incident_id: str
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
    similar_past_incidents: list[str]  # incident_ids; empty is a real, honest answer today
