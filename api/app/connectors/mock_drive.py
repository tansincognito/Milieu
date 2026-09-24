"""MockDriveConnector: markdown files under `mock-data/drive/<stage>/*.md`.

Stage comes from the top-level folder (§3): `drive/sales` -> `sales`, etc. Each markdown
heading section (§6.2) becomes its own source record.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from app.connectors.base import RawSource
from app.connectors.markdown import split_markdown_sections
from app.schemas.sources import DriveProvenance, NormalizedSource, SourceKind, Stage

# Mock docs can optionally state their own narrative date ("Last updated: 2026-10-10"),
# which — unlike filesystem mtime (checkout time, meaningless for lineage/supersession
# ordering across a fictional §18 scenario timeline) — reflects when the document was
# actually "last modified" in the story. Falls back to mtime when absent.
_LAST_UPDATED_RE = re.compile(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)

FOLDER_TO_STAGE: dict[str, Stage] = {
    "sales": "sales",
    "product": "product",
    "engineering": "engineering",
    "customer_success": "customer_success",
}


class MockDriveConnector:
    kind: SourceKind = "drive"

    def __init__(self, root: Path) -> None:
        self._root = root

    def fetch(self, since: datetime | None) -> Iterable[RawSource]:
        for path in sorted(self._root.rglob("*.md")):
            relative = path.relative_to(self._root)
            text = path.read_text(encoding="utf-8")
            last_updated = _LAST_UPDATED_RE.search(text)
            if last_updated:
                mtime = datetime.fromisoformat(last_updated.group(1)).replace(tzinfo=UTC)
            else:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            for section in split_markdown_sections(text):
                yield RawSource(
                    kind="drive",
                    external_id=f"{relative.as_posix()}#{section.index}",
                    payload={
                        "path": relative.as_posix(),
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
