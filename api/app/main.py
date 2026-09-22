"""FastAPI app (§15): /health, /sources/mock/load, /sources/slack/events,
/sources/slack/interactions, /context/{id}, /jobs/stats."""

from __future__ import annotations

from fastapi import FastAPI

from app.api import context, health, jobs, slack, sources

app = FastAPI(title="Milieu — Context Continuity Engine")

app.include_router(health.router)
app.include_router(sources.router)
app.include_router(slack.router)
app.include_router(context.router)
app.include_router(jobs.router)
