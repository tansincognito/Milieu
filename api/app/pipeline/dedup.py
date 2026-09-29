"""Deduplication (§7.2).

Two objects are duplicate *candidates* when they share `entity_id`, `type`, and
`subject_key`, and have compatible slots (no slot with a conflicting value). Among
candidates: cosine similarity >= AUTO_DUPLICATE_THRESHOLD creates an automatic
`duplicate_of` relation (+ `supported_by` back onto the canonical, whose authority
becomes the max across the group); REVIEW_DUPLICATE_THRESHOLD..AUTO_DUPLICATE_THRESHOLD
creates an unresolved `duplicate_of` relation, routed to review via
`POST /conflicts/{relation_id}/resolve`.

Source records and evidence are never merged or deleted — this module only ever adds
`context_relations` rows and, on auto-link, bumps the canonical's `authority` column.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.orm import ContextObjects, ContextRelations

AUTO_DUPLICATE_THRESHOLD = 0.92
REVIEW_DUPLICATE_THRESHOLD = 0.80

# Bookkeeping keys the pipeline stashes under attributes.extra (§7.3's corrects/
# corrects_hint) describe the *statement*, not a slot value — they never make two
# otherwise-identical objects "incompatible".
_IGNORED_EXTRA_KEYS = {"corrects", "corrects_hint"}


def cosine_similarity(a: list[float] | None, b: list[float] | None) -> float:
    if a is None or b is None:
        return 0.0
    a_list, b_list = list(a), list(b)
    dot = sum(x * y for x, y in zip(a_list, b_list, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a_list))
    norm_b = math.sqrt(sum(y * y for y in b_list))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def slots_compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """No slot present (non-null) in both `a` and `b` with a differing value."""
    for key, value in a.items():
        if value is None:
            continue
        if key == "extra":
            other_extra = b.get("extra") or {}
            for ek, ev in (value or {}).items():
                if ek in _IGNORED_EXTRA_KEYS or ev is None:
                    continue
                other_ev = other_extra.get(ek)
                if other_ev is not None and other_ev != ev:
                    return False
            continue
        other_value = b.get(key)
        if other_value is not None and other_value != value:
            return False
    return True


def find_subject_siblings(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    type_: str,
    subject_key: str,
    exclude_id: uuid.UUID,
) -> list[ContextObjects]:
    """Active objects sharing (entity_id, type, subject_key) — the shared candidate pool
    for both dedup (§7.2) and lifecycle/supersession/conflict (§7.3)."""
    return (
        db.query(ContextObjects)
        .filter(
            ContextObjects.tenant_id == tenant_id,
            ContextObjects.entity_id == entity_id,
            ContextObjects.type == type_,
            ContextObjects.subject_key == subject_key,
            ContextObjects.status == "active",
            ContextObjects.id != exclude_id,
        )
        .all()
    )


def apply_dedup(
    db: Session, new_obj: ContextObjects, sibling: ContextObjects, cosine: float
) -> bool:
    """Returns True if `new_obj`/`sibling` were handled as duplicates (caller should not
    also run lifecycle rules on this pair)."""
    if cosine < REVIEW_DUPLICATE_THRESHOLD:
        return False

    now = datetime.now(UTC)
    if cosine >= AUTO_DUPLICATE_THRESHOLD:
        canonical, duplicate = (
            (new_obj, sibling) if new_obj.authority >= sibling.authority else (sibling, new_obj)
        )
        db.add(
            ContextRelations(
                id=uuid.uuid4(),
                from_id=duplicate.id,
                to_id=canonical.id,
                relation="duplicate_of",
                created_by="system:dedup",
                confidence=round(cosine, 2),
                created_at=now,
                resolved_at=now,
            )
        )
        db.add(
            ContextRelations(
                id=uuid.uuid4(),
                from_id=canonical.id,
                to_id=duplicate.id,
                relation="supported_by",
                created_by="system:dedup",
                confidence=round(cosine, 2),
                created_at=now,
                resolved_at=now,
            )
        )
        max_authority = max(canonical.authority, duplicate.authority)
        canonical.authority = max_authority
    else:
        db.add(
            ContextRelations(
                id=uuid.uuid4(),
                from_id=new_obj.id,
                to_id=sibling.id,
                relation="duplicate_of",
                created_by="system:dedup",
                confidence=round(cosine, 2),
                created_at=now,
                resolved_at=None,
            )
        )
    return True
