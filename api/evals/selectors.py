"""Resolves the declarative `source`/`object` selectors used in `evals/cases/*.yaml` into
real rows from the tenant a seeded ingest just wrote (§19: cases assert on real pipeline
output, not hand-built fixtures).

Selectors deliberately key off facts that are *known in advance from the mock-data files
and connector code* (a call's `call_id`, an email's `message_id`, a drive doc's path +
section heading, a Slack message's own literal text) rather than anything the LLM
produces -- so a case file reads as "the object extracted from this exact source", and the
LLM-dependent fields (type, subject_key, stance, status...) stay purely on the assertion
side, where the eval is actually testing them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.orm import ContextObjects, Sources


class SelectorError(RuntimeError):
    """Raised when a `source`/`object` selector resolves to zero or more than one row.
    Kept distinct from an assertion failure: this means the eval case couldn't even find
    the thing it wanted to check, which is its own diagnostic signal (e.g. "the extractor
    didn't produce anything for this source at all")."""


def _source_matches(source: Sources, spec: dict[str, Any]) -> bool:
    kind = spec["kind"]
    if source.kind != kind:
        return False
    prov = source.provenance or {}

    if kind == "call":
        return prov.get("call_id") == spec["call_id"]
    if kind == "email":
        return prov.get("message_id") == spec["message_id"]
    if kind == "drive":
        if prov.get("path") != spec["path"]:
            return False
        heading_contains = spec.get("heading_contains")
        if heading_contains is not None:
            heading = (prov.get("section_heading") or "").lower()
            return heading_contains.lower() in heading
        return True
    if kind == "slack":
        text_contains = spec.get("text_contains")
        if text_contains is not None and text_contains.lower() not in (source.text or "").lower():
            return False
        channel = spec.get("channel")
        return channel is None or prov.get("channel_id") == channel
    raise ValueError(f"unknown source selector kind: {kind!r}")


def resolve_source(db: Session, tenant_id: uuid.UUID, spec: dict[str, Any]) -> Sources:
    candidates = [
        s
        for s in db.query(Sources).filter(Sources.tenant_id == tenant_id).all()
        if _source_matches(s, spec)
    ]
    if len(candidates) != 1:
        raise SelectorError(
            f"source selector {spec!r} matched {len(candidates)} sources (expected exactly 1)"
        )
    return candidates[0]


def resolve_objects(
    db: Session, tenant_id: uuid.UUID, spec: dict[str, Any]
) -> list[ContextObjects]:
    """`spec` must have a `source` sub-selector, plus optional filters on the objects that
    source produced: `type`, `subject_key_suffix`, `subject_key_contains`, `stance`,
    `evidence_quote_contains`."""
    source = resolve_source(db, tenant_id, spec["source"])
    objects = (
        db.query(ContextObjects)
        .filter(ContextObjects.tenant_id == tenant_id, ContextObjects.source_id == source.id)
        .all()
    )

    type_ = spec.get("type")
    if type_ is not None:
        objects = [o for o in objects if o.type == type_]

    subject_key_suffix = spec.get("subject_key_suffix")
    if subject_key_suffix is not None:
        objects = [o for o in objects if o.subject_key.endswith(subject_key_suffix)]

    subject_key_contains = spec.get("subject_key_contains")
    if subject_key_contains is not None:
        objects = [o for o in objects if subject_key_contains in o.subject_key]

    stance = spec.get("stance")
    if stance is not None:
        objects = [o for o in objects if (o.attributes or {}).get("stance") == stance]

    evidence_quote_contains = spec.get("evidence_quote_contains")
    if evidence_quote_contains is not None:
        needle = evidence_quote_contains.lower()
        objects = [o for o in objects if needle in (o.evidence_quote or "").lower()]

    return objects


def resolve_object(db: Session, tenant_id: uuid.UUID, spec: dict[str, Any]) -> ContextObjects:
    objects = resolve_objects(db, tenant_id, spec)
    if len(objects) != 1:
        got = [(o.type, o.subject_key, o.evidence_quote[:60]) for o in objects]
        raise SelectorError(
            f"object selector {spec!r} matched {len(objects)} objects (expected exactly 1): {got}"
        )
    return objects[0]


@dataclass
class ResolvedField:
    value: Any
    path: str


def get_object_field(obj: ContextObjects, field_path: str) -> Any:
    """Reads `obj.<field>`, or `obj.attributes["<slot>"]` when `field_path` is
    `attributes.<slot>` (dotted access into the jsonb attributes blob)."""
    if field_path.startswith("attributes."):
        slot = field_path.removeprefix("attributes.")
        return (obj.attributes or {}).get(slot)
    return getattr(obj, field_path)
