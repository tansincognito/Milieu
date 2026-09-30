"""FastAPI app (§15): /health, /sources/mock/load, /sources/slack/events,
/sources/slack/interactions, /context/*, /entities/*, /conflicts/*, /gaps/*, /handoffs/*,
/flows, /incidents/*, /tenant, /connections, /departments, /jobs/stats.

CORS is enabled for the local dashboard dev server (`web/`, Vite on 5173 by default) —
the API has no cookie-based auth in the MVP (§14, principals are a query param), so an
open dev-origin allowlist carries no session-hijack risk. Origins are read from
`DASHBOARD_ORIGINS` (comma-separated) so this stays configurable without code changes.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    conflicts,
    context,
    entities,
    gaps,
    handoffs,
    health,
    incidents,
    jobs,
    org_setup,
    slack,
    sources,
)
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.pipeline.contracts import load_contracts

logger = logging.getLogger(__name__)

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Seed the §9 Context Contracts on boot.

    `load_contracts` is an idempotent upsert by contract id, so this is safe on every
    restart. Without it the contracts table stays empty in a running app — they were only
    ever loaded by the test fixtures — which leaves `GET /contracts` empty and makes
    `POST /handoffs/validate-all` return 409 on a freshly started stack.
    """
    try:
        with SessionLocal() as db:
            ids = load_contracts(db, CONTRACTS_DIR)
        logger.info("loaded %d context contracts from %s", len(ids), CONTRACTS_DIR)
    except Exception:
        # A contract-seeding failure must not stop the API from serving: every other
        # endpoint works without contracts, and /handoffs reports the empty state clearly.
        logger.exception("failed to load context contracts from %s", CONTRACTS_DIR)
    yield


app = FastAPI(title="Milieu — Context Continuity Engine", lifespan=lifespan)

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
app.include_router(incidents.router)
app.include_router(org_setup.router)
app.include_router(jobs.router)
