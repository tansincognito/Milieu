"""Org setup screen + simulation/production toggle — read/write models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    mode: Literal["simulation", "production"]


class TenantModeUpdate(BaseModel):
    mode: Literal["simulation", "production"]


class ConnectionOut(BaseModel):
    id: uuid.UUID | None  # None until this kind has ever been toggled
    kind: str
    provider: str
    status: str
    external_account: str | None
    last_synced_at: datetime | None
    last_error: str | None
    seed_rows: int  # how many simulation_seed_sources rows exist for this kind


class ConnectionUpdate(BaseModel):
    connect: bool  # true = check the box (connect), false = uncheck (disconnect)


class DepartmentOut(BaseModel):
    """§7.1 of architecture v2: departments-as-data isn't built yet (it needs its own
    migration replacing the STAGES CHECK constraint with a real FK-backed table). This
    mirrors that shape read-only, off today's fixed four stages, so the org setup screen's
    team checklist has something real to render without pretending departments are
    already configurable."""

    slug: str
    name: str
    configurable: bool = False
