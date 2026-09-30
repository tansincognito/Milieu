"""Org setup screen (§7 of ARCHITECTURE-v2.md): GET/PATCH /tenant, GET/PATCH /connections,
GET /departments.

"Connect once" from the pasted product spec: checking a source on this screen is a real
write to `connections` (migration 0006, §5), not UI chrome. Unchecking disconnects it.
Departments are read-only today -- see `DepartmentOut`'s docstring for why.
"""

from __future__ import annotations

import uuid
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models.orm import (
    CONNECTION_KINDS,
    STAGES,
    Connections,
    SimulationSeedSources,
    Tenants,
)
from app.schemas.org_setup import (
    ConnectionOut,
    ConnectionUpdate,
    DepartmentOut,
    TenantModeUpdate,
    TenantOut,
)

router = APIRouter()

_STAGE_NAMES = {
    "sales": "Sales",
    "product": "Product",
    "engineering": "Engineering",
    "customer_success": "Customer Success",
}


def _tenant_id() -> uuid.UUID:
    return uuid.UUID(get_settings().tenant_id)


@router.get("/tenant", response_model=TenantOut)
def get_tenant(db: Session = Depends(get_db)) -> TenantOut:
    tenant = db.get(Tenants, _tenant_id())
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return TenantOut(id=tenant.id, name=tenant.name, mode=cast(Literal["simulation", "production"], tenant.mode))


@router.patch("/tenant/mode", response_model=TenantOut)
def set_tenant_mode(body: TenantModeUpdate, db: Session = Depends(get_db)) -> TenantOut:
    tenant = db.get(Tenants, _tenant_id())
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")

    if body.mode == "production":
        # Honest refusal, not a fake success: no real Gmail/Slack/Drive/Directory OAuth
        # connector exists yet (architecture v2 §5). Switching the flag without a working
        # connector behind it would make every subsequent ingest silently do nothing.
        raise HTTPException(
            status_code=409,
            detail=(
                "production mode needs a real connector configured first — none exists yet. "
                "Every source today runs in simulation mode against seeded data."
            ),
        )

    tenant.mode = body.mode
    db.commit()
    return TenantOut(id=tenant.id, name=tenant.name, mode=cast(Literal["simulation", "production"], tenant.mode))


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(db: Session = Depends(get_db)) -> list[ConnectionOut]:
    tenant_id = _tenant_id()
    existing = {c.kind: c for c in db.query(Connections).filter(Connections.tenant_id == tenant_id)}
    seed_count_rows = (
        db.query(SimulationSeedSources.kind, func.count(SimulationSeedSources.id))
        .filter(SimulationSeedSources.tenant_id == tenant_id)
        .group_by(SimulationSeedSources.kind)
        .all()
    )
    seed_counts: dict[str, int] = {kind: count for kind, count in seed_count_rows}

    out: list[ConnectionOut] = []
    for kind in CONNECTION_KINDS:
        conn = existing.get(kind)
        out.append(
            ConnectionOut(
                id=conn.id if conn else None,
                kind=kind,
                provider=conn.provider if conn else "simulation",
                status=conn.status if conn else "disconnected",
                external_account=conn.external_account if conn else None,
                last_synced_at=conn.last_synced_at if conn else None,
                last_error=conn.last_error if conn else None,
                seed_rows=seed_counts.get(kind, 0),
            )
        )
    return out


@router.patch("/connections/{kind}", response_model=ConnectionOut)
def set_connection(kind: str, body: ConnectionUpdate, db: Session = Depends(get_db)) -> ConnectionOut:
    if kind not in CONNECTION_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown connection kind '{kind}'")
    tenant_id = _tenant_id()

    conn = (
        db.query(Connections)
        .filter(Connections.tenant_id == tenant_id, Connections.kind == kind)
        .first()
    )
    if conn is None:
        conn = Connections(tenant_id=tenant_id, kind=kind, provider="simulation")
        db.add(conn)

    if body.connect:
        seed_rows = (
            db.query(func.count(SimulationSeedSources.id))
            .filter(SimulationSeedSources.tenant_id == tenant_id, SimulationSeedSources.kind == kind)
            .scalar()
        )
        # Honest status, not a fake "connected": a source with zero seed rows (Linear,
        # Salesforce — no seed data exists for either in this repo) shows as 'error' rather
        # than pretending it has something to sync.
        conn.status = "connected" if seed_rows else "error"
        conn.last_error = None if seed_rows else "no simulation seed data available for this source"
    else:
        conn.status = "disconnected"

    db.commit()
    db.refresh(conn)
    seed_rows = (
        db.query(func.count(SimulationSeedSources.id))
        .filter(SimulationSeedSources.tenant_id == tenant_id, SimulationSeedSources.kind == kind)
        .scalar()
    )
    return ConnectionOut(
        id=conn.id,
        kind=conn.kind,
        provider=conn.provider,
        status=conn.status,
        external_account=conn.external_account,
        last_synced_at=conn.last_synced_at,
        last_error=conn.last_error,
        seed_rows=seed_rows,
    )


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments() -> list[DepartmentOut]:
    return [DepartmentOut(slug=s, name=_STAGE_NAMES[s]) for s in STAGES]
