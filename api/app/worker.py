"""Single-loop worker process (§13.2/§20): `python -m app.worker`."""

from __future__ import annotations

import logging
import time
import uuid

from redis import Redis

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.factories import build_embedding_client, build_llm_client, build_redis_client
from app.embedding.base import EmbeddingClient
from app.llm.base import LLMClient, LLMRateLimitedError
from app.pipeline.ingest import EXTRACT_JOB_TYPE
from app.pipeline.process import process_extraction_job
from app.queue.base import JobQueue
from app.queue.postgres import PostgresJobQueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("app.worker")

POLL_INTERVAL_SECONDS = 1.0
# Comfortably longer than the LLM client's own 180s request timeout, so this never reclaims
# a job that's still genuinely in flight -- only one truly abandoned by a dead worker.
STALE_JOB_TIMEOUT_SECONDS = 600
# Floor on the gap between the START of one job attempt and the next, regardless of whether
# the previous one succeeded or failed. Found live (2026-10-01, switching to Groq): with no
# pacing here, a fast provider's round trip (Groq: ~0.3-0.6s) means this loop can fire five
# job attempts within half a second the moment several jobs are queued -- and burst straight
# through a real per-account rate limit that a single request would never have hit. OpenRouter's
# own latency happened to make this a non-issue before; it should not have been implicit.
#
# This floor alone is NOT sufficient for Groq: its real constraint is an 8000-token/minute
# budget (not request count). Budget-aware pacing (waiting out the window when the remaining
# balance is thin) lives in `GroqLLMClient._wait_for_budget` instead of here, because it has
# to guard *every* outbound request, not just the gap between job claims -- a job's own
# internal validation-retry (`GroqLLMClient._call`) fires a second request with no gap
# otherwise, and that pair alone was enough to 429 and eventually poison jobs even with this
# floor in place and a worker-loop-level version of this check between claims (found live,
# 2026-10-01: pacing only between claims never saw the real back-to-back pair, since both
# requests belonged to the same job).
MIN_JOB_INTERVAL_SECONDS = 2.0


def run_once(
    queue: JobQueue, redis_client: Redis, llm: LLMClient, embedder: EmbeddingClient
) -> bool:
    """Claim and process one job. Returns False if the queue had nothing runnable."""
    job = queue.claim([EXTRACT_JOB_TYPE])
    if job is None:
        return False

    settings = get_settings()
    try:
        db = SessionLocal()
    except Exception as exc:
        # A transient DB blip (e.g. Postgres mid-restart) must fail this job and keep the
        # worker loop alive, not crash the whole process -- found live (2026-10-01): a brief
        # "database system is in recovery mode" error here killed the worker with the job
        # already claimed and no `queue.fail` call, leaving it stuck in `running` until
        # `reclaim_stale`'s 600s timeout. `queue.fail` opens its own session, independent of
        # the one that just failed here, so it's safe to call without `db`.
        queue.fail(job.id, repr(exc))
        logger.exception("job %s failed: could not open DB session (attempt %d)", job.id, job.attempts)
        return True

    try:
        tenant_id = uuid.UUID(job.payload["tenant_id"])
        source_id = uuid.UUID(job.payload["source_id"])
        try:
            result = process_extraction_job(
                db, redis_client, llm, embedder, settings, tenant_id, source_id
            )
        except LLMRateLimitedError as exc:
            db.rollback()
            queue.fail(job.id, str(exc), retry_after=exc.retry_after)
            logger.warning("job %s rate-limited (attempt %d): %s", job.id, job.attempts, exc)
        except Exception as exc:  # any failure here must fail the job, not crash the worker loop
            db.rollback()
            queue.fail(job.id, repr(exc))
            logger.exception("job %s failed (attempt %d)", job.id, job.attempts)
        else:
            queue.ack(job.id)
            logger.info(
                "job %s done: %d context objects created, %d rejected (evidence-span)",
                job.id,
                result.created,
                result.rejected,
            )
    finally:
        db.close()
    return True


def main() -> None:
    settings = get_settings()
    queue = PostgresJobQueue(SessionLocal)
    redis_client = build_redis_client(settings)
    llm = build_llm_client(settings)
    embedder = build_embedding_client(settings)

    reclaimed = queue.reclaim_stale(STALE_JOB_TIMEOUT_SECONDS)
    if reclaimed:
        logger.warning(
            "reclaimed %d job(s) stuck in 'running' (a previous worker died mid-job)", reclaimed
        )

    logger.info("worker started (model=%s), polling for jobs", settings.llm_model)
    while True:
        did_work = run_once(queue, redis_client, llm, embedder)
        if did_work:
            time.sleep(MIN_JOB_INTERVAL_SECONDS)
        else:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
