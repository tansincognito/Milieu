#!/usr/bin/env python3
"""One-time import: copy `/mock-data`'s content into `simulation_seed_sources` (migration
0006), so simulation mode's seed data lives in Postgres instead of flat files on disk.

Scope, stated plainly: this makes the data available in Postgres in a shape close to what a
real API would hand back (one row per drive doc / email thread / Slack channel / call
transcript). It does NOT yet switch `app/connectors/mock_*.py` to read from these rows
instead of disk -- that connector-level change is deliberately deferred to a follow-up, so
today's run keeps reading disk files exactly as it always has and nothing already verified
(ingestion, the eval harness, the dashboard) is put at risk by this import existing.

Usage: uv run python ../scripts/import_mock_data_to_postgres.py [--tenant-id UUID]
Idempotent: re-running upserts by (tenant_id, kind, external_id).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.db import engine  # noqa: E402

MOCK_DATA_DIR = Path(__file__).resolve().parents[1] / "mock-data"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

UPSERT_SQL = text(
    """
    INSERT INTO simulation_seed_sources (id, tenant_id, kind, external_id, payload, source_ts)
    VALUES (:id, :tenant_id, :kind, :external_id, :payload, :source_ts)
    ON CONFLICT (tenant_id, kind, external_id)
    DO UPDATE SET payload = EXCLUDED.payload, source_ts = EXCLUDED.source_ts
    """
)


def _upsert(session, tenant_id: str, kind: str, external_id: str, payload: dict) -> None:
    session.execute(
        UPSERT_SQL,
        {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "kind": kind,
            "external_id": external_id,
            "payload": json.dumps(payload),
            "source_ts": None,
        },
    )


def import_drive(session, tenant_id: str) -> int:
    n = 0
    for path in sorted((MOCK_DATA_DIR / "drive").rglob("*.md")):
        rel = str(path.relative_to(MOCK_DATA_DIR / "drive"))
        _upsert(session, tenant_id, "drive", rel, {"path": rel, "content": path.read_text()})
        n += 1
    return n


def import_email(session, tenant_id: str) -> int:
    n = 0
    email_dir = MOCK_DATA_DIR / "email"
    if not email_dir.exists():
        return 0
    for path in sorted(email_dir.glob("*.json")):
        payload = json.loads(path.read_text())
        external_id = payload.get("thread_id", path.stem)
        _upsert(session, tenant_id, "email", external_id, payload)
        n += 1
    return n


def import_slack(session, tenant_id: str) -> int:
    n = 0
    seed_path = MOCK_DATA_DIR / "slack" / "seed.json"
    if not seed_path.exists():
        return 0
    data = json.loads(seed_path.read_text())
    for channel in data.get("channels", []):
        channel_id = channel.get("id") or channel.get("channel_id") or channel.get("name")
        _upsert(session, tenant_id, "slack", str(channel_id), channel)
        n += 1
    return n


def import_calls(session, tenant_id: str) -> int:
    n = 0
    calls_dir = MOCK_DATA_DIR / "calls"
    if not calls_dir.exists():
        return 0
    for path in sorted(calls_dir.glob("*.txt")):
        call_id = path.stem
        _upsert(
            session, tenant_id, "call", call_id, {"call_id": call_id, "transcript": path.read_text()}
        )
        n += 1
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    args = parser.parse_args()

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        counts = {
            "drive": import_drive(session, args.tenant_id),
            "email": import_email(session, args.tenant_id),
            "slack": import_slack(session, args.tenant_id),
            "call": import_calls(session, args.tenant_id),
        }
        session.commit()
    finally:
        session.close()

    total = sum(counts.values())
    print(f"imported {total} rows into simulation_seed_sources for tenant {args.tenant_id}:")
    for kind, n in counts.items():
        print(f"  {kind:8s} {n}")


if __name__ == "__main__":
    main()
