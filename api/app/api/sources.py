"""POST /sources/mock/load — dev-only ingestion of every mock connector (§15, §21 exit
criterion: "POST /sources/mock/load ... produce objects whose evidence spans all verify").

GET /sources/{id} is a dashboard addition (not in §15's original table): the detail panel
(§17.3) needs the full source text to highlight the evidence quote in place. `ContextObjectOut.
source` only carries `{kind, stage, source_ts, provenance}` (§14 redaction fields), never the
body text, so there was no way to fetch it without this endpoint. Same ACL redaction rule as
`context_object_to_out`."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_queue
from app.api.permissions import PUBLIC_PRINCIPAL
from app.core.config import Settings
from app.core.db import get_db
from app.models.orm import Sources
from app.pipeline.load_mock_data import load_mock_data
from app.queue.base import JobQueue
from app.schemas.context import SourceOut

router = APIRouter()


@router.post("/sources/mock/load")
def load_mock(
    db: Session = Depends(get_db),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, int]:
    tenant_id = uuid.UUID(settings.tenant_id)
    return load_mock_data(db, queue, tenant_id, Path(settings.mock_data_dir))


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(
    source_id: uuid.UUID,
    principal: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> SourceOut:
    source = db.get(Sources, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    visible = bool(source.acl) and (
        PUBLIC_PRINCIPAL in source.acl or set(source.acl) & set(principal or [])
    )
    return SourceOut(
        id=source.id,
        kind=source.kind,
        stage=source.stage,
        source_ts=source.source_ts,
        text=source.text if visible else None,
        provenance=source.provenance if visible else None,
    )
