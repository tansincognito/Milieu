"""EmbeddingClient interface (§11)."""

from __future__ import annotations

from typing import Protocol


class EmbeddingClient(Protocol):
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
