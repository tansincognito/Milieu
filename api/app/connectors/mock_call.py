"""MockCallConnector: reads `simulation_seed_sources` (kind='call'), one row per transcript,
seeded from `mock-data/calls/*.txt` by `app.pipeline.seed_simulation_sources` -- the same
rows `/connections/call/sync` and `/simulation/search` already read.

One source per transcript (dispatch scope decision — see CallProvenance docstring), so
the extractor gets full conversational context. Consent is required at ingestion (§6.2:
"Calls without consent.given = true are rejected"); it's parsed from a leading metadata
header of `# key: value` lines (mock-data convention, since there's no real call system).

Stage defaults to `sales` (§3: "Default sales") — the MVP mock data has no CS-run
onboarding kickoff call, so that branch isn't exercised here.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import RawSource
from app.models.orm import SimulationSeedSources
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

    def __init__(self, db: Session, tenant_id: uuid.UUID) -> None:
        self._db = db
        self._tenant_id = tenant_id

    def fetch(self, since: datetime | None) -> Iterable[RawSource]:
        rows = self._db.execute(
            select(SimulationSeedSources)
            .where(
                SimulationSeedSources.tenant_id == self._tenant_id,
                SimulationSeedSources.kind == "call",
            )
            .order_by(SimulationSeedSources.external_id)
        ).scalars()
        for row in rows:
            raw_text = row.payload["transcript"]
            meta, body = _parse_header(raw_text)
            yield RawSource(
                kind="call",
                external_id=meta.get("call_id", row.external_id),
                payload={
                    "source_id": str(row.id),
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
                transcript_path=f"simulation_seed_sources:{raw.payload['source_id']}",
                consent=CallConsent(
                    given=True,
                    by=meta.get("consent_by", "unknown"),
                    at=datetime.fromisoformat(meta["consent_at"]),
                ),
            ),
        )
