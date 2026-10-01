"""MockSlackSeedConnector: reads `simulation_seed_sources` (kind='slack'), one row per
channel, seeded from `mock-data/slack/seed.json` by `app.pipeline.seed_simulation_sources`
-- the same rows `/connections/slack/sync` and `/simulation/search` already read.

One source per message. Stage comes from a channel -> stage config map (§3), not from
the directory; `actor_role` is resolved later in the pipeline from `provenance.author_id`
via the people directory. Unmapped channels get a null stage (still ingested, excluded
from handoffs per §3).

This is distinct from the real `SlackConnector` (Events API, §12) — that's the next
dispatch. This connector only replays the seeded data through `/sources/mock/load`.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import RawSource
from app.models.orm import SimulationSeedSources
from app.schemas.sources import NormalizedSource, SlackProvenance, SourceKind, Stage


class MockSlackSeedConnector:
    kind: SourceKind = "slack"

    def __init__(
        self, db: Session, tenant_id: uuid.UUID, channel_stage_map: dict[str, Stage | None]
    ) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._channel_stage_map = channel_stage_map

    def fetch(self, since: datetime | None) -> Iterable[RawSource]:
        rows = self._db.execute(
            select(SimulationSeedSources)
            .where(
                SimulationSeedSources.tenant_id == self._tenant_id,
                SimulationSeedSources.kind == "slack",
            )
            .order_by(SimulationSeedSources.external_id)
        ).scalars()
        for row in rows:
            channel = row.payload
            workspace_id = channel["workspace_id"]
            for message in channel["messages"]:
                yield RawSource(
                    kind="slack",
                    external_id=f"{channel['channel_id']}:{message['ts']}",
                    payload={
                        "workspace_id": workspace_id,
                        "channel_id": channel["channel_id"],
                        "channel_name": channel["name"],
                        **message,
                    },
                )

    def normalize(self, raw: RawSource) -> NormalizedSource:
        payload = raw.payload
        text = payload["text"]
        stage = self._channel_stage_map.get(payload["channel_name"])
        ts_seconds = float(payload["ts"].split(".")[0])

        return NormalizedSource(
            kind="slack",
            external_id=raw.external_id,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=text,
            source_ts=datetime.fromtimestamp(ts_seconds, tz=UTC),
            stage=stage,
            acl=["*"],
            provenance=SlackProvenance(
                workspace_id=payload["workspace_id"],
                channel_id=payload["channel_id"],
                message_ts=payload["ts"],
                thread_ts=payload.get("thread_ts"),
                author_id=payload["user"],
            ),
        )
