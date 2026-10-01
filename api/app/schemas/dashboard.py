"""Login resolution + the personalized "My Dashboard" (architecture v2 §8-9): a real login
is not built (no session/JWT layer exists yet), but the DIRECTORY lookup behind it is real —
this resolves an email to a real `people` row via the same table §14's ACL and authority
already depend on, not a client-side fake."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel

from app.schemas.context import ContextObjectOut


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None
    team: str | None
    role: str | None


class DeadlineOut(BaseModel):
    object: ContextObjectOut
    due_date: date
    due_date_precision: str | None
    overdue: bool


class DashboardOut(BaseModel):
    scope: str  # "personal" (team-scoped) or "org" (everything)
    team: str | None
    contradictions: list[ContextObjectOut]
    decisions_pending: list[ContextObjectOut]
    deadlines_incoming: list[DeadlineOut]
    degradation_gaps: list[uuid.UUID]  # context_gaps ids, status='open'
    degradation_count: int
    incidents: list[str]  # incident_ids with a linked object in this scope
