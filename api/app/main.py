"""FastAPI app (§15): /health, /sources/mock/load, /sources/slack/events,
/sources/slack/interactions, /context/*, /entities/*, /conflicts/*, /gaps/*, /handoffs/*,
/jobs/stats.

CORS is enabled for the local dashboard dev server (`web/`, Vite on 5173 by default) —
the API has no cookie-based auth in the MVP (§14, principals are a query param), so an
open dev-origin allowlist carries no session-hijack risk. Origins are read from
`DASHBOARD_ORIGINS` (comma-separated) so this stays configurable without code changes.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import conflicts, context, entities, gaps, handoffs, health, jobs, slack, sources
from app.core.config import get_settings

app = FastAPI(title="Milieu — Context Continuity Engine")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.dashboard_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(sources.router)
app.include_router(slack.router)
app.include_router(context.router)
app.include_router(entities.router)
app.include_router(conflicts.router)
app.include_router(gaps.router)
app.include_router(handoffs.router)
app.include_router(jobs.router)
