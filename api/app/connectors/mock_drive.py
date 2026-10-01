"""MockDriveConnector: reads `simulation_seed_sources` (kind='drive'), one row per document,
seeded from `mock-data/drive/<stage>/*.md` by `app.pipeline.seed_simulation_sources` -- the
same rows `/connections/drive/sync` and `/simulation/search` already read.

Stage comes from the top-level folder (§3): `drive/sales` -> `sales`, etc. Each markdown
heading section (§6.2) becomes its own source record.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import RawSource
from app.connectors.markdown import split_markdown_sections
from app.models.orm import SimulationSeedSources
from app.schemas.sources import DriveProvenance, NormalizedSource, SourceKind, Stage

# Mock docs can optionally state their own narrative date ("Last updated: 2026-10-10"),
# which — unlike a DB row's `created_at` (import time, meaningless for lineage/supersession
# ordering across a fictional §18 scenario timeline) — reflects when the document was
# actually "last modified" in the story. Falls back to `created_at` when absent.
_LAST_UPDATED_RE = re.compile(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)

FOLDER_TO_STAGE: dict[str, Stage] = {
    "sales": "sales",
    "product": "product",
    "engineering": "engineering",
    "customer_success": "customer_success",
    "leadership": "leadership",
}


class MockDriveConnector:
    kind: SourceKind = "drive"

    def __init__(self, db: Session, tenant_id: uuid.UUID) -> None:
        self._db = db
        self._tenant_id = tenant_id

    def fetch(self, since: datetime | None) -> Iterable[RawSource]:
        rows = self._db.execute(
            select(SimulationSeedSources)
            .where(
                SimulationSeedSources.tenant_id == self._tenant_id,
                SimulationSeedSources.kind == "drive",
            )
            .order_by(SimulationSeedSources.external_id)
        ).scalars()
        for row in rows:
            path = row.payload["path"]
            text = row.payload["content"]
            last_updated = _LAST_UPDATED_RE.search(text)
            if last_updated:
                mtime = datetime.fromisoformat(last_updated.group(1)).replace(tzinfo=UTC)
            else:
                mtime = row.created_at
            for section in split_markdown_sections(text):
                yield RawSource(
                    kind="drive",
                    external_id=f"{path}#{section.index}",
                    payload={
                        "path": path,
                        "heading": section.heading,
                        "section_index": section.index,
                        "text": section.text,
                        "modified_ts": mtime.isoformat(),
                    },
                )

    def normalize(self, raw: RawSource) -> NormalizedSource:
        path = raw.payload["path"]
        stage_folder = Path(path).parts[0]
        stage = FOLDER_TO_STAGE.get(stage_folder)
        text = raw.payload["text"]
        modified_ts = datetime.fromisoformat(raw.payload["modified_ts"])

        return NormalizedSource(
            kind="drive",
            external_id=raw.external_id,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=text,
            source_ts=modified_ts,
            stage=stage,
            acl=["*"],
            provenance=DriveProvenance(
                document_id=path,
                path=path,
                section_heading=raw.payload["heading"],
                section_index=raw.payload["section_index"],
                modified_ts=modified_ts,
            ),
        )
