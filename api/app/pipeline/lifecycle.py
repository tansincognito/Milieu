"""Lifecycle, supersession, conflict (§7.3) — the core Day 2a deliverable.

`resolve_object_state` is the entry point: given a newly-persisted context object N, it
finds every currently-`active` sibling sharing (entity_id, type, subject_key) and, per
sibling, routes the pair to dedup (§7.2, compatible slots) or to R1-R4 below (incompatible
slots — a different stance or a conflicting slot value). R5 (human resolution) is not run
here; it lives in the `POST /conflicts/{relation_id}/resolve` review endpoint.

Rules are evaluated in order, first match wins. Every status transition on an *existing*
object writes a `context_versions` row; nothing is ever deleted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.orm import ContextObjects, ContextRelations, ContextVersions
from app.pipeline.dedup import (
    apply_dedup,
    cosine_similarity,
    find_subject_siblings,
    slots_compatible,
)

# §7.3 stance compatibility matrix. Only required/deferred/in_progress have a defined row
# (the spec table gives no row for done/dropped as the *first* stance) — a lookup that
# misses in both orderings has no defined incompatibility and simply doesn't trigger R1,
# falling through to R2-R4 instead.
_STANCE_MATRIX: dict[tuple[str, str], str] = {
    ("required", "required"): "ok",
    ("required", "deferred"): "conflict",
    ("required", "in_progress"): "ok",
    ("required", "done"): "ok",
    ("required", "dropped"): "conflict",
    ("deferred", "required"): "conflict",
    ("deferred", "deferred"): "ok",
    ("deferred", "in_progress"): "conflict",
    ("deferred", "done"): "conflict",
    ("deferred", "dropped"): "ok",
    ("in_progress", "required"): "ok",
    ("in_progress", "deferred"): "conflict",
    ("in_progress", "in_progress"): "ok",
    ("in_progress", "done"): "ok",
    ("in_progress", "dropped"): "conflict",
}


def stances_conflict(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    outcome = _STANCE_MATRIX.get((a, b)) or _STANCE_MATRIX.get((b, a))
    return outcome == "conflict"


def same_author(a: ContextObjects, b: ContextObjects) -> bool:
    """§7.3: 'same author' means the same `people.id`, resolved through the directory."""
    return a.actor_person_id is not None and a.actor_person_id == b.actor_person_id


def is_explicit_correction(new_obj: ContextObjects) -> bool:
    """§7.3: an explicit correction with confidence < 0.7 falls back to R4."""
    extra = (new_obj.attributes or {}).get("extra") or {}
    return bool(extra.get("corrects")) and float(new_obj.confidence) >= 0.7


def _transition(db: Session, obj: ContextObjects, status: str, reason: str, now: datetime) -> None:
    """Status transition on an *existing* (already-persisted) object: bumps its version
    and writes a context_versions row. A no-op if already in that status."""
    if obj.status == status:
        return
    obj.status = status
    obj.version += 1
    obj.updated_at = now
    db.add(
        ContextVersions(
            id=uuid.uuid4(),
            context_id=obj.id,
            version=obj.version,
            status=status,
            attributes=obj.attributes,
            content=obj.content,
            changed_by=None,
            reason=reason,
            created_at=now,
        )
    )


def _mark_conflicting(
    db: Session, new_obj: ContextObjects, existing: ContextObjects, rule: str, now: datetime
) -> None:
    new_obj.status = "conflicting"
    _transition(db, existing, "conflicting", f"{rule}: conflicts with {new_obj.id}", now)
    db.add(
        ContextRelations(
            id=uuid.uuid4(),
            from_id=new_obj.id,
            to_id=existing.id,
            relation="contradicts",
            created_by=f"system:lifecycle:{rule}",
            confidence=None,
            created_at=now,
            resolved_at=None,  # pending R5 human resolution
        )
    )


def apply_lifecycle_rules(
    db: Session, new_obj: ContextObjects, existing: ContextObjects, now: datetime
) -> None:
    """R1-R4, first match wins. Mutates `new_obj.status` in place (its own version=1 row
    is written by the caller once, after all siblings are processed) and transitions
    `existing` via `_transition` when a rule changes its status."""
    new_stance = (new_obj.attributes or {}).get("stance")
    existing_stance = (existing.attributes or {}).get("stance")

    # R1: different stages + incompatible stances -> both conflicting.
    if new_obj.stage != existing.stage and stances_conflict(new_stance, existing_stance):
        _mark_conflicting(db, new_obj, existing, "R1", now)
        return

    # R2: lower authority never wins, regardless of recency.
    if new_obj.authority < existing.authority:
        _mark_conflicting(db, new_obj, existing, "R2", now)
        return

    # R3: later timestamp, authority >=, and (same author OR explicit correction).
    if (
        new_obj.valid_from > existing.valid_from
        and new_obj.authority >= existing.authority
        and (same_author(new_obj, existing) or is_explicit_correction(new_obj))
    ):
        db.add(
            ContextRelations(
                id=uuid.uuid4(),
                from_id=new_obj.id,
                to_id=existing.id,
                relation="supersedes",
                created_by="system:lifecycle:R3",
                confidence=round(float(new_obj.confidence), 2),
                created_at=now,
                resolved_at=now,
            )
        )
        _transition(db, existing, "superseded", f"R3: superseded by {new_obj.id}", now)
        existing.valid_to = new_obj.valid_from
        return

    # R4: anything else — different authors, same stage, no correction. Never silently
    # resolved by recency.
    _mark_conflicting(db, new_obj, existing, "R4", now)


def resolve_object_state(db: Session, tenant_id: uuid.UUID, new_obj: ContextObjects) -> None:
    """Entry point, called once per newly-persisted candidate object, after it has been
    flushed (so it has an id/embedding and is comparable against siblings already in the
    database)."""
    siblings = find_subject_siblings(
        db, tenant_id, new_obj.entity_id, new_obj.type, new_obj.subject_key, new_obj.id
    )
    if not siblings:
        return

    now = datetime.now(UTC)
    for sibling in siblings:
        compatible = slots_compatible(new_obj.attributes or {}, sibling.attributes or {})
        if compatible:
            cosine = cosine_similarity(new_obj.embedding, sibling.embedding)
            apply_dedup(db, new_obj, sibling, cosine)
        else:
            apply_lifecycle_rules(db, new_obj, sibling, now)
