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
from app.queue.base import JobQueue


def load_mock_data(
    db: Session, queue: JobQueue, tenant_id: uuid.UUID, mock_data_dir: Path
) -> dict[str, int]:
    directory_path = mock_data_dir / "directory.json"
    load_directory(db, tenant_id, directory_path)

    directory = SqlAlchemyPeopleDirectory(db)
    channel_stage_map = load_slack_channel_stage_map(directory_path)

    connectors: list[SourceConnector] = [
        MockDriveConnector(mock_data_dir / "drive"),
        MockEmailConnector(mock_data_dir / "email", directory, tenant_id),
        MockCallConnector(mock_data_dir / "calls"),
        MockSlackSeedConnector(mock_data_dir / "slack" / "seed.json", channel_stage_map),
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
