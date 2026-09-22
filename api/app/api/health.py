"""GET /health — DB, Redis, queue depth (§15)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_redis
from app.core.db import get_db

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db), redis_client: Redis = Depends(get_redis)) -> JSONResponse:
    db_ok = True
    queue_depth: int | None = None
    try:
        db.execute(text("SELECT 1"))
        queue_depth = db.execute(
            text("SELECT count(*) FROM processing_jobs WHERE status = 'queued'")
        ).scalar_one()
    except Exception:  # noqa: BLE001 — a health check must report ANY DB failure, not crash
        db_ok = False

    redis_ok = True
    try:
        redis_client.ping()
    except Exception:  # noqa: BLE001 — same: report any Redis failure rather than 500
        redis_ok = False

    body = {"db": db_ok, "redis": redis_ok, "queue_depth": queue_depth}
    return JSONResponse(status_code=200 if db_ok and redis_ok else 503, content=body)
