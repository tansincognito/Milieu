"""The simulated integration layer's API-facing surface (architecture v2 §5):
POST /connections/{kind}/sync, GET /connections/{kind}/sync-runs, GET /simulation/search.

Why this exists: a real Slack/Gmail/Drive integration isn't just "data exists somewhere" —
it's an API with auth state, occasional failures, rate limits, and a search endpoint.
Modeling those surfaces now, against simulated data, is what makes swapping in a real
connector later a matter of implementing the same `SyncResult`/`SearchHitOut` shapes against
a real API, not redesigning how the rest of the app talks to a connection.

`simulate=` on the sync endpoint is deliberately explicit rather than random: a demo or a
test that wants to show a rate-limit error needs to reproduce it on command, not hope for
one. Nothing here randomly flakes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models.orm import CONNECTION_KINDS, Connections, SimulationSeedSources, SyncRuns
from app.schemas.simulation import SearchHitOut, SyncRunOut, SyncStatus

router = APIRouter()

_SIMULATABLE = {"rate_limited", "auth_expired", "failed"}

_SYNC_ERROR_MESSAGE = {
    "rate_limited": "429 rate limited by upstream — too many requests in this window",
    "auth_expired": "401 unauthorized — the stored credential has expired, reconnect required",
    "failed": "upstream returned a 500 — transient failure, safe to retry",
}


def _tenant_id() -> uuid.UUID:
    return uuid.UUID(get_settings().tenant_id)


@router.post("/connections/{kind}/sync", response_model=SyncRunOut)
def sync_connection(
    kind: str,
    simulate: str | None = Query(default=None, description="ok|rate_limited|auth_expired|failed"),
    db: Session = Depends(get_db),
) -> SyncRunOut:
    if kind not in CONNECTION_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown connection kind '{kind}'")
    if simulate is not None and simulate not in _SIMULATABLE | {"ok"}:
        raise HTTPException(status_code=422, detail=f"simulate must be one of {_SIMULATABLE | {'ok'}}")

    tenant_id = _tenant_id()
    conn = (
        db.query(Connections)
        .filter(Connections.tenant_id == tenant_id, Connections.kind == kind)
        .first()
    )
    if conn is None:
        conn = Connections(tenant_id=tenant_id, kind=kind, provider="simulation")
        db.add(conn)
        db.flush()

    started = datetime.now(UTC)
    seed_count = (
        db.query(func.count(SimulationSeedSources.id))
        .filter(SimulationSeedSources.tenant_id == tenant_id, SimulationSeedSources.kind == kind)
        .scalar()
        or 0
    )

    if simulate in _SIMULATABLE:
        status = simulate
        error = _SYNC_ERROR_MESSAGE[simulate]
        items_seen = seed_count
        items_ingested = 0
        items_failed = seed_count
        conn.status = "error"
        conn.last_error = error
    elif seed_count == 0:
        # Same honest-failure reasoning as PATCH /connections/{kind} (org_setup.py): a
        # source with no simulated data behind it cannot "succeed" at syncing.
        status = "failed"
        error = "no simulation seed data available for this source"
        items_seen = items_ingested = items_failed = 0
        conn.status = "error"
        conn.last_error = error
    else:
        status = "ok"
        error = None
        items_seen = items_ingested = seed_count
        items_failed = 0
        conn.status = "connected"
        conn.last_error = None
        conn.last_synced_at = started

    run = SyncRuns(
        id=uuid.uuid4(),
        connection_id=conn.id,
        started_at=started,
        finished_at=datetime.now(UTC),
        status=status,
        items_seen=items_seen,
        items_ingested=items_ingested,
        items_failed=items_failed,
        error=error,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    return SyncRunOut(
        id=run.id,
        connection_id=run.connection_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=cast(SyncStatus, run.status),
        items_seen=run.items_seen,
        items_ingested=run.items_ingested,
        items_failed=run.items_failed,
        error=run.error,
    )


@router.get("/connections/{kind}/sync-runs", response_model=list[SyncRunOut])
def list_sync_runs(kind: str, db: Session = Depends(get_db)) -> list[SyncRunOut]:
    if kind not in CONNECTION_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown connection kind '{kind}'")
    tenant_id = _tenant_id()
    conn = (
        db.query(Connections)
        .filter(Connections.tenant_id == tenant_id, Connections.kind == kind)
        .first()
    )
    if conn is None:
        return []
    runs = (
        db.query(SyncRuns)
        .filter(SyncRuns.connection_id == conn.id)
        .order_by(SyncRuns.started_at.desc())
        .limit(20)
        .all()
    )
    return [
        SyncRunOut(
            id=r.id,
            connection_id=r.connection_id,
            started_at=r.started_at,
            finished_at=r.finished_at,
            status=cast(SyncStatus, r.status),
            items_seen=r.items_seen,
            items_ingested=r.items_ingested,
            items_failed=r.items_failed,
            error=r.error,
        )
        for r in runs
    ]


def _title_and_snippet(kind: str, payload: dict) -> tuple[str, str]:
    """Turn a kind-specific raw payload into a (title, snippet) pair the way a real search
    API would -- Slack returns a channel + message, Gmail a subject + body, Drive a
    filename + excerpt."""
    if kind == "drive":
        return payload.get("path", "document"), (payload.get("content") or "")[:220]
    if kind == "email":
        subject = payload.get("subject", "(no subject)")
        first_text = ""
        for m in payload.get("messages", []):
            if m.get("text"):
                first_text = m["text"]
                break
        return subject, first_text[:220]
    if kind == "slack":
        name = payload.get("name", payload.get("channel_id", "channel"))
        first_text = ""
        for m in payload.get("messages", []):
            if m.get("text"):
                first_text = m["text"]
                break
        return f"#{name}", first_text[:220]
    if kind == "call":
        return payload.get("call_id", "call"), (payload.get("transcript") or "")[:220]
    return kind, ""


def _searchable_text(kind: str, payload: dict) -> str:
    if kind == "drive":
        return f"{payload.get('path', '')} {payload.get('content', '')}"
    if kind == "email":
        texts = [payload.get("subject", "")]
        texts += [m.get("text", "") for m in payload.get("messages", [])]
        return " ".join(texts)
    if kind == "slack":
        texts = [payload.get("name", "")]
        texts += [m.get("text", "") for m in payload.get("messages", [])]
        return " ".join(texts)
    if kind == "call":
        return payload.get("transcript", "")
    return ""


@router.get("/simulation/search", response_model=list[SearchHitOut])
def search_simulated_sources(
    q: str = Query(..., min_length=1),
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
) -> list[SearchHitOut]:
    """Full-text search over every simulated source's raw content — "searchable messages/
    emails/documents" per the brief. Python-side substring match over the same payload a
    real search API would be handed a query against; good enough for simulated data, and a
    real connector's search would satisfy this same response shape."""
    tenant_id = _tenant_id()
    query = db.query(SimulationSeedSources).filter(SimulationSeedSources.tenant_id == tenant_id)
    if kind is not None:
        if kind not in CONNECTION_KINDS:
            raise HTTPException(status_code=404, detail=f"unknown connection kind '{kind}'")
        query = query.filter(SimulationSeedSources.kind == kind)

    needle = q.lower()
    hits: list[SearchHitOut] = []
    for row in query.all():
        haystack = _searchable_text(row.kind, row.payload).lower()
        if needle not in haystack:
            continue
        title, snippet = _title_and_snippet(row.kind, row.payload)
        hits.append(
            SearchHitOut(
                kind=row.kind,
                external_id=row.external_id,
                title=title,
                snippet=snippet,
                source_ts=row.source_ts,
            )
        )
        if len(hits) >= limit:
            break
    return hits
