"""JobQueue claim/retry/poison against real compose Postgres (§13.2)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.db import engine
from app.queue.base import MAX_ATTEMPTS
from app.queue.postgres import PostgresJobQueue

pytestmark = pytest.mark.integration

SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture
def queue() -> PostgresJobQueue:
    return PostgresJobQueue(SessionFactory)


def _cleanup(job_type_prefix: str) -> None:
    with SessionFactory() as db:
        db.execute(
            text("DELETE FROM processing_jobs WHERE job_type LIKE :p"),
            {"p": f"{job_type_prefix}%"},
        )
        db.commit()


def test_enqueue_is_idempotent_on_key(queue: PostgresJobQueue) -> None:
    job_type = f"test_idempotent_{uuid.uuid4().hex[:8]}"
    key = f"{job_type}:key1"
    try:
        id1 = queue.enqueue(job_type, {"n": 1}, key)
        id2 = queue.enqueue(job_type, {"n": 2}, key)
        assert id1 == id2
    finally:
        _cleanup(job_type)


def test_claim_uses_skip_locked_and_marks_running(queue: PostgresJobQueue) -> None:
    job_type = f"test_claim_{uuid.uuid4().hex[:8]}"
    try:
        queue.enqueue(job_type, {"n": 1}, f"{job_type}:a")
        job = queue.claim([job_type])
        assert job is not None
        assert job.job_type == job_type
        assert job.attempts == 1

        # Nothing else is queued for this job_type, so a second claim finds nothing.
        assert queue.claim([job_type]) is None
    finally:
        _cleanup(job_type)


def test_fail_requeues_with_backoff_until_poison(queue: PostgresJobQueue) -> None:
    job_type = f"test_poison_{uuid.uuid4().hex[:8]}"
    try:
        queue.enqueue(job_type, {"n": 1}, f"{job_type}:a")

        for attempt in range(1, MAX_ATTEMPTS + 1):
            job = queue.claim([job_type])
            assert job is not None, f"expected a claimable job on attempt {attempt}"
            assert job.attempts == attempt
            # retry_after=-1 (one second in the past) guarantees the job is immediately
            # claimable again regardless of any clock skew between this process and Postgres.
            queue.fail(job.id, f"boom attempt {attempt}", retry_after=-1)

        with SessionFactory() as db:
            row = db.execute(
                text("SELECT status, attempts, last_error FROM processing_jobs WHERE job_type = :t"),
                {"t": job_type},
            ).mappings().one()
            assert row["status"] == "poison"
            assert row["attempts"] == MAX_ATTEMPTS
            assert "boom attempt 3" in row["last_error"]

        # A poisoned job is never claimable again.
        assert queue.claim([job_type]) is None
    finally:
        _cleanup(job_type)


def test_ack_marks_done(queue: PostgresJobQueue) -> None:
    job_type = f"test_ack_{uuid.uuid4().hex[:8]}"
    try:
        queue.enqueue(job_type, {"n": 1}, f"{job_type}:a")
        job = queue.claim([job_type])
        assert job is not None
        queue.ack(job.id)

        with SessionFactory() as db:
            status = db.execute(
                text("SELECT status FROM processing_jobs WHERE id = :id"), {"id": job.id}
            ).scalar_one()
            assert status == "done"
    finally:
        _cleanup(job_type)


def test_reclaim_stale_requeues_an_abandoned_running_job(queue: PostgresJobQueue) -> None:
    """A worker that's SIGKILLed mid-job (not a graceful shutdown) leaves its claimed job in
    `running` forever -- `claim()` only ever looks at `queued` rows. Found live (2026-10-01):
    restarting the worker process mid-session orphaned two real jobs this way. Backdates
    `updated_at` directly (not a real sleep) to prove the age check, not the clock."""
    job_type = f"test_reclaim_{uuid.uuid4().hex[:8]}"
    try:
        queue.enqueue(job_type, {"n": 1}, f"{job_type}:a")
        job = queue.claim([job_type])
        assert job is not None

        with SessionFactory() as db:
            db.execute(
                text("UPDATE processing_jobs SET updated_at = now() - interval '1 hour' WHERE id = :id"),
                {"id": job.id},
            )
            db.commit()

        reclaimed = queue.reclaim_stale(timeout_seconds=600)
        assert reclaimed == 1

        with SessionFactory() as db:
            status = db.execute(
                text("SELECT status FROM processing_jobs WHERE id = :id"), {"id": job.id}
            ).scalar_one()
            assert status == "queued"

        # And it's claimable again -- the actual point of resetting it.
        reclaimed_job = queue.claim([job_type])
        assert reclaimed_job is not None
        assert reclaimed_job.id == job.id
    finally:
        _cleanup(job_type)


def test_reclaim_stale_leaves_a_genuinely_in_flight_job_alone(queue: PostgresJobQueue) -> None:
    """The whole point of the timeout: a job claimed moments ago (still legitimately being
    worked on) must NOT be reset out from under its worker."""
    job_type = f"test_reclaim_live_{uuid.uuid4().hex[:8]}"
    try:
        queue.enqueue(job_type, {"n": 1}, f"{job_type}:a")
        job = queue.claim([job_type])
        assert job is not None

        reclaimed = queue.reclaim_stale(timeout_seconds=600)
        assert reclaimed == 0

        with SessionFactory() as db:
            status = db.execute(
                text("SELECT status FROM processing_jobs WHERE id = :id"), {"id": job.id}
            ).scalar_one()
            assert status == "running"
    finally:
        _cleanup(job_type)
