"""Postgres-backed JobQueue (§13.2): SELECT ... FOR UPDATE SKIP LOCKED, idempotency key,
max 3 attempts, poison after that."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.queue.base import MAX_ATTEMPTS, ClaimedJob


class PostgresJobQueue:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def enqueue(self, job_type: str, payload: dict[str, Any], idempotency_key: str) -> uuid.UUID:
        import json

        with self._session_factory() as db:
            row = db.execute(
                text(
                    """
                    INSERT INTO processing_jobs (id, job_type, idempotency_key, payload, status)
                    VALUES (:id, :job_type, :idempotency_key, cast(:payload AS jsonb), 'queued')
                    ON CONFLICT (idempotency_key) DO UPDATE
                        SET idempotency_key = processing_jobs.idempotency_key
                    RETURNING id
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "job_type": job_type,
                    "idempotency_key": idempotency_key,
                    "payload": json.dumps(payload),
                },
            ).one()
            db.commit()
            return row.id

    def claim(self, job_types: list[str] | None = None) -> ClaimedJob | None:
        with self._session_factory() as db:
            query = """
                SELECT id, job_type, payload, attempts, idempotency_key
                FROM processing_jobs
                WHERE status = 'queued' AND run_after <= now()
            """
            params: dict[str, Any] = {}
            if job_types:
                query += " AND job_type = ANY(:job_types)"
                params["job_types"] = job_types
            query += " ORDER BY run_after ASC LIMIT 1 FOR UPDATE SKIP LOCKED"

            row = db.execute(text(query), params).mappings().first()
            if row is None:
                return None

            db.execute(
                text(
                    "UPDATE processing_jobs SET status = 'running', attempts = attempts + 1, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": row["id"]},
            )
            db.commit()
            return ClaimedJob(
                id=row["id"],
                job_type=row["job_type"],
                payload=row["payload"],
                attempts=row["attempts"] + 1,
                idempotency_key=row["idempotency_key"],
            )

    def ack(self, job_id: uuid.UUID) -> None:
        with self._session_factory() as db:
            db.execute(
                text("UPDATE processing_jobs SET status = 'done', updated_at = now() WHERE id = :id"),
                {"id": job_id},
            )
            db.commit()

    def fail(self, job_id: uuid.UUID, error: str) -> None:
        with self._session_factory() as db:
            attempts = db.execute(
                text("SELECT attempts FROM processing_jobs WHERE id = :id"), {"id": job_id}
            ).scalar_one()

            if attempts >= MAX_ATTEMPTS:
                db.execute(
                    text(
                        "UPDATE processing_jobs SET status = 'poison', last_error = :error, "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"id": job_id, "error": error},
                )
            else:
                backoff = timedelta(seconds=2**attempts)
                run_after = datetime.now(UTC) + backoff
                db.execute(
                    text(
                        "UPDATE processing_jobs SET status = 'queued', last_error = :error, "
                        "run_after = :run_after, updated_at = now() WHERE id = :id"
                    ),
                    {"id": job_id, "error": error, "run_after": run_after},
                )
            db.commit()
