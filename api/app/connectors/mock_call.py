"""MockCallConnector: speaker-labelled transcripts under `mock-data/calls/*.txt`.

One source per transcript (dispatch scope decision — see CallProvenance docstring), so
the extractor gets full conversational context. Consent is required at ingestion (§6.2:
"Calls without consent.given = true are rejected"); it's parsed from a leading metadata
header of `# key: value` lines (mock-data convention, since there's no real call system).

Stage defaults to `sales` (§3: "Default sales") — the MVP mock data has no CS-run
onboarding kickoff call, so that branch isn't exercised here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from app.connectors.base import RawSource
from app.schemas.sources import CallConsent, CallProvenance, NormalizedSource, SourceKind


class ConsentNotGivenError(RuntimeError):
    """Raised when a call transcript lacks consent.given = true (§6.2)."""


def _parse_header(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines(keepends=True)
    meta: dict[str, str] = {}
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        key, _, value = lines[i][1:].partition(":")
        meta[key.strip()] = value.strip()
        i += 1
    # skip the blank line(s) separating the header from the transcript body
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    body = "".join(lines[i:])
    return meta, body


class MockCallConnector:
    kind: SourceKind = "call"

    def __init__(self, root: Path) -> None:
        self._root = root

    def fetch(self, since: datetime | None) -> Iterable[RawSource]:
        for path in sorted(self._root.glob("*.txt")):
            raw_text = path.read_text(encoding="utf-8")
            meta, body = _parse_header(raw_text)
            yield RawSource(
                kind="call",
                external_id=meta.get("call_id", path.stem),
                payload={
                    "path": str(path),
                    "meta": meta,
                    "body": body,
                },
            )

    def normalize(self, raw: RawSource) -> NormalizedSource:
        meta = raw.payload["meta"]
        body = raw.payload["body"]
        consent_given = meta.get("consent_given", "false").strip().lower() == "true"
        if not consent_given:
            raise ConsentNotGivenError(f"call {raw.external_id} has no recorded consent")

        return NormalizedSource(
            kind="call",
            external_id=raw.external_id,
            content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            text=body,
            source_ts=datetime.fromisoformat(meta["date"]),
            stage="sales",
            acl=["*"],
            provenance=CallProvenance(
                call_id=raw.external_id,
                transcript_path=raw.payload["path"],
                consent=CallConsent(
                    given=True,
                    by=meta.get("consent_by", "unknown"),
                    at=datetime.fromisoformat(meta["consent_at"]),
                ),
            ),
        )
