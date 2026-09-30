"""JobQueue interface (§13.2)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

MAX_ATTEMPTS = 3


@dataclass
class ClaimedJob:
    id: uuid.UUID
    job_type: str
    payload: dict[str, Any]
    attempts: int
    idempotency_key: str


class JobQueue(Protocol):
    def enqueue(self, job_type: str, payload: dict[str, Any], idempotency_key: str) -> uuid.UUID:
        """Insert a job. Idempotent: re-enqueuing the same key returns the existing job id."""
        ...

    def claim(self, job_types: list[str] | None = None) -> ClaimedJob | None:
        """Claim one runnable job (SKIP LOCKED), or None if the queue is empty."""
        ...

    def ack(self, job_id: uuid.UUID) -> None:
        """Mark a claimed job done."""
        ...

    def fail(self, job_id: uuid.UUID, error: str, retry_after: float | None = None) -> None:
        """Record a failure. Requeues with backoff (or `retry_after` seconds if given —
        e.g. an LLM provider's 429 Retry-After), or moves to `poison` at MAX_ATTEMPTS."""
        ...

    def reclaim_stale(self, timeout_seconds: int) -> int:
        """Reset any job stuck in `running` for longer than `timeout_seconds` back to
        `queued`. A worker that crashes or is killed mid-job (SIGKILL, no graceful
        shutdown) leaves its claimed job in `running` forever — `claim()` only looks at
        `queued` rows, so nothing else ever picks it back up. Found live (2026-10-01):
        restarting the worker process several times in one session orphaned two jobs this
        way; they sat in `running` for hours until reset by hand. Returns the count reset."""
        ...
