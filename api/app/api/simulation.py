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
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_queue
from app.connectors.base import RawSource, SourceConnector
from app.connectors.mock_call import ConsentNotGivenError, MockCallConnector
from app.connectors.mock_drive import MockDriveConnector
from app.connectors.mock_email import MockEmailConnector
from app.connectors.mock_slack_seed import MockSlackSeedConnector
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.directory.resolve import SqlAlchemyPeopleDirectory
from app.models.orm import CONNECTION_KINDS, Connections, SimulationSeedSources, SyncRuns
from app.pipeline.ingest import ingest_source
from app.pipeline.seed_directory import load_slack_channel_stage_map
from app.pipeline.seed_simulation_sources import (
    import_calls,
    import_drive,
    import_email,
    import_slack,
)
from app.queue.base import JobQueue
from app.schemas.simulation import SearchHitOut, SyncRunOut, SyncStatus

router = APIRouter()


def _connector_for(
    kind: str, db: Session, tenant_id: uuid.UUID, mock_data_dir: Path
) -> SourceConnector | None:
    """The real `SourceConnector` for a syncable kind, or None for a kind with no connector
    (currently just `directory`, which is seeded separately via `seed_directory` -- it has
    no `Sources`/extraction pipeline of its own)."""
    if kind == "drive":
        return MockDriveConnector(db, tenant_id)
    if kind == "call":
        return MockCallConnector(db, tenant_id)
    if kind == "email":
        return MockEmailConnector(db, tenant_id, SqlAlchemyPeopleDirectory(db))
    if kind == "slack":
        channel_stage_map = load_slack_channel_stage_map(mock_data_dir / "directory.json")
        return MockSlackSeedConnector(db, tenant_id, channel_stage_map)
    return None


def _refresh_seed(kind: str, db: Session, tenant_id: uuid.UUID, mock_data_dir: Path) -> int:
    """Upsert just this kind's disk content into `simulation_seed_sources` before reading
    it back, so a sync always reflects the latest mock-data rather than whatever an earlier
    `load_mock_data`/import run happened to leave behind."""
    importer = {"drive": import_drive, "call": import_calls, "email": import_email, "slack": import_slack}
    fn = importer.get(kind)
    if fn is None:
        return 0
    n = fn(db, tenant_id, mock_data_dir)
    db.commit()
    return n


def _ingest_kind(
    kind: str, db: Session, queue: JobQueue, tenant_id: uuid.UUID, mock_data_dir: Path
) -> tuple[int, int, int]:
    """Actually run `kind`'s connector through the real ingest path (normalize -> hash ->
    enqueue, same as `load_mock_data`/`POST /sources/mock/load`), not just report a seed
    count -- found live (2026-10-01): `sync` previously faked `items_ingested = seed_count`
    without ever calling `ingest_source`, so clicking "sync" never actually produced new
    `Sources` rows or extraction jobs. Returns (items_seen, items_ingested, items_failed)."""
    _refresh_seed(kind, db, tenant_id, mock_data_dir)
    connector = _connector_for(kind, db, tenant_id, mock_data_dir)
    if connector is None:
        return 0, 0, 0

    seen = ingested = failed = 0
    raw: RawSource
    for raw in connector.fetch(None):
        seen += 1
        try:
            normalized = connector.normalize(raw)
        except ConsentNotGivenError:
            failed += 1
            continue
        ingest_source(db, queue, tenant_id, normalized)
        ingested += 1
    return seen, ingested, failed


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
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_settings),
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

    if simulate in _SIMULATABLE:
        # A simulated failure never touches real data -- "nothing here randomly flakes"
        # means the failure itself is on demand, not that it fakes real ingestion around it.
        seed_count = (
            db.query(func.count(SimulationSeedSources.id))
            .filter(SimulationSeedSources.tenant_id == tenant_id, SimulationSeedSources.kind == kind)
            .scalar()
            or 0
        )
        status = simulate
        error = _SYNC_ERROR_MESSAGE[simulate]
        items_seen = seed_count
        items_ingested = 0
        items_failed = seed_count
        conn.status = "error"
        conn.last_error = error
    else:
        mock_data_dir = Path(settings.mock_data_dir)
        items_seen, items_ingested, items_failed = _ingest_kind(
            kind, db, queue, tenant_id, mock_data_dir
        )
        if items_seen == 0:
            # Same honest-failure reasoning as PATCH /connections/{kind} (org_setup.py): a
            # source with no simulated data behind it cannot "succeed" at syncing.
            status = "failed"
            error = "no simulation seed data available for this source"
            conn.status = "error"
            conn.last_error = error
        elif items_failed > 0:
            status = "partial"
            error = f"{items_failed} of {items_seen} item(s) failed to ingest"
            conn.status = "connected"
            conn.last_error = error
            conn.last_synced_at = started
        else:
            status = "ok"
            error = None
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
