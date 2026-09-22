"""MockEmailConnector: JSON thread files under `mock-data/email/*.json`.

One source per message. Stage comes from the sender's directory lookup (§3.1), not from
config — an internal sender takes their team's stage, a known customer domain resolves to
`sales`, and an unknown sender gets a null stage (ingested, routed to review upstream).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.connectors.base import RawSource
from app.directory.resolve import PeopleDirectory, resolve_person
from app.schemas.sources import EmailProvenance, NormalizedSource


class MockEmailConnector:
    kind: Literal["email"] = "email"

    def __init__(self, root: Path, directory: PeopleDirectory, tenant_id: uuid.UUID) -> None:
        self._root = root
        self._directory = directory
        self._tenant_id = tenant_id

    def fetch(self, since: datetime | None) -> Iterable[RawSource]:
        for path in sorted(self._root.glob("*.json")):
            thread = json.loads(path.read_text(encoding="utf-8"))
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
