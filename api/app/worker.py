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


def run_once(
    queue: JobQueue, redis_client: Redis, llm: LLMClient, embedder: EmbeddingClient
) -> bool:
    """Claim and process one job. Returns False if the queue had nothing runnable."""
    job = queue.claim([EXTRACT_JOB_TYPE])
    if job is None:
        return False

    settings = get_settings()
    db = SessionLocal()
    try:
        tenant_id = uuid.UUID(job.payload["tenant_id"])
        source_id = uuid.UUID(job.payload["source_id"])
        try:
            created = process_extraction_job(
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
            logger.info("job %s done: %d context objects created", job.id, created)
    finally:
        db.close()
    return True


def main() -> None:
    settings = get_settings()
    queue = PostgresJobQueue(SessionLocal)
    redis_client = build_redis_client(settings)
    llm = build_llm_client(settings)
    embedder = build_embedding_client(settings)

    logger.info("worker started (model=%s), polling for jobs", settings.llm_model)
    while True:
        did_work = run_once(queue, redis_client, llm, embedder)
        if not did_work:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
