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
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.orm import ContextObjects, ContextRelations

AUTO_DUPLICATE_THRESHOLD = 0.92
REVIEW_DUPLICATE_THRESHOLD = 0.80

# Bookkeeping keys the pipeline stashes under attributes.extra (§7.3's corrects/
# corrects_hint) describe the *statement*, not a slot value — they never make two
# otherwise-identical objects "incompatible".
_IGNORED_EXTRA_KEYS = {"corrects", "corrects_hint"}

# `rationale` is free-text explanatory prose (why a decision/stance was taken), not a
# categorical/structured slot value -- unlike `root_cause`/`remediation` (§4.2 incident
# slots), which state what actually happened/is being done and are meaningfully different
# facts when they differ (confirmed by a real test: a corrected root_cause must still route
# through R3 supersession, not get silently treated as compatible). Found live (2026-10-01):
# two "SCIM deferred to Q1" extractions worded their `rationale` differently ("avoid
# splitting focus before SSO lands" vs "SSO is the higher-leverage item for the renewal")
# despite stating the identical fact -- a literal `!=` on free text flagged them as an
# incompatible slot and routed a true duplicate into R4 ("conflicting") instead of dedup,
# same failure class as the due_date-precision bug below.
_FREE_TEXT_KEYS = {"rationale"}


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


def _quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _due_dates_compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two `due_date`s at the *same* precision describe the same point only up to that
    precision's granularity -- "Q1" and "quarter"-precision due_date are the slot value,
    not whatever single day the extractor happened to normalize it to. Found live
    (2026-10-01): two independent "deferred to Q1" extractions normalized to 2027-01-01 and
    2027-03-31 respectively -- same quarter, same stated precision, but a literal `!=`
    compare treated them as a conflicting slot and both ended up `status='conflicting'` in
    the dashboard, next to each other, looking like duplicated noise rather than the single
    real fact they are. A day-precision date still needs an exact match; only a shared
    coarser precision gets bucketed."""
    a_raw, b_raw = a.get("due_date"), b.get("due_date")
    a_precision, b_precision = a.get("due_date_precision"), b.get("due_date_precision")
    if a_raw == b_raw:
        return True
    if a_precision != b_precision or a_precision not in ("month", "quarter"):
        return bool(a_raw == b_raw)
    a_date = a_raw if isinstance(a_raw, date) else date.fromisoformat(str(a_raw))
    b_date = b_raw if isinstance(b_raw, date) else date.fromisoformat(str(b_raw))
    if a_precision == "month":
        return (a_date.year, a_date.month) == (b_date.year, b_date.month)
    return (a_date.year, _quarter(a_date)) == (b_date.year, _quarter(b_date))


def slots_compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """No slot present (non-null) in both `a` and `b` with a differing value."""
    for key, value in a.items():
        if value is None or key in _FREE_TEXT_KEYS:
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
        if key == "due_date_precision":
            # Compared as part of "due_date" below, not on its own -- a precision mismatch
            # alone (e.g. "day" vs "quarter") isn't a conflicting slot value by itself.
            continue
        if key == "due_date":
            other_value = b.get(key)
            if other_value is not None and not _due_dates_compatible(a, b):
                return False
            continue
        if key == "quantity_unit":
            # Found alongside the due_date/rationale bug (2026-10-01): two extractions of
            # the identical "8,000 users" fact wrote "users" and "provisioned users" --
            # same unit, different wording, exact-match treated it as a conflicting slot.
            # A containment check (lowercased) catches that and "users" vs "users per org"
            # while still treating truly different units ("seats" vs "users") as
            # incompatible, since neither contains the other.
            other_value = b.get(key)
            if other_value is not None and value != other_value:
                v, o = value.lower(), other_value.lower()
                if v not in o and o not in v:
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
