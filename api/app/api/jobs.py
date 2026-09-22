"""GET /jobs/stats — queue observability (§13.2: "Job status is visible in the dashboard
(queued / running / done / failed / poison counts)")."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.orm import JOB_STATUSES

router = APIRouter()


@router.get("/jobs/stats")
def job_stats(db: Session = Depends(get_db)) -> dict[str, int]:
    rows = db.execute(text("SELECT status, count(*) FROM processing_jobs GROUP BY status")).all()
    counts = {status: 0 for status in JOB_STATUSES}
    counts.update({status: count for status, count in rows})
    return counts
