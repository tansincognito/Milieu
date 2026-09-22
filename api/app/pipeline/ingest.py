"""Ingest: normalize -> content_hash dedupe -> enqueue extraction job (§13.1, up to enqueue).

Idempotent: re-ingesting the same (tenant, kind, external_id, content_hash) is a no-op that
returns the existing source id without re-enqueueing.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.orm import Sources
from app.queue.base import JobQueue
from app.schemas.sources import NormalizedSource

EXTRACT_JOB_TYPE = "extract_source"


def ingest_source(
    db: Session, queue: JobQueue, tenant_id: uuid.UUID, normalized: NormalizedSource
) -> tuple[uuid.UUID, bool]:
    """Returns (source_id, was_new)."""
    existing = (
        db.query(Sources)
        .filter(
            Sources.tenant_id == tenant_id,
            Sources.kind == normalized.kind,
            Sources.external_id == normalized.external_id,
            Sources.content_hash == normalized.content_hash,
        )
        .first()
    )
    if existing is not None:
        return existing.id, False

    source = Sources(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind=normalized.kind,
        external_id=normalized.external_id,
        version=normalized.version,
        content_hash=normalized.content_hash,
        stage=normalized.stage,
        acl=normalized.acl,
        provenance=normalized.provenance.model_dump(mode="json"),
        text=normalized.text,
        source_ts=normalized.source_ts,
    )
    db.add(source)
    db.commit()

    idempotency_key = f"{EXTRACT_JOB_TYPE}:{source.id}:{normalized.content_hash}"
    queue.enqueue(
        job_type=EXTRACT_JOB_TYPE,
        payload={"source_id": str(source.id), "tenant_id": str(tenant_id)},
        idempotency_key=idempotency_key,
    )
    return source.id, True
