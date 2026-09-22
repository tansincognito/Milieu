"""SourceConnector interface (§6.1)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.schemas.sources import NormalizedSource, SourceKind


@dataclass
class RawSource:
    """Kind-specific raw payload before normalization."""

    kind: SourceKind
    external_id: str
    payload: dict[str, Any] = field(default_factory=dict)


class SourceConnector(Protocol):
    kind: SourceKind

    def fetch(self, since: datetime | None) -> Iterable[RawSource]: ...

    def normalize(self, raw: RawSource) -> NormalizedSource: ...
