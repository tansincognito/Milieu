"""Loads every mock connector's data through the same ingest path a real connector would
use (§2: "All loaded from /mock-data through the same ingestion interface")."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.connectors.base import SourceConnector
from app.connectors.mock_call import ConsentNotGivenError, MockCallConnector
from app.connectors.mock_drive import MockDriveConnector
from app.connectors.mock_email import MockEmailConnector
from app.connectors.mock_slack_seed import MockSlackSeedConnector
from app.directory.resolve import SqlAlchemyPeopleDirectory
from app.pipeline.ingest import ingest_source
from app.pipeline.seed_directory import load_directory, load_slack_channel_stage_map
from app.pipeline.seed_simulation_sources import seed_simulation_sources
from app.queue.base import JobQueue


def load_mock_data(
    db: Session, queue: JobQueue, tenant_id: uuid.UUID, mock_data_dir: Path
) -> dict[str, int]:
    directory_path = mock_data_dir / "directory.json"
    load_directory(db, tenant_id, directory_path)

    directory = SqlAlchemyPeopleDirectory(db)
    channel_stage_map = load_slack_channel_stage_map(directory_path)

    # Upsert disk -> `simulation_seed_sources` first, so the connectors below (and
    # `/connections/{kind}/sync`, `/simulation/search`) all read the same rows instead of
    # two copies of the same content that could drift apart.
    seed_simulation_sources(db, tenant_id, mock_data_dir)

    connectors: list[SourceConnector] = [
        MockDriveConnector(db, tenant_id),
        # Calls before email: jobs process roughly in enqueue order (§13.1), not content
        # chronology, so for pairs where a call and an email both land on the same fact
        # (e.g. Globex: the customer's call statement should out-authority the AE's later
        # internal-email restatement per §7.3 R2), the higher-signal source needs to reach
        # `resolve_object_state` first.
        MockCallConnector(db, tenant_id),
        MockEmailConnector(db, tenant_id, directory),
        MockSlackSeedConnector(db, tenant_id, channel_stage_map),
    ]

    counts = {
        "sources_created": 0,
        "sources_skipped": 0,
        "jobs_enqueued": 0,
        "consent_rejected": 0,
    }
    for connector in connectors:
        for raw in connector.fetch(None):
            try:
                normalized = connector.normalize(raw)
            except ConsentNotGivenError:
                counts["consent_rejected"] += 1
                continue
            _, was_new = ingest_source(db, queue, tenant_id, normalized)
            if was_new:
                counts["sources_created"] += 1
                counts["jobs_enqueued"] += 1
            else:
                counts["sources_skipped"] += 1

    return counts
