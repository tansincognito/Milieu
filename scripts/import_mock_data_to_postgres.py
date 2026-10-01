#!/usr/bin/env python3
"""Standalone CLI for `/mock-data` -> `simulation_seed_sources` (migration 0006).

`app.pipeline.seed_simulation_sources.seed_simulation_sources` (the function this calls)
now also runs automatically from `load_mock_data` and the eval harness on every ingest, so
this script is no longer the only way the table gets populated -- it's kept for the case
where you want the seed rows refreshed (e.g. for `/simulation/search` or a `sync` status
check) without running a full ingest.

Usage: uv run python ../scripts/import_mock_data_to_postgres.py [--tenant-id UUID]
Idempotent: re-running upserts by (tenant_id, kind, external_id).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.pipeline.seed_simulation_sources import seed_simulation_sources  # noqa: E402

MOCK_DATA_DIR = Path(__file__).resolve().parents[1] / "mock-data"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    args = parser.parse_args()
    tenant_id = uuid.UUID(args.tenant_id)

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        counts = seed_simulation_sources(session, tenant_id, MOCK_DATA_DIR)
    finally:
        session.close()

    total = sum(counts.values())
    print(f"imported {total} rows into simulation_seed_sources for tenant {tenant_id}:")
    for kind, n in counts.items():
        print(f"  {kind:8s} {n}")


if __name__ == "__main__":
    main()
