"""Assertion library. Each function takes `(db, tenant_id, params)` and returns a
`CheckResult`. `evals/cases/*.yaml` reference these by `kind`, so cases stay pure data and
the interpretation logic lives in one place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.orm import ContextGaps, ContextRelations
from evals.handoff_eval import gap_subject_key, latest_validation
from evals.selectors import (
    SelectorError,
    get_object_field,
    resolve_object,
    resolve_objects,
)


@dataclass
class CheckResult:
    passed: bool
    detail: str


class CheckFn(Protocol):
    def __call__(self, db: Session, tenant_id: uuid.UUID, params: dict[str, Any]) -> CheckResult: ...


def check_entities_equal(db: Session, tenant_id: uuid.UUID, params: dict[str, Any]) -> CheckResult:
    """All `objects` selectors must resolve to context objects sharing one `entity_id`
    (§7.1: alias resolution collapsing several sources' mentions of the same company)."""
    specs = params["objects"]
    resolved = [(spec, resolve_object(db, tenant_id, spec)) for spec in specs]
    entity_ids = {obj.entity_id for _, obj in resolved}
    if len(entity_ids) == 1:
        return CheckResult(True, f"all {len(resolved)} objects share entity_id={entity_ids!r}")
    detail = ", ".join(f"{spec.get('source', {})!r} -> entity_id={obj.entity_id}" for spec, obj in resolved)
    return CheckResult(False, f"expected one shared entity_id, got {len(entity_ids)} distinct: {detail}")


def check_entities_distinct(db: Session, tenant_id: uuid.UUID, params: dict[str, Any]) -> CheckResult:
    """`a` and `b` must resolve to context objects with *different* `entity_id`s (no
    over-merging of two different companies into one entity)."""
    obj_a = resolve_object(db, tenant_id, params["a"])
    obj_b = resolve_object(db, tenant_id, params["b"])
    if obj_a.entity_id != obj_b.entity_id:
        return CheckResult(True, f"entity_id differs: {obj_a.entity_id} != {obj_b.entity_id}")
    return CheckResult(False, f"expected distinct entities, both resolved to entity_id={obj_a.entity_id}")


def _relation_query(db: Session, tenant_id: uuid.UUID, relation: str, from_id: uuid.UUID, to_id: uuid.UUID):
    return (
        db.query(ContextRelations)
        .filter(
            ContextRelations.relation == relation,
            ContextRelations.from_id == from_id,
            ContextRelations.to_id == to_id,
        )
        .all()
    )


def _find_relations(
    db: Session,
    tenant_id: uuid.UUID,
    relation: str,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    either_direction: bool,
) -> list[ContextRelations]:
    if either_direction:
        return (
            db.query(ContextRelations)
            .filter(
                ContextRelations.relation == relation,
                or_(
                    and_(ContextRelations.from_id == from_id, ContextRelations.to_id == to_id),
                    and_(ContextRelations.from_id == to_id, ContextRelations.to_id == from_id),
                ),
            )
            .all()
        )
    return _relation_query(db, tenant_id, relation, from_id, to_id)


def check_relation_exists(db: Session, tenant_id: uuid.UUID, params: dict[str, Any]) -> CheckResult:
    """A `context_relations` row of kind `relation` connects `from`/`to` (§8, §7.2, §7.3).
    Direction matters for `supersedes`/`derived_from`; `either_direction` (default: true for
    `duplicate_of`/`contradicts`, false otherwise) lets a case relax that."""
    relation = params["relation"]
    obj_from = resolve_object(db, tenant_id, params["from"])
    obj_to = resolve_object(db, tenant_id, params["to"])
    either_direction = params.get("either_direction", relation in ("duplicate_of", "contradicts"))
    rows = _find_relations(db, tenant_id, relation, obj_from.id, obj_to.id, either_direction)

    created_by_contains = params.get("created_by_contains")
    if created_by_contains is not None:
        rows = [r for r in rows if created_by_contains in (r.created_by or "")]

    if rows:
        tags = [r.created_by for r in rows]
        return CheckResult(True, f"found {len(rows)} `{relation}` relation(s): created_by={tags!r}")
    return CheckResult(
        False,
        f"expected a `{relation}` relation between {obj_from.id} and {obj_to.id} "
        f"(either_direction={either_direction}"
        + (f", created_by_contains={created_by_contains!r}" if created_by_contains else "")
        + "), found none",
    )


def check_relation_absent(db: Session, tenant_id: uuid.UUID, params: dict[str, Any]) -> CheckResult:
    """The negation of `relation_exists` -- always checks both directions, since the point
    is "these two objects were never linked this way", regardless of direction."""
    relation = params["relation"]
    obj_from = resolve_object(db, tenant_id, params["from"])
    obj_to = resolve_object(db, tenant_id, params["to"])
    rows = _find_relations(db, tenant_id, relation, obj_from.id, obj_to.id, either_direction=True)
    if not rows:
        return CheckResult(True, f"no `{relation}` relation between {obj_from.id} and {obj_to.id}")
    return CheckResult(
        False,
        f"expected no `{relation}` relation between {obj_from.id} and {obj_to.id}, "
        f"found {len(rows)}: {[(r.from_id, r.to_id, r.created_by) for r in rows]}",
    )


def check_object_field_equals(db: Session, tenant_id: uuid.UUID, params: dict[str, Any]) -> CheckResult:
    """`object.<field>` (or `object.attributes.<slot>`) equals `value`. The single-purpose
    workhorse for "status is X", "version is 1", "stance is required", etc."""
    obj = resolve_object(db, tenant_id, params["object"])
    field_path = params["field"]
    actual = get_object_field(obj, field_path)
    expected = params["value"]
    if actual == expected:
        return CheckResult(True, f"{field_path}={actual!r}")
    return CheckResult(False, f"expected {field_path}={expected!r}, got {actual!r}")


def check_object_count(db: Session, tenant_id: uuid.UUID, params: dict[str, Any]) -> CheckResult:
    """Exactly `count` objects match the (source + filters) selector -- used when the
    number of extracted objects is itself the thing under test."""
    objects = resolve_objects(db, tenant_id, params["objects"])
    expected = params["count"]
    if len(objects) == expected:
        return CheckResult(True, f"found {len(objects)} matching objects")
    return CheckResult(False, f"expected {expected} matching objects, found {len(objects)}")


def check_handoff_gap_outcome(db: Session, tenant_id: uuid.UUID, params: dict[str, Any]) -> CheckResult:
    """§19.2 degradation set: asserts the §10 validator's latest run for
    `(entity_slug, contract_id)` produced a `context_gaps` row for the `(subject, slot)`
    triple with the expected `outcome`. `subject_key_suffix`/`subject_key_contains` mirror
    the selector filters used everywhere else in this harness. `slot: null` (the YAML
    default) matches an `object_missing`/whole-object gap, which never has a slot (§10.1).
    Optionally asserts `inherited` (§10.4 chain attribution) when given.

    Requires `evals.handoff_eval.run_required_handoff_validations` to have already run in
    this process (`evals.runner.main` does this once, before any case runs) -- this check
    only *reads* the `handoff_validations`/`context_gaps` rows that produced."""
    entity_slug = params["entity_slug"]
    contract_id = params["contract_id"]
    validation = latest_validation(db, tenant_id, entity_slug, contract_id)
    if validation is None:
        return CheckResult(
            False,
            f"no handoff_validations row for entity={entity_slug!r} contract={contract_id!r} "
            "-- run_required_handoff_validations either didn't run or the entity/contract "
            "doesn't exist yet",
        )

    gaps = db.query(ContextGaps).filter(ContextGaps.validation_id == validation.id).all()
    suffix = params.get("subject_key_suffix")
    contains = params.get("subject_key_contains")
    expected_slot = params.get("slot")
    expected_inherited = params.get("inherited")

    matches = []
    for g in gaps:
        subject_key = gap_subject_key(db, g)
        if subject_key is None:
            continue
        if suffix is not None and not subject_key.endswith(suffix):
            continue
        if contains is not None and contains not in subject_key:
            continue
        if g.slot != expected_slot:
            continue
        matches.append(g)

    expected_outcome = params["outcome"]
    hits = [g for g in matches if g.outcome == expected_outcome]
    if expected_inherited is not None:
        hits = [g for g in hits if g.inherited == expected_inherited]

    if hits:
        g = hits[0]
        return CheckResult(
            True,
            f"outcome={g.outcome} slot={g.slot!r} inherited={g.inherited} "
            f"severity={g.severity} ({g.severity_band}): {g.explanation[:80]}...",
        )
    if matches:
        found = [(g.outcome, g.inherited) for g in matches]
        return CheckResult(
            False,
            f"found {len(matches)} matching gap(s) but (outcome, inherited)={found}, "
            f"expected outcome={expected_outcome!r}"
            + (f" inherited={expected_inherited}" if expected_inherited is not None else ""),
        )
    return CheckResult(
        False,
        f"no gap found for entity={entity_slug!r} contract={contract_id!r} "
        f"subject~{suffix or contains!r} slot={expected_slot!r} "
        f"(validator produced {len(gaps)} gap(s) total for this handoff)",
    )


CHECKS: dict[str, CheckFn] = {
    "entities_equal": check_entities_equal,
    "entities_distinct": check_entities_distinct,
    "relation_exists": check_relation_exists,
    "relation_absent": check_relation_absent,
    "object_field_equals": check_object_field_equals,
    "object_count": check_object_count,
    "handoff_gap_outcome": check_handoff_gap_outcome,
}


def run_check(db: Session, tenant_id: uuid.UUID, kind: str, params: dict[str, Any]) -> CheckResult:
    fn = CHECKS.get(kind)
    if fn is None:
        return CheckResult(False, f"unknown check kind: {kind!r}")
    try:
        return fn(db, tenant_id, params)
    except SelectorError as exc:
        # A selector resolution failure is itself a check failure (surfaced with a
        # `[SELECTOR]` marker by the runner) -- it means the case's premise couldn't even
        # be evaluated against what the pipeline actually produced.
        return CheckResult(False, f"[SELECTOR] {exc}")
