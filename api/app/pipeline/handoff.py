"""The handoff validator (§10) — Milieu's core differentiator. For a given
`(entity, from_stage, to_stage, contract_id)` it compares upstream and downstream Context
Objects slot by slot and persists `context_gaps` rows for every loss.

Deliberately has no dependency on `LLMClient`. §10.1's outcome table names an "LLM judge"
fallback for `equivalent`, `generalized`, `contradicted`, and `stale_reference` — this
module implements the deterministic paths only (vocabulary hierarchy, date precision,
stance/date comparison, a small synonym table, and `context_objects.status` history), which
is what makes it provable without a live model (see `tests/integration/test_handoff.py`).
Slot pairs a deterministic rule can't classify fall back to `contradicted` — the
conservative choice, since an unflagged loss is worse than a gap a human dismisses.

Algorithm (§10.1):
1. Group upstream objects (`active` or `conflicting`, `from_stage`, matching types,
   authority >= field.min_upstream_authority) by `subject_key`. The reference `u` per group
   is the highest-authority member (ties broken by recency); if any member is
   `conflicting`, the group is tagged `upstream_conflict` (§10.1 step 1).
2. Find the downstream counterpart `d`: same `subject_key`, active, in `to_stage` (highest
   authority, then most recent). No `d` -> one `object_missing` gap for the whole field.
3. Otherwise, compare every slot the field lists. If `u`'s own value for a slot is unset,
   walk the `derived_from` lineage chain up from `u` to the nearest ancestor that does have
   it (§8) — this is what lets a loss that already happened at an earlier handoff (e.g.
   Sales->Product) keep surfacing at the next one (Product->Engineering) instead of being
   silently skipped because the immediate upstream object no longer carries the value.
   Needing to walk up *is* chain attribution (§10.4): it means the loss originates at an
   earlier handoff, so the gap is `inherited`; if `u` has its own value, any loss found here
   is new at this handoff, so the gap is `origin`.
4. `preserved` and `equivalent` never produce a gap (§10.1 point 4).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.orm import ContextGaps, ContextObjects, ContextRelations, HandoffValidations
from app.pipeline.contracts import ContractField, ContractSpec, get_contract
from app.pipeline.lineage import upstream_chain

Outcome = str  # one of GAP_OUTCOMES (app.models.orm) — see module docstring

IMPORTANCE_WEIGHT: dict[str, float] = {"critical": 3.0, "high": 2.0, "normal": 1.0}
AUTHORITY_WEIGHT: dict[int, float] = {4: 1.0, 3: 0.8, 2: 0.6, 1: 0.3, 0: 0.1}
OUTCOME_WEIGHT: dict[str, float] = {
    "object_missing": 1.0,
    "contradicted": 1.0,
    "stale_reference": 0.9,
    "missing": 0.9,
    "generalized": 0.6,
}

# §4.2: "the vocabulary also defines generalization: SAML, OIDC, and Okta refine `sso`".
# Keyed by capability slug (the segment of subject_key after the entity slug).
CAPABILITY_GENERALIZES: dict[str, set[str]] = {
    "sso": {"saml", "oidc"},
}

# §4.2 region hierarchy example: "EU ⊃ eu-west-1". Broad key -> its narrow members.
REGION_HIERARCHY: dict[str, set[str]] = {
    "eu": {"eu-west-1", "eu-central-1", "eu-north-1", "eu-west-2"},
    "us": {"us-east-1", "us-east-2", "us-west-1", "us-west-2"},
}

# A small, deterministic synonym table for the `equivalent` outcome (§10.1: "different
# wording, same meaning"). Real paraphrase judging needs the LLM judge fallback the spec
# names — out of scope while the free tier is saturated (see dispatch report) — but the
# *mechanism* (equivalent -> no gap, same as preserved) is real and covered by a test.
SLOT_EQUIVALENTS: dict[str, list[set[str]]] = {
    "protocol": [{"saml", "saml2", "saml 2.0", "saml-based sso"}],
}

_DATE_PRECISION_ORDER: dict[str, int] = {"day": 0, "month": 1, "quarter": 2}

_SLOT_KIND: dict[str, str] = {
    "protocol": "specificity",
    "idp": "dependency",
    "due_date": "deadline",
    "priority": "priority",
    "acceptance_criteria": "acceptance",
    "rationale": "rationale",
    "region": "constraint",
    "quantity": "specificity",
    "integrations": "dependency",
    "plan": "specificity",
    "impact": "incident",
    "time_window": "incident",
    "root_cause": "incident",
    "sla_impact": "incident",
    "remediation": "incident",
    "extra": "specificity",
}

# For the three nested incident slots, §10.1's worked losses (I3/I4/I2 in §19.1 Golden 3)
# turn on one representative sub-key rather than whole-dict equality (customers/error_rate
# etc. can legitimately vary without that being the loss worth flagging).
_DICT_SLOT_KEY: dict[str, str] = {
    "impact": "data_loss",
    "sla_impact": "breached",
    "time_window": "start",
}


def _is_set(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple)):
        return len(value) > 0
    return True


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, (list, tuple)):
        return tuple(sorted(_normalize(v) for v in value))
    if isinstance(value, dict):
        return tuple(sorted((k, _normalize(v)) for k, v in value.items()))
    return value


def _capability_of(subject_key: str) -> str:
    parts = subject_key.split(":")
    return parts[1] if len(parts) > 1 else subject_key


def _protocol_generalizes(capability: str, upstream_protocol: Any) -> bool:
    children = CAPABILITY_GENERALIZES.get(capability, set())
    return _normalize(upstream_protocol) in children


def _region_generalizes(upstream_region_norm: Any, downstream_region_norm: Any) -> bool:
    narrow = REGION_HIERARCHY.get(downstream_region_norm, set())
    return upstream_region_norm in narrow


def _is_equivalent(slot: str, nu: Any, nd: Any) -> bool:
    for group in SLOT_EQUIVALENTS.get(slot, []):
        if nu in group and nd in group:
            return True
    return False


@dataclass
class GapDraft:
    contract_field: str
    upstream_id: uuid.UUID | None
    downstream_id: uuid.UUID | None
    slot: str | None
    outcome: Outcome
    severity: float
    severity_band: str
    inherited: bool
    upstream_conflict: bool
    explanation: str


def _severity(importance: str, authority: int, outcome: Outcome) -> tuple[float, str]:
    value = (
        IMPORTANCE_WEIGHT[importance]
        * AUTHORITY_WEIGHT.get(authority, 0.1)
        * OUTCOME_WEIGHT[outcome]
    )
    value = round(value, 2)
    band = "high" if value >= 2.0 else "medium" if value >= 1.0 else "low"
    return value, band


_OUTCOME_LABEL: dict[str, str] = {
    "generalized": "loss (generalized)",
    "missing": "loss (dropped)",
    "contradicted": "Contradiction",
    "stale_reference": "Stale context in use",
    "object_missing": "Requirement dropped entirely",
}


def _label(outcome: Outcome, slot: str | None) -> str:
    if outcome == "contradicted":
        return "Contradiction"
    if outcome == "stale_reference":
        return "Stale context in use"
    if outcome == "object_missing":
        return "Requirement dropped entirely"
    kind = _SLOT_KIND.get(slot or "", "context")
    if outcome == "generalized" and kind == "deadline":
        return "Deadline generalized"
    return f"{kind.capitalize()} {_OUTCOME_LABEL.get(outcome, outcome)}"


def _fmt(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _explain(
    *,
    outcome: Outcome,
    contract_id: str,
    field: ContractField,
    slot: str | None,
    u: ContextObjects,
    d: ContextObjects | None,
    u_val: Any,
    d_val: Any,
    note: str | None,
) -> str:
    label = _label(outcome, slot)
    contract_field = f"{contract_id}.{field.name}"
    u_desc = (
        f'upstream {u.type} "{u.content}" ({u.actor_role}, authority {u.authority}, '
        f"{u.stage} {u.valid_from.date().isoformat()})"
    )
    if outcome == "object_missing":
        return (
            f"**{label}** — {u_desc} has no counterpart in `{field.name}` downstream. "
            f"Contract `{contract_field}` ({field.importance}) requires this to propagate."
        )
    assert d is not None
    d_desc = f'downstream "{d.content}" ({d.stage} {d.valid_from.date().isoformat()})'
    verb = {
        "generalized": "generalizes this to",
        "missing": "drops this — it is absent in",
        "contradicted": "contradicts this with",
        "stale_reference": "still cites a superseded value:",
    }.get(outcome, "changes this to")
    slot_desc = f"`{slot} = {_fmt(u_val)}`" if slot else ""
    value_desc = "" if d_val is None else f" `{_fmt(d_val)}`"
    extra = f" ({note})" if note else ""
    return (
        f"**{label}** — {u_desc} specifies {slot_desc}. {d_desc} {verb}{value_desc}{extra}. "
        f"Contract `{contract_field}` ({field.importance}) requires this slot to survive."
    )


def _effective_upstream_value(
    db: Session, u: ContextObjects, slot: str
) -> tuple[ContextObjects, Any, bool]:
    """Returns (source_object, value, walked_up). `walked_up=True` means `u`'s own slot was
    unset and the value came from an ancestor via `derived_from` — the loss originates at an
    earlier handoff (§10.4 chain attribution)."""
    own = (u.attributes or {}).get(slot)
    if _is_set(own):
        return u, own, False
    for row in upstream_chain(db, u.id):
        ancestor = db.get(ContextObjects, row["object_id"])
        if ancestor is None:
            continue
        val = (ancestor.attributes or {}).get(slot)
        if _is_set(val):
            return ancestor, val, True
    return u, None, False


def _find_stale_reference(
    db: Session, eff_obj: ContextObjects, slot: str, nd_value: Any
) -> ContextObjects | None:
    """§10.1: 'd's value matches a superseded version of u' — searches sibling objects
    (same entity/subject_key/stage/type as the *current* reference `eff_obj`) that were
    superseded, for one whose slot value is what the downstream object still cites."""
    candidates = (
        db.query(ContextObjects)
        .filter(
            ContextObjects.tenant_id == eff_obj.tenant_id,
            ContextObjects.entity_id == eff_obj.entity_id,
            ContextObjects.subject_key == eff_obj.subject_key,
            ContextObjects.stage == eff_obj.stage,
            ContextObjects.type == eff_obj.type,
            ContextObjects.status == "superseded",
            ContextObjects.id != eff_obj.id,
        )
        .all()
    )
    for cand in candidates:
        val = (cand.attributes or {}).get(slot)
        if _is_set(val) and _normalize(val) == nd_value:
            return cand
    return None


def _classify_slot(
    *,
    db: Session,
    slot: str,
    subject_key: str,
    u_val: Any,
    d_val: Any,
    d_attrs: dict[str, Any],
    eff_obj: ContextObjects,
) -> tuple[Outcome, str | None] | None:
    """Returns (outcome, note) for a losing slot, or None when it's preserved/equivalent
    (§10.1 point 4: neither produces a gap)."""
    if not _is_set(d_val):
        # §4.2 vocabulary generalization: the slot can be legitimately unset downstream
        # while the *subject itself* is the generalized form (protocol SAML -> capability
        # sso), or the downstream statement only carries a coarser period (due_date_
        # precision set, no exact date) rather than nothing at all.
        if slot == "protocol" and _protocol_generalizes(_capability_of(subject_key), u_val):
            return "generalized", f"downstream targets the broader capability `{_capability_of(subject_key)}`"
        if slot == "due_date" and _is_set(d_attrs.get("due_date_precision")):
            return "generalized", f"downstream only states `{d_attrs['due_date_precision']}` precision"
        return "missing", None

    nu, nd = _normalize(u_val), _normalize(d_val)
    if nu == nd:
        return None  # preserved
    if _is_equivalent(slot, nu, nd):
        return None  # equivalent — different wording, same meaning; no gap

    stale = _find_stale_reference(db, eff_obj, slot, nd)
    if stale is not None:
        return "stale_reference", f'matches superseded value "{stale.content}"'

    if slot == "protocol":
        # d_val is set but differs from u_val: generalized only if the downstream value IS
        # the broader capability name itself (e.g. protocol="SSO"); a sibling protocol
        # (SAML vs OIDC) is a contradiction, not a generalization.
        if nd == _capability_of(subject_key):
            return "generalized", None
        return "contradicted", None

    if slot == "region":
        if _region_generalizes(nu, nd):
            return "generalized", None
        return "contradicted", None

    if slot == "due_date":
        return _classify_due_date(eff_obj.attributes or {}, d_attrs)

    if slot in _DICT_SLOT_KEY:
        key = _DICT_SLOT_KEY[slot]
        u_sub = _normalize((u_val or {}).get(key)) if isinstance(u_val, dict) else None
        d_sub = _normalize((d_val or {}).get(key)) if isinstance(d_val, dict) else None
        if u_sub == d_sub:
            return None
        return "contradicted", f"`{key}` differs"

    return "contradicted", None


def _classify_due_date(u_attrs: dict[str, Any], d_attrs: dict[str, Any]) -> tuple[Outcome, str | None]:
    u_prec = _DATE_PRECISION_ORDER.get(u_attrs.get("due_date_precision") or "day", 0)
    d_prec = _DATE_PRECISION_ORDER.get(d_attrs.get("due_date_precision") or "day", 0)
    if d_prec > u_prec:
        return "generalized", f"precision dropped from `{u_attrs.get('due_date_precision') or 'day'}` to `{d_attrs.get('due_date_precision') or 'day'}`"
    return "contradicted", None


def _upstream_groups(
    db: Session, tenant_id: uuid.UUID, entity_id: uuid.UUID, from_stage: str, field: ContractField
) -> dict[str, list[ContextObjects]]:
    rows = (
        db.query(ContextObjects)
        .filter(
            ContextObjects.tenant_id == tenant_id,
            ContextObjects.entity_id == entity_id,
            ContextObjects.stage == from_stage,
            ContextObjects.type.in_(field.types),
            ContextObjects.status.in_(("active", "conflicting")),
            ContextObjects.authority >= field.min_upstream_authority,
        )
        .all()
    )
    groups: dict[str, list[ContextObjects]] = {}
    for obj in rows:
        groups.setdefault(obj.subject_key, []).append(obj)
    return groups


def _pick_reference(members: list[ContextObjects]) -> tuple[ContextObjects, bool]:
    ordered = sorted(members, key=lambda o: (o.authority, o.valid_from), reverse=True)
    reference = ordered[0]
    upstream_conflict = any(m.status == "conflicting" for m in members)
    return reference, upstream_conflict


def _downstream_counterpart(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    to_stage: str,
    subject_key: str,
    types: list[str],
) -> ContextObjects | None:
    candidates = (
        db.query(ContextObjects)
        .filter(
            ContextObjects.tenant_id == tenant_id,
            ContextObjects.entity_id == entity_id,
            ContextObjects.stage == to_stage,
            ContextObjects.subject_key == subject_key,
            ContextObjects.type.in_(types),
            ContextObjects.status == "active",
        )
        .order_by(ContextObjects.authority.desc(), ContextObjects.valid_from.desc())
        .all()
    )
    return candidates[0] if candidates else None


def _validate_propagate_field(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    contract: ContractSpec,
    field: ContractField,
) -> list[GapDraft]:
    out: list[GapDraft] = []
    groups = _upstream_groups(db, tenant_id, entity_id, contract.from_stage, field)
    for subject_key, members in groups.items():
        u, upstream_conflict = _pick_reference(members)
        d = _downstream_counterpart(db, tenant_id, entity_id, contract.to_stage, subject_key, field.types)

        if d is None:
            severity, band = _severity(field.importance, u.authority, "object_missing")
            out.append(
                GapDraft(
                    contract_field=field.name,
                    upstream_id=u.id,
                    downstream_id=None,
                    slot=None,
                    outcome="object_missing",
                    severity=severity,
                    severity_band=band,
                    inherited=False,
                    upstream_conflict=upstream_conflict,
                    explanation=_explain(
                        outcome="object_missing", contract_id=contract.id, field=field,
                        slot=None, u=u, d=None, u_val=None, d_val=None, note=None,
                    ),
                )
            )
            continue

        for slot in field.slots:
            eff_obj, u_val, walked_up = _effective_upstream_value(db, u, slot)
            if not _is_set(u_val):
                continue
            d_val = (d.attributes or {}).get(slot)
            result = _classify_slot(
                db=db, slot=slot, subject_key=u.subject_key, u_val=u_val, d_val=d_val,
                d_attrs=d.attributes or {}, eff_obj=eff_obj,
            )
            if result is None:
                continue
            outcome, note = result
            severity, band = _severity(field.importance, eff_obj.authority, outcome)
            out.append(
                GapDraft(
                    contract_field=field.name,
                    upstream_id=eff_obj.id,
                    downstream_id=d.id,
                    slot=slot,
                    outcome=outcome,
                    severity=severity,
                    severity_band=band,
                    inherited=walked_up,
                    upstream_conflict=upstream_conflict,
                    explanation=_explain(
                        outcome=outcome, contract_id=contract.id, field=field, slot=slot,
                        u=eff_obj, d=d, u_val=u_val, d_val=d_val, note=note,
                    ),
                )
            )
    return out


def _validate_present_field(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    contract: ContractSpec,
    field: ContractField,
) -> list[GapDraft]:
    out: list[GapDraft] = []
    downstream_objs = (
        db.query(ContextObjects)
        .filter(
            ContextObjects.tenant_id == tenant_id,
            ContextObjects.entity_id == entity_id,
            ContextObjects.stage == contract.to_stage,
            ContextObjects.type.in_(field.types),
            ContextObjects.status == "active",
        )
        .all()
    )

    def _draft(obj: ContextObjects, slot: str | None, reason: str) -> GapDraft:
        severity, band = _severity(field.importance, obj.authority, "missing")
        explanation = (
            f"**Missing evidence link** — downstream {obj.type} \"{obj.content}\" "
            f"({obj.stage} {obj.valid_from.date().isoformat()}) {reason}. "
            f"Contract `{contract.id}.{field.name}` ({field.importance}) requires this."
        )
        return GapDraft(
            contract_field=field.name, upstream_id=None, downstream_id=obj.id, slot=slot,
            outcome="missing", severity=severity, severity_band=band, inherited=False,
            upstream_conflict=False, explanation=explanation,
        )

    for obj in downstream_objs:
        if field.rule == "has_derived_from":
            has_lineage = (
                db.query(ContextRelations)
                .filter(ContextRelations.relation == "derived_from", ContextRelations.from_id == obj.id)
                .first()
                is not None
            )
            if not has_lineage:
                out.append(_draft(obj, None, "has no `derived_from` link back to upstream evidence"))
        elif field.rule == "has_customer_contact":
            has_contact = (
                db.query(ContextObjects)
                .filter(
                    ContextObjects.tenant_id == tenant_id,
                    ContextObjects.entity_id == entity_id,
                    ContextObjects.stage == contract.to_stage,
                    ContextObjects.type == "dependency",
                    ContextObjects.actor_role == "customer",
                )
                .first()
                is not None
            )
            if not has_contact:
                out.append(_draft(obj, None, "has no captured customer contact"))
        else:
            for slot in field.slots:
                if not _is_set((obj.attributes or {}).get(slot)):
                    out.append(_draft(obj, slot, f"is missing `{slot}`"))
    return out


def _input_hash(db: Session, tenant_id: uuid.UUID, entity_id: uuid.UUID, contract: ContractSpec) -> str:
    rows = (
        db.query(ContextObjects.id, ContextObjects.version)
        .filter(
            ContextObjects.tenant_id == tenant_id,
            ContextObjects.entity_id == entity_id,
            ContextObjects.stage.in_([contract.from_stage, contract.to_stage]),
        )
        .order_by(ContextObjects.id)
        .all()
    )
    basis = f"{contract.id}|" + "|".join(f"{i}:{v}" for i, v in rows)
    return hashlib.sha256(basis.encode()).hexdigest()


def _summarize(gaps: list[GapDraft]) -> dict[str, Any]:
    by_outcome: dict[str, int] = {}
    by_band: dict[str, int] = {}
    inherited = 0
    for g in gaps:
        by_outcome[g.outcome] = by_outcome.get(g.outcome, 0) + 1
        by_band[g.severity_band] = by_band.get(g.severity_band, 0) + 1
        if g.inherited:
            inherited += 1
    return {
        "total": len(gaps),
        "by_outcome": by_outcome,
        "by_severity_band": by_band,
        "inherited": inherited,
        "origin": len(gaps) - inherited,
    }


def validate_handoff(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    contract_id: str,
    as_of: datetime | None = None,
) -> HandoffValidations:
    """Runs the full §10 algorithm for one `(entity, contract)` handoff and persists the
    validation + every resulting gap. Re-running is safe (each call is a fresh
    `handoff_validations` row plus fresh `context_gaps` rows — history is never deleted,
    matching §7.3's "nothing is deleted" invariant for the rest of the schema)."""
    contract = get_contract(db, contract_id)
    if contract is None:
        raise ValueError(f"unknown contract: {contract_id}")

    as_of = as_of or datetime.now(UTC)
    drafts: list[GapDraft] = []
    for field in contract.fields:
        if field.check == "propagate":
            drafts.extend(_validate_propagate_field(db, tenant_id, entity_id, contract, field))
        else:
            drafts.extend(_validate_present_field(db, tenant_id, entity_id, contract, field))

    validation = HandoffValidations(
        id=uuid.uuid4(),
        entity_id=entity_id,
        contract_id=contract.id,
        as_of=as_of,
        input_hash=_input_hash(db, tenant_id, entity_id, contract),
        summary=_summarize(drafts),
        created_at=datetime.now(UTC),
    )
    db.add(validation)
    db.flush()

    for draft in drafts:
        db.add(
            ContextGaps(
                id=uuid.uuid4(),
                validation_id=validation.id,
                contract_field=draft.contract_field,
                upstream_id=draft.upstream_id,
                downstream_id=draft.downstream_id,
                slot=draft.slot,
                outcome=draft.outcome,
                severity=draft.severity,
                severity_band=draft.severity_band,
                inherited=draft.inherited,
                upstream_conflict=draft.upstream_conflict,
                explanation=draft.explanation,
                status="open",
            )
        )
    db.commit()
    db.refresh(validation)
    return validation
