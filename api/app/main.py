"""FastAPI app (§15). Day 1a routes only: /health, /sources/mock/load, /context/{id},
/jobs/stats."""

from __future__ import annotations

from fastapi import FastAPI

from app.api import context, health, jobs, sources

app = FastAPI(title="Milieu — Context Continuity Engine")

app.include_router(health.router)
app.include_router(sources.router)
app.include_router(context.router)
app.include_router(jobs.router)
