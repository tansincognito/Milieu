"""Idempotent upsert of `/mock-data` into `simulation_seed_sources` (migration 0006).

Extracted from `scripts/import_mock_data_to_postgres.py` so `load_mock_data` and the eval
harness can call it directly instead of requiring that script to be run by hand first --
closing the gap its own docstring used to flag: the mock connectors previously always read
disk directly, never these rows, so `/connections/{kind}/sync`'s seed count and
`/simulation/search` were working off a separate copy of the same content that could drift
out of sync with whatever actually got ingested.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

_UPSERT_SQL = text(
    """
    INSERT INTO simulation_seed_sources (id, tenant_id, kind, external_id, payload, source_ts)
    VALUES (:id, :tenant_id, :kind, :external_id, cast(:payload AS jsonb), :source_ts)
    ON CONFLICT (tenant_id, kind, external_id)
    DO UPDATE SET payload = EXCLUDED.payload, source_ts = EXCLUDED.source_ts
    """
)


def _upsert(db: Session, tenant_id: uuid.UUID, kind: str, external_id: str, payload: dict) -> None:
    db.execute(
        _UPSERT_SQL,
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "kind": kind,
            "external_id": external_id,
            "payload": json.dumps(payload),
            "source_ts": None,
        },
    )


def import_drive(db: Session, tenant_id: uuid.UUID, mock_data_dir: Path) -> int:
    n = 0
    drive_dir = mock_data_dir / "drive"
    if not drive_dir.exists():
        return 0
    for path in sorted(drive_dir.rglob("*.md")):
        rel = str(path.relative_to(drive_dir))
        _upsert(db, tenant_id, "drive", rel, {"path": rel, "content": path.read_text()})
        n += 1
    return n


def import_email(db: Session, tenant_id: uuid.UUID, mock_data_dir: Path) -> int:
    n = 0
    email_dir = mock_data_dir / "email"
    if not email_dir.exists():
        return 0
    for path in sorted(email_dir.glob("*.json")):
        payload = json.loads(path.read_text())
        external_id = payload.get("thread_id", path.stem)
        _upsert(db, tenant_id, "email", external_id, payload)
        n += 1
    return n


def import_slack(db: Session, tenant_id: uuid.UUID, mock_data_dir: Path) -> int:
    n = 0
    seed_path = mock_data_dir / "slack" / "seed.json"
    if not seed_path.exists():
        return 0
    data = json.loads(seed_path.read_text())
    workspace_id = data.get("workspace_id")
    for channel in data.get("channels", []):
        channel_id = channel.get("id") or channel.get("channel_id") or channel.get("name")
        # A real `conversations.list` response wouldn't carry the workspace id on each
        # channel either -- a connection already knows which workspace it's scoped to.
        # Stamping it in here anyway is what lets `MockSlackSeedConnector.normalize` read a
        # self-contained row instead of needing a second query for seed-file-level context.
        _upsert(db, tenant_id, "slack", str(channel_id), {"workspace_id": workspace_id, **channel})
        n += 1
    return n


def import_calls(db: Session, tenant_id: uuid.UUID, mock_data_dir: Path) -> int:
    n = 0
    calls_dir = mock_data_dir / "calls"
    if not calls_dir.exists():
        return 0
    for path in sorted(calls_dir.glob("*.txt")):
        call_id = path.stem
        _upsert(db, tenant_id, "call", call_id, {"call_id": call_id, "transcript": path.read_text()})
        n += 1
    return n


def seed_simulation_sources(db: Session, tenant_id: uuid.UUID, mock_data_dir: Path) -> dict[str, int]:
    """Upsert every mock-data file into `simulation_seed_sources` for `tenant_id`. Safe to
    call on every ingest -- upserts by (tenant_id, kind, external_id), so a repeat run just
    refreshes `payload` for unchanged files rather than duplicating rows."""
    counts = {
        "drive": import_drive(db, tenant_id, mock_data_dir),
        "email": import_email(db, tenant_id, mock_data_dir),
        "slack": import_slack(db, tenant_id, mock_data_dir),
        "call": import_calls(db, tenant_id, mock_data_dir),
    }
    db.commit()
    return counts
