"""Incident Context Pack — GET /incidents, GET /incidents/{incident_id}.

The pasted product spec asks for "rapidly reconstruct everything relevant" for a live
incident: timeline, impact, root cause, remediation, people, and past similar incidents.
Checked the real data in this repo's own dev database before building this (not assumed):
of every INC-2311-related object, exactly ONE carries a structured
`attributes.extra.incident_id` — the postmortem document's own sections, tagged via
`process._sibling_subject_key`. Every #incidents Slack message just has
`subject_capability="incident"` with no incident_id at all, because nothing in the pipeline
today gives the bot a way to stamp an ongoing incident's id onto a live message as it
arrives.

That means there is no reliable machine key joining "everything about INC-2311" across
sources — only within the one document that happens to name it. This endpoint is built to
be honest about that rather than hide it: objects that genuinely carry the incident_id are
`linked=true`; objects that only match by capability and fall inside the linked objects'
time window are `linked=false` and shown separately, clearly labeled unconfirmed. Today
there is exactly one incident in the seed data, so the time-window heuristic happens to be
unambiguous — a second concurrent incident would break it, which is the real fix still
needed: a live incident needs its id available at capture time (e.g. a Slack thread's root
message carrying it, propagated to every reply), not reconstructed after the fact.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.context import context_object_to_out
from app.core.db import get_db
from app.models.orm import ContextGaps, ContextObjects, Entities, HandoffValidations, Sources
from app.schemas.incidents import (
    IncidentContextPackOut,
    IncidentPeopleOut,
    IncidentSummaryOut,
    IncidentTimelineEntryOut,
)

router = APIRouter()

_UNCONFIRMED_WINDOW = timedelta(hours=72)


def _linked_objects(db: Session, incident_id: str) -> list[ContextObjects]:
    return (
        db.query(ContextObjects)
        .filter(ContextObjects.attributes["extra"]["incident_id"].astext == incident_id)
        .all()
    )


def _unconfirmed_candidates(
    db: Session, linked: list[ContextObjects], window: timedelta
) -> list[ContextObjects]:
    """Bare `capability=incident` objects (no incident_id at all) inside the linked set's
    time window. See module docstring — this is a real but unproven signal."""
    if not linked:
        return []
    sources_by_obj = {
        o.id: db.get(Sources, o.source_id) for o in linked if o.source_id is not None
    }
    timestamps = [s.source_ts for s in sources_by_obj.values() if s is not None]
    if not timestamps:
        return []
    lo, hi = min(timestamps) - window, max(timestamps) + window
    linked_ids = {o.id for o in linked}

    candidates = (
        db.query(ContextObjects)
        .join(Sources, ContextObjects.source_id == Sources.id)
        .filter(
            or_(
                ContextObjects.subject_key.like("%:incident"),
                ContextObjects.subject_key == "unknown:incident",
            ),
            Sources.source_ts >= lo,
            Sources.source_ts <= hi,
        )
        .all()
    )
    return [c for c in candidates if c.id not in linked_ids]


@router.get("/incidents", response_model=list[IncidentSummaryOut])
def list_incidents(db: Session = Depends(get_db)) -> list[IncidentSummaryOut]:
    """Every incident_id that appears anywhere in `attributes.extra.incident_id` — today
    that is exactly the ones a drive postmortem document has named."""
    rows = (
        db.query(ContextObjects)
        .filter(ContextObjects.attributes["extra"]["incident_id"].isnot(None))
        .all()
    )
    by_id: dict[str, list[ContextObjects]] = {}
    for row in rows:
        incident_id = (row.attributes.get("extra") or {}).get("incident_id")
        if incident_id:
            by_id.setdefault(incident_id, []).append(row)

    out: list[IncidentSummaryOut] = []
    for incident_id, objs in by_id.items():
        entities_for_group = (db.get(Entities, o.entity_id) for o in objs if o.entity_id)
        entity_names = {e.name for e in entities_for_group if e is not None}
        sources = [db.get(Sources, o.source_id) for o in objs if o.source_id]
        first_seen = min((s.source_ts for s in sources if s), default=None)
        out.append(
            IncidentSummaryOut(
                incident_id=incident_id,
                entities=sorted(entity_names),
                first_seen_at=first_seen,
                object_count=len(objs),
            )
        )
    return sorted(out, key=lambda i: i.first_seen_at or "", reverse=True)


@router.get("/incidents/{incident_id}", response_model=IncidentContextPackOut)
def get_incident_pack(incident_id: str, db: Session = Depends(get_db)) -> IncidentContextPackOut:
    linked = _linked_objects(db, incident_id)
    if not linked:
        raise HTTPException(status_code=404, detail=f"no objects carry incident_id={incident_id!r}")

    unconfirmed = _unconfirmed_candidates(db, linked, _UNCONFIRMED_WINDOW)

    entity_names: set[str] = set()
    timeline: list[IncidentTimelineEntryOut] = []
    for obj, is_linked in [(o, True) for o in linked] + [(o, False) for o in unconfirmed]:
        entity = db.get(Entities, obj.entity_id) if obj.entity_id else None
        if entity:
            entity_names.add(entity.name)
        source = db.get(Sources, obj.source_id) if obj.source_id else None
        at = source.source_ts if source else obj.created_at
        timeline.append(
            IncidentTimelineEntryOut(
                object=context_object_to_out(obj, source, principals=None),
                at=at,
                linked=is_linked,
            )
        )
    timeline.sort(key=lambda e: e.at)

    people_counts: dict[tuple[str, str], int] = {}
    for obj in linked + unconfirmed:
        key = (obj.actor_label or "Unknown", obj.actor_role)
        people_counts[key] = people_counts.get(key, 0) + 1
    people = [
        IncidentPeopleOut(actor_label=label, actor_role=role, object_count=n)
        for (label, role), n in sorted(people_counts.items(), key=lambda kv: -kv[1])
    ]

    def _latest_active_with(attr_key: str) -> ContextObjects | None:
        candidates = [
            o
            for o in linked + unconfirmed
            if o.status == "active" and (o.attributes or {}).get(attr_key) is not None
        ]
        return max(candidates, key=lambda o: o.created_at, default=None)

    impact_obj = _latest_active_with("impact")
    sla_obj = _latest_active_with("sla_impact")
    root_cause_obj = _latest_active_with("root_cause")
    remediation_obj = _latest_active_with("remediation")

    affected_entity_ids = {o.entity_id for o in linked + unconfirmed if o.entity_id}
    validation_ids = [
        v.id
        for v in db.query(HandoffValidations).filter(
            HandoffValidations.entity_id.in_(affected_entity_ids)
        )
    ]
    open_gap_ids = [
        g.id
        for g in db.query(ContextGaps.id).filter(
            ContextGaps.validation_id.in_(validation_ids), ContextGaps.status == "open"
        )
    ] if validation_ids else []

    return IncidentContextPackOut(
        incident_id=incident_id,
        entities=sorted(entity_names),
        timeline=timeline,
        people=people,
        impact=context_object_to_out(impact_obj, db.get(Sources, impact_obj.source_id), None)
        if impact_obj
        else None,
        sla_impact=context_object_to_out(sla_obj, db.get(Sources, sla_obj.source_id), None)
        if sla_obj
        else None,
        root_cause=context_object_to_out(
            root_cause_obj, db.get(Sources, root_cause_obj.source_id), None
        )
        if root_cause_obj
        else None,
        remediation=context_object_to_out(
            remediation_obj, db.get(Sources, remediation_obj.source_id), None
        )
        if remediation_obj
        else None,
        open_gaps=open_gap_ids,
        # Honest empty state: the seed data has exactly one incident. A real "similar past
        # incidents" feature needs embedding similarity over root_cause/impact text across
        # multiple resolved incidents, which there's nothing to compare against yet.
        similar_past_incidents=[],
    )
