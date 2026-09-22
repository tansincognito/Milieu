"""fastembed EmbeddingClient implementation (local, no API key required)."""

from __future__ import annotations

from fastembed import TextEmbedding


class FastEmbedClient:
    dimensions = 384

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vec.tolist() for vec in self._model.embed(texts)]
