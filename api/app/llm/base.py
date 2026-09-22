"""LLMClient interface (§11)."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMClient(Protocol):
    def extract(self, schema: type[SchemaT], system: str, content: str) -> SchemaT:
        """One extraction call. Temperature 0. Structured output validated against `schema`."""
        ...

    def judge(self, schema: type[SchemaT], prompt: str) -> SchemaT:
        """LLM-as-judge call (equivalence/generalization checks, §10.1). Same guarantees."""
        ...


class LLMValidationError(RuntimeError):
    """Raised when the model's output fails Pydantic validation twice (initial + one retry)."""


class LLMRateLimitedError(RuntimeError):
    """Raised on HTTP 429. Carries `retry_after` seconds when the provider sent one, so the
    JobQueue backoff (§13.2) can honor it via `run_after` instead of a fixed exponential step."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
