"""POST /sources/mock/load — dev-only ingestion of every mock connector (§15, §21 exit
criterion: "POST /sources/mock/load ... produce objects whose evidence spans all verify")."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_queue
from app.core.config import Settings
from app.core.db import get_db
from app.pipeline.load_mock_data import load_mock_data
from app.queue.base import JobQueue

router = APIRouter()


@router.post("/sources/mock/load")
def load_mock(
    db: Session = Depends(get_db),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, int]:
    tenant_id = uuid.UUID(settings.tenant_id)
    return load_mock_data(db, queue, tenant_id, Path(settings.mock_data_dir))
