"""Incident Context Pack — GET /incidents, POST /incidents (declare), GET /incidents/{id},
POST /incidents/{id}/link, POST /incidents/{id}/resolve.

Backed by a first-class `incidents` table since migration 0009. Before this, "everything
about INC-2311" was reconstructed at read time from a subject_key pattern
(`%:incident%`) plus a 72h time-window guess around the one object that happened to carry a
structured `incident_id` in `attributes.extra` — checked the real dev database before
building that version, and found exactly one object out of the whole incident carried a
real id; everything else only had `capability="incident"`, because nothing in the pipeline
gave the extractor a way to stamp an ongoing incident's id onto a live message as it
arrived. That heuristic was honest about its own limits (see the pre-0009 version of this
file) but only worked because the seed data has exactly one incident.

This version keeps the same honesty, moved from query-time to declare-time: `declare()`
still only auto-confirms a link for objects that genuinely carry the structured
incident_id, and still only *suggests* (never auto-confirms) same-capability/same-window
objects as candidates (`IncidentContextObjects.linked=False`) — but now that's a durable,
reviewable row instead of a heuristic recomputed on every GET. The real remaining gap:
nothing auto-declares an incident or auto-links a NEW live message to an already-open one
yet — `declare()` is a one-time action (backfill script, or a future Slack "P0 declared"
command / dashboard button), not a pipeline hook. That's the next piece, not this one.

"Previous similar incidents" is a real field now (it was a hardcoded `[]` before, since
there was no clean incident boundary to compare against) — but deliberately the simple,
honest version: other RESOLVED incidents sharing at least one affected entity, not
embedding-similarity-ranked. Noted as a real simplification, not hidden.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.context import context_object_to_out
from app.core.config import get_settings
from app.core.db import get_db
from app.models.orm import (
    ContextGaps,
    ContextObjects,
    Entities,
    HandoffValidations,
    IncidentContextObjects,
    IncidentEntities,
    Incidents,
    Sources,
)
from app.schemas.context import ContextObjectOut
from app.schemas.incidents import (
    DeclareIncidentRequest,
    IncidentContextPackOut,
    IncidentPeopleOut,
    IncidentStatus,
    IncidentSummaryOut,
    IncidentTimelineEntryOut,
    LinkObjectRequest,
    Severity,
)

router = APIRouter()

_UNCONFIRMED_WINDOW = timedelta(hours=72)


def _tenant_id() -> uuid.UUID:
    return uuid.UUID(get_settings().tenant_id)


def _entity_names(db: Session, incident: Incidents) -> list[str]:
    rows = (
        db.query(Entities.name)
        .join(IncidentEntities, IncidentEntities.entity_id == Entities.id)
        .filter(IncidentEntities.incident_id == incident.id)
        .all()
    )
    return sorted(name for (name,) in rows)


def _summary(db: Session, incident: Incidents) -> IncidentSummaryOut:
    object_count = (
        db.query(IncidentContextObjects)
        .filter(IncidentContextObjects.incident_id == incident.id, IncidentContextObjects.linked.is_(True))
        .count()
    )
    return IncidentSummaryOut(
        id=incident.id,
        incident_id=incident.incident_id,
        title=incident.title,
        severity=cast(Severity, incident.severity),
        status=cast(IncidentStatus, incident.status),
        entities=_entity_names(db, incident),
        declared_at=incident.declared_at,
        resolved_at=incident.resolved_at,
        object_count=object_count,
    )


@router.get("/incidents", response_model=list[IncidentSummaryOut])
def list_incidents(db: Session = Depends(get_db)) -> list[IncidentSummaryOut]:
    tenant_id = _tenant_id()
    incidents = (
        db.query(Incidents)
        .filter(Incidents.tenant_id == tenant_id)
        .order_by(Incidents.declared_at.desc())
        .all()
    )
    return [_summary(db, i) for i in incidents]


@router.post("/incidents", response_model=IncidentSummaryOut)
def declare_incident(body: DeclareIncidentRequest, db: Session = Depends(get_db)) -> IncidentSummaryOut:
    """Declare a new incident and auto-discover its evidence.

    Confirmed links: objects that already carry `attributes.extra.incident_id ==
    body.incident_id`. Candidates: bare `capability="incident"` objects inside a window
    around those confirmed objects' timestamps — same heuristic the old read-time version
    used, now materialized as rows instead of recomputed on every read. If nothing carries
    the structured id yet (the live "P0 declared" case, not a backfill), the window anchors
    on the declaration moment itself instead of finding nothing.
    """
    tenant_id = _tenant_id()
    existing = (
        db.query(Incidents)
        .filter(Incidents.tenant_id == tenant_id, Incidents.incident_id == body.incident_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"incident {body.incident_id!r} already declared")

    for entity_id in body.entity_ids:
        if db.get(Entities, entity_id) is None:
            raise HTTPException(status_code=404, detail=f"entity {entity_id} not found")

    incident = Incidents(
        tenant_id=tenant_id,
        incident_id=body.incident_id,
        title=body.title,
        severity=body.severity,
        declared_at=body.anchor_at or datetime.now(UTC),
    )
    db.add(incident)
    db.flush()

    for entity_id in body.entity_ids:
        db.add(IncidentEntities(incident_id=incident.id, entity_id=entity_id))

    confirmed = (
        db.query(ContextObjects)
        .filter(
            ContextObjects.tenant_id == tenant_id,
            ContextObjects.attributes["extra"]["incident_id"].astext == body.incident_id,
        )
        .all()
    )
    for obj in confirmed:
        db.add(IncidentContextObjects(incident_id=incident.id, context_object_id=obj.id, linked=True))

    sources = [db.get(Sources, o.source_id) for o in confirmed if o.source_id]
    timestamps = [s.source_ts for s in sources if s is not None]
    if timestamps:
        # The usual case: anchor the candidate window on the confirmed objects' own
        # narrative timestamps.
        lo, hi = min(timestamps) - _UNCONFIRMED_WINDOW, max(timestamps) + _UNCONFIRMED_WINDOW
    else:
        # Nothing anywhere carries the structured incident_id yet -- the realistic case for
        # a live "P0 declared" moment (the pasted mockup's own framing), not just a backfill
        # of already-resolved history. Anchor on the declaration itself rather than finding
        # nothing: a human declaring a P0 right now is exactly the signal that should pull
        # in nearby same-capability chatter as review candidates.
        lo = incident.declared_at - _UNCONFIRMED_WINDOW
        hi = incident.declared_at + _UNCONFIRMED_WINDOW

    confirmed_ids = {o.id for o in confirmed}
    # A candidate must plausibly be ABOUT one of this incident's declared customers, or be
    # generic internal chatter that never named one (entity resolution's own
    # DEFAULT_ENTITY_HINT = "Unknown" fallback — a real, recurring pattern for on-call
    # messages, not the same thing as "belongs to a different incident"). Without this, the
    # time-window alone lets two unrelated incidents on different customers pull in each
    # other's objects the moment their windows overlap — found live via cross-test
    # contamination while adding this endpoint's test coverage, not theoretical.
    allowed_entity_ids = set(body.entity_ids) | {
        row.id for row in db.query(Entities.id).filter(Entities.tenant_id == tenant_id, Entities.name == "Unknown")
    }
    candidates = (
        db.query(ContextObjects)
        .join(Sources, ContextObjects.source_id == Sources.id)
        .filter(
            ContextObjects.tenant_id == tenant_id,
            ContextObjects.entity_id.in_(allowed_entity_ids),
            or_(
                ContextObjects.subject_key.like("%:incident"),
                ContextObjects.subject_key == "unknown:incident",
            ),
            Sources.source_ts >= lo,
            Sources.source_ts <= hi,
        )
        .all()
    )
    for obj in candidates:
        if obj.id in confirmed_ids:
            continue
        db.add(IncidentContextObjects(incident_id=incident.id, context_object_id=obj.id, linked=False))

    db.commit()
    db.refresh(incident)
    return _summary(db, incident)


@router.post("/incidents/{incident_id}/link", response_model=IncidentSummaryOut)
def link_object(incident_id: str, body: LinkObjectRequest, db: Session = Depends(get_db)) -> IncidentSummaryOut:
    """Manually confirm or add a candidate link — the review action a human takes on an
    `unconfirmed` timeline entry, or a future pipeline hook uses to attach a new message to
    an already-open incident."""
    tenant_id = _tenant_id()
    incident = (
        db.query(Incidents)
        .filter(Incidents.tenant_id == tenant_id, Incidents.incident_id == incident_id)
        .first()
    )
    if incident is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id!r} not found")
    obj = db.get(ContextObjects, body.context_object_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="context object not found")

    existing = db.get(IncidentContextObjects, (incident.id, body.context_object_id))
    if existing is not None:
        existing.linked = body.linked
    else:
        db.add(
            IncidentContextObjects(
                incident_id=incident.id, context_object_id=body.context_object_id, linked=body.linked
            )
        )
    db.commit()
    return _summary(db, incident)


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentSummaryOut)
def resolve_incident(incident_id: str, db: Session = Depends(get_db)) -> IncidentSummaryOut:
    tenant_id = _tenant_id()
    incident = (
        db.query(Incidents)
        .filter(Incidents.tenant_id == tenant_id, Incidents.incident_id == incident_id)
        .first()
    )
    if incident is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id!r} not found")
    incident.status = "resolved"
    incident.resolved_at = datetime.now(UTC)
    db.commit()
    return _summary(db, incident)


@router.get("/incidents/{incident_id}", response_model=IncidentContextPackOut)
def get_incident_pack(incident_id: str, db: Session = Depends(get_db)) -> IncidentContextPackOut:
    tenant_id = _tenant_id()
    incident = (
        db.query(Incidents)
        .filter(Incidents.tenant_id == tenant_id, Incidents.incident_id == incident_id)
        .first()
    )
    if incident is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id!r} not found")

    links = (
        db.query(IncidentContextObjects).filter(IncidentContextObjects.incident_id == incident.id).all()
    )
    objects_by_id = {
        o.id: o
        for o in db.query(ContextObjects).filter(
            ContextObjects.id.in_([link.context_object_id for link in links])
        )
    }

    timeline: list[IncidentTimelineEntryOut] = []
    all_objs: list[ContextObjects] = []
    for link in links:
        obj = objects_by_id.get(link.context_object_id)
        if obj is None:
            continue
        all_objs.append(obj)
        source = db.get(Sources, obj.source_id) if obj.source_id else None
        at = source.source_ts if source else obj.created_at
        timeline.append(
            IncidentTimelineEntryOut(
                object=context_object_to_out(obj, source, principals=None), at=at, linked=link.linked
            )
        )
    timeline.sort(key=lambda e: e.at)

    people_counts: dict[tuple[str, str], int] = {}
    for obj in all_objs:
        key = (obj.actor_label or "Unknown", obj.actor_role)
        people_counts[key] = people_counts.get(key, 0) + 1
    people = [
        IncidentPeopleOut(actor_label=label, actor_role=role, object_count=n)
        for (label, role), n in sorted(people_counts.items(), key=lambda kv: -kv[1])
    ]

    def _latest_active_with(attr_key: str) -> ContextObjects | None:
        candidates = [
            o for o in all_objs if o.status == "active" and (o.attributes or {}).get(attr_key) is not None
        ]
        return max(candidates, key=lambda o: o.created_at, default=None)

    impact_obj = _latest_active_with("impact")
    sla_obj = _latest_active_with("sla_impact")
    root_cause_obj = _latest_active_with("root_cause")
    remediation_obj = _latest_active_with("remediation")

    affected_entity_ids = [
        eid
        for (eid,) in db.query(IncidentEntities.entity_id).filter(IncidentEntities.incident_id == incident.id)
    ]
    validation_ids = [
        v.id
        for v in db.query(HandoffValidations).filter(HandoffValidations.entity_id.in_(affected_entity_ids))
    ] if affected_entity_ids else []
    open_gap_ids = (
        [
            g.id
            for g in db.query(ContextGaps.id).filter(
                ContextGaps.validation_id.in_(validation_ids), ContextGaps.status == "open"
            )
        ]
        if validation_ids
        else []
    )

    similar: list[Incidents] = []
    if affected_entity_ids:
        similar = (
            db.query(Incidents)
            .join(IncidentEntities, IncidentEntities.incident_id == Incidents.id)
            .filter(
                Incidents.tenant_id == tenant_id,
                Incidents.id != incident.id,
                Incidents.status == "resolved",
                IncidentEntities.entity_id.in_(affected_entity_ids),
            )
            .distinct()
            .order_by(Incidents.declared_at.desc())
            .limit(5)
            .all()
        )

    def _out(obj: ContextObjects | None) -> ContextObjectOut | None:
        if obj is None:
            return None
        return context_object_to_out(obj, db.get(Sources, obj.source_id), None)

    return IncidentContextPackOut(
        id=incident.id,
        incident_id=incident.incident_id,
        title=incident.title,
        severity=cast(Severity, incident.severity),
        status=cast(IncidentStatus, incident.status),
        entities=_entity_names(db, incident),
        timeline=timeline,
        people=people,
        impact=_out(impact_obj),
        sla_impact=_out(sla_obj),
        root_cause=_out(root_cause_obj),
        remediation=_out(remediation_obj),
        open_gaps=open_gap_ids,
        similar_past_incidents=[_summary(db, s) for s in similar],
    )
