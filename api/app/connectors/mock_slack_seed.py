"""MockSlackSeedConnector: `mock-data/slack/seed.json`.

One source per message. Stage comes from a channel -> stage config map (§3), not from
the directory; `actor_role` is resolved later in the pipeline from `provenance.author_id`
via the people directory. Unmapped channels get a null stage (still ingested, excluded
from handoffs per §3).

This is distinct from the real `SlackConnector` (Events API, §12) — that's the next
dispatch. This connector only replays the dev seed file through `/sources/mock/load`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from app.connectors.base import RawSource
from app.schemas.sources import NormalizedSource, SlackProvenance, SourceKind, Stage


class MockSlackSeedConnector:
    kind: SourceKind = "slack"

    def __init__(self, seed_path: Path, channel_stage_map: dict[str, Stage | None]) -> None:
        self._seed_path = seed_path
        self._channel_stage_map = channel_stage_map

    def fetch(self, since: datetime | None) -> Iterable[RawSource]:
        seed = json.loads(self._seed_path.read_text(encoding="utf-8"))
        workspace_id = seed["workspace_id"]
        for channel in seed["channels"]:
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
