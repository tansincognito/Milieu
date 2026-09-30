"""Sync simulation + search over simulation-seed content — read/write models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SyncStatus = Literal["ok", "partial", "rate_limited", "auth_expired", "failed"]


class SyncRunOut(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    status: SyncStatus
    items_seen: int
    items_ingested: int
    items_failed: int
    error: str | None


class SearchHitOut(BaseModel):
    """One matching simulated source, shaped like a real search API's result item
    (Slack/Gmail/Drive search all return: where it's from, a snippet, when)."""

    kind: str
    external_id: str
    title: str
    snippet: str
    source_ts: datetime | None
