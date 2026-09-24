"""Lineage (§8): `derived_from` links a downstream-stage object back to the upstream-stage
object it descends from — same `subject_key`, later `valid_from`. This is the link the
(Day 3) handoff validator walks; traversal here uses recursive CTEs, no graph DB.

The stage pairs below mirror the `from_stage`/`to_stage` pairs §9 defines for Context
Contracts. Contracts themselves aren't seeded until Day 3, but the pairs are stable spec
knowledge, so lineage (explicitly Day 2a scope) doesn't need to wait on the contracts
table — this constant is the part of §9 that Day 3's contract loader will supersede with
configurable data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.orm import ContextObjects, ContextRelations

UPSTREAM_TO_DOWNSTREAM: tuple[tuple[str, str], ...] = (
    ("sales", "product"),
    ("product", "engineering"),
    ("sales", "customer_success"),
    ("engineering", "sales"),
    ("engineering", "customer_success"),
)


def _link_exists(db: Session, from_id: uuid.UUID, to_id: uuid.UUID) -> bool:
    return (
        db.query(ContextRelations)
        .filter(
            ContextRelations.from_id == from_id,
            ContextRelations.to_id == to_id,
            ContextRelations.relation == "derived_from",
        )
        .first()
        is not None
    )


def _add_derived_from(db: Session, downstream: ContextObjects, upstream: ContextObjects) -> None:
    if downstream.id == upstream.id or _link_exists(db, downstream.id, upstream.id):
        return
    db.add(
        ContextRelations(
            id=uuid.uuid4(),
            from_id=downstream.id,
            to_id=upstream.id,
            relation="derived_from",
            created_by="system:lineage",
            confidence=None,
            created_at=datetime.now(UTC),
            resolved_at=datetime.now(UTC),
        )
    )


def create_lineage_links(db: Session, tenant_id: uuid.UUID, new_obj: ContextObjects) -> None:
    """Called once per newly-persisted object. Links are created bidirectionally because
    ingestion/processing order does not track real-world chronology (sources are enqueued
    per-connector, not by `source_ts`) — an upstream object can easily be processed after
    a downstream one that already qualifies for a `derived_from` edge to it."""
    if new_obj.stage is None:
        return

    for from_stage, to_stage in UPSTREAM_TO_DOWNSTREAM:
        if new_obj.stage == to_stage:
            upstream_candidates = (
                db.query(ContextObjects)
                .filter(
                    ContextObjects.tenant_id == tenant_id,
                    ContextObjects.entity_id == new_obj.entity_id,
                    ContextObjects.subject_key == new_obj.subject_key,
                    ContextObjects.stage == from_stage,
                    ContextObjects.valid_from < new_obj.valid_from,
                    ContextObjects.id != new_obj.id,
                )
                .all()
            )
            for upstream in upstream_candidates:
                _add_derived_from(db, new_obj, upstream)

        if new_obj.stage == from_stage:
            downstream_candidates = (
                db.query(ContextObjects)
                .filter(
                    ContextObjects.tenant_id == tenant_id,
                    ContextObjects.entity_id == new_obj.entity_id,
                    ContextObjects.subject_key == new_obj.subject_key,
                    ContextObjects.stage == to_stage,
                    ContextObjects.valid_from > new_obj.valid_from,
                    ContextObjects.id != new_obj.id,
                )
                .all()
            )
            for downstream in downstream_candidates:
                _add_derived_from(db, downstream, new_obj)


_UPSTREAM_CTE = """
WITH RECURSIVE chain AS (
    SELECT id, from_id, to_id, 0 AS depth
    FROM context_relations
    WHERE relation = 'derived_from' AND from_id = :context_id
    UNION ALL
    SELECT r.id, r.from_id, r.to_id, chain.depth + 1
    FROM context_relations r
    JOIN chain ON r.from_id = chain.to_id
    WHERE r.relation = 'derived_from' AND chain.depth < 20
)
SELECT c.id AS object_id, c.type, c.subject_key, c.stage, c.content, c.status,
       c.authority, c.valid_from, chain.depth
FROM chain
JOIN context_objects c ON c.id = chain.to_id
ORDER BY chain.depth ASC
"""

_DOWNSTREAM_CTE = """
WITH RECURSIVE chain AS (
    SELECT id, from_id, to_id, 0 AS depth
    FROM context_relations
    WHERE relation = 'derived_from' AND to_id = :context_id
    UNION ALL
    SELECT r.id, r.from_id, r.to_id, chain.depth + 1
    FROM context_relations r
    JOIN chain ON r.to_id = chain.from_id
    WHERE r.relation = 'derived_from' AND chain.depth < 20
)
SELECT c.id AS object_id, c.type, c.subject_key, c.stage, c.content, c.status,
       c.authority, c.valid_from, chain.depth
FROM chain
JOIN context_objects c ON c.id = chain.from_id
ORDER BY chain.depth ASC
"""


def upstream_chain(db: Session, context_id: uuid.UUID) -> list[dict]:
    """Walks `derived_from` edges from `context_id` back to root evidence (§8: 'the
    dashboard shows the chain from the current object back to root evidence')."""
    rows = db.execute(text(_UPSTREAM_CTE), {"context_id": context_id}).mappings().all()
    return [dict(row) for row in rows]


def downstream_chain(db: Session, context_id: uuid.UUID) -> list[dict]:
    """Walks `derived_from` edges forward from `context_id` to whatever descends from it."""
    rows = db.execute(text(_DOWNSTREAM_CTE), {"context_id": context_id}).mappings().all()
    return [dict(row) for row in rows]
