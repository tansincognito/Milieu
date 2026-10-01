"""MockEmailConnector: reads `simulation_seed_sources` (kind='email'), one row per thread,
seeded from `mock-data/email/*.json` by `app.pipeline.seed_simulation_sources` -- the same
rows `/connections/email/sync` and `/simulation/search` already read, so there's one copy of
this data instead of two drifting in and out of sync.

One source per message. Stage comes from the sender's directory lookup (§3.1), not from
config — an internal sender takes their team's stage, a known customer domain resolves to
`sales`, and an unknown sender gets a null stage (ingested, routed to review upstream).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import RawSource
from app.directory.resolve import PeopleDirectory, resolve_person
from app.models.orm import SimulationSeedSources
from app.schemas.sources import EmailProvenance, NormalizedSource, SourceKind


class MockEmailConnector:
    kind: SourceKind = "email"

    def __init__(self, db: Session, tenant_id: uuid.UUID, directory: PeopleDirectory) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._directory = directory

    def fetch(self, since: datetime | None) -> Iterable[RawSource]:
        rows = self._db.execute(
            select(SimulationSeedSources)
            .where(
                SimulationSeedSources.tenant_id == self._tenant_id,
                SimulationSeedSources.kind == "email",
            )
            .order_by(SimulationSeedSources.external_id)
        ).scalars()
        for row in rows:
            thread = row.payload
            thread_id = thread["thread_id"]
            subject = thread["subject"]
            for message in thread["messages"]:
                yield RawSource(
                    kind="email",
                    external_id=message["message_id"],
                    payload={"thread_id": thread_id, "subject": subject, **message},
                )

    def normalize(self, raw: RawSource) -> NormalizedSource:
        payload = raw.payload
        text = payload["text"]
        resolution = resolve_person(self._directory, self._tenant_id, email=payload["from"])

        return NormalizedSource(
            kind="email",
            external_id=raw.external_id,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=text,
            source_ts=datetime.fromisoformat(payload["date"]),
            stage=resolution.stage,  # type: ignore[arg-type]
            acl=["*"],
            provenance=EmailProvenance(
                thread_id=payload["thread_id"],
                message_id=payload["message_id"],
                **{"from": payload["from"]},
                to=payload.get("to", []),
                cc=payload.get("cc", []),
                subject=payload["subject"],
            ),
        )
