"""GET /people/resolve, GET /dashboard — the personalized landing page.

"tan@tenet.gmail.com logs in, it checks I'm in engineering, brings me the dashboard" (pasted
product spec): `/people/resolve` is the real lookup behind that — a query against the same
`people` table §7.3's authority rules and §14's ACL already use, not a mock. There is no
session/token layer yet (architecture v2 §8, not built), so the frontend holds the resolved
person in browser storage rather than a server session; every dashboard request still re-
proves its numbers against real tables, nothing is cached client-side except identity.

`scope=org` (for a C-suite/leadership view) drops the team filter and aggregates across the
whole tenant; `scope=personal` (everyone else) filters every widget to the caller's own team.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.context import context_object_to_out
from app.core.config import get_settings
from app.core.db import get_db
from app.models.orm import ContextGaps, ContextObjects, People, Sources
from app.schemas.dashboard import DashboardOut, DeadlineOut, PersonOut

router = APIRouter()


@router.get("/people/resolve", response_model=PersonOut)
def resolve_person(email: str = Query(...), db: Session = Depends(get_db)) -> PersonOut:
    tenant_id = uuid.UUID(get_settings().tenant_id)
    person = (
        db.query(People)
        .filter(People.tenant_id == tenant_id, People.email == email.strip().lower())
        .first()
    )
    if person is None:
        raise HTTPException(status_code=404, detail=f"no directory entry for {email!r}")
    return PersonOut(
        id=person.id, name=person.name, email=person.email, team=person.team, role=person.role
    )


def _scoped(query, team: str | None):
    if team is not None:
        query = query.filter(ContextObjects.stage == team)
    return query


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(
    scope: str = Query(default="personal", pattern="^(personal|org)$"),
    team: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> DashboardOut:
    if scope == "personal" and not team:
        raise HTTPException(status_code=422, detail="team is required when scope=personal")
    effective_team = team if scope == "personal" else None

    contradictions = (
        _scoped(db.query(ContextObjects).filter(ContextObjects.status == "conflicting"), effective_team)
        .order_by(ContextObjects.updated_at.desc())
        .limit(20)
        .all()
    )

    decisions_pending = (
        _scoped(
            db.query(ContextObjects).filter(
                ContextObjects.type == "decision", ContextObjects.status == "candidate"
            ),
            effective_team,
        )
        .order_by(ContextObjects.created_at.desc())
        .limit(20)
        .all()
    )

    active_with_due_date = _scoped(
        db.query(ContextObjects).filter(
            ContextObjects.status == "active",
            ContextObjects.attributes["due_date"].isnot(None),
        ),
        effective_team,
    ).all()
    today = datetime.now(UTC).date()
    deadlines: list[DeadlineOut] = []
    for obj in active_with_due_date:
        raw = obj.attributes.get("due_date")
        if not raw:
            continue
        due = date.fromisoformat(raw)
        source = db.get(Sources, obj.source_id) if obj.source_id else None
        deadlines.append(
            DeadlineOut(
                object=context_object_to_out(obj, source, principals=None),
                due_date=due,
                due_date_precision=obj.attributes.get("due_date_precision"),
                overdue=due < today,
            )
        )
    deadlines.sort(key=lambda d: d.due_date)
    deadlines = deadlines[:20]

    open_gaps_query = db.query(ContextGaps.id).filter(ContextGaps.status == "open")
    if effective_team is not None:
        # A gap's upstream/downstream object carries the team; join through whichever side
        # is present (upstream is nullable for present-check gaps — migration 0004).
        open_gaps_query = open_gaps_query.join(
            ContextObjects,
            (ContextObjects.id == ContextGaps.upstream_id)
            | (ContextObjects.id == ContextGaps.downstream_id),
        ).filter(ContextObjects.stage == effective_team)
    open_gap_ids = [row.id for row in open_gaps_query.distinct().limit(50)]
    degradation_count = (
        db.query(ContextGaps.id).filter(ContextGaps.status == "open").count()
        if effective_team is None
        else len(open_gap_ids)
    )

    incident_query = db.query(ContextObjects.attributes).filter(
        ContextObjects.attributes["extra"]["incident_id"].isnot(None)
    )
    if effective_team is not None:
        incident_query = incident_query.filter(ContextObjects.stage == effective_team)
    incident_id_set: set[str] = set()
    for row in incident_query.all():
        incident_id = (row.attributes.get("extra") or {}).get("incident_id")
        if incident_id:
            incident_id_set.add(str(incident_id))
    incident_ids = sorted(incident_id_set)

    return DashboardOut(
        scope=scope,
        team=effective_team,
        contradictions=[context_object_to_out(o, db.get(Sources, o.source_id), None) for o in contradictions],
        decisions_pending=[
            context_object_to_out(o, db.get(Sources, o.source_id), None) for o in decisions_pending
        ],
        deadlines_incoming=deadlines,
        degradation_gaps=open_gap_ids,
        degradation_count=degradation_count,
        incidents=incident_ids,
    )
