# Milieu — Context Continuity Engine

A system that extracts structured, versioned, source-linked Context Objects from Slack, email, documents, and call transcripts. It resolves them into a single state per customer and detects when important context is lost, contradicted, or degraded across organizational handoffs (Sales → Product → Engineering, plus onboarding and incident flows).

**Not enterprise search. Not a RAG chatbot.** See [SPEC.md § 1 & § 39](docs/SPEC.md#1-product) for the full framing.

---

## Status

**MVP, Day 1-3 scope complete** (`feature/mvp-1.0`):
- **Day 1** ✓ multi-source ingestion (Slack, email, drive, calls) → Context Objects with live-verified extraction and Slack endpoints.
- **Day 2** ✓ entity resolution, dedup, lifecycle/supersession/conflict rules (R1-R5), lineage, query + review APIs.
- **Day 3** ✓ Context Contracts, handoff validator, degradation detection, evals.
- **Dashboard** ✓ personalized landing page (contradictions, pending decisions, deadlines, gaps, incidents), entity explorer, login + setup wizard.
- **Incidents** ✓ first-class `incidents` table (multi-entity, with "previous similar incidents") replacing an earlier subject_key-pattern heuristic.
- **Simulation layer** ✓ `tenants`/`connections`/`sync_runs` model a real connector's auth/cursor/failure surface against simulated data in `simulation_seed_sources`; mock connectors read from there (not disk) and `/connections/{kind}/sync` does a real ingest.
- Not built: real OAuth connectors (Gmail/Slack/Drive), server-side session/auth (every request is implicitly the one configured tenant).

See [docs/DECISIONS.md](docs/DECISIONS.md) for the concrete bugs found and fixed along the way, with the tradeoffs behind each one.

---

## Architecture at a glance

```
connections (mock, simulated) → ingest → normalize → queue → extract (evidence-bound)
  → resolve entity → dedupe → lifecycle/supersession/conflict (R1-R5) → persist → lineage
  → handoff validation → gaps/incidents → dashboard + entity explorer + review queue
```

**Stack:**
- **API & Worker:** FastAPI + async Python, `uv` for dependency management.
- **Database:** PostgreSQL 17 (pgvector for embeddings, pg_trgm for entity resolution).
- **Cache & Queue:** Redis (retrieval cache); job queue is Postgres-backed.
- **LLM:** dual-provider via a shared `LLMClient` interface — OpenRouter (free tier, pooled capacity, the default) with Groq as a fallback (own hardware, per-account token budget; see `app/llm/groq.py` for the pacing this required).
- **Embeddings:** fastembed local model (BAAI/bge-small-en-v1.5, 384 dims).
- **Sources:** real Slack endpoints + mock connectors for Drive, email, and call transcripts, backed by `simulation_seed_sources` in Postgres.
- **Orchestration:** Docker Compose for local dev (Postgres + Redis; API and worker run locally).

---

## Quick start

### 1. Clone and set up environment

```bash
cd /Users/ajaymac/Projects/Milieu
cp .env.example api/.env
```

Then edit `api/.env` and fill in `OPENROUTER_API_KEY` (get one free at https://openrouter.ai). Leave all other values as-is for local dev.

### 2. Start services

```bash
make up
```

This brings up PostgreSQL (port `5544`) and Redis (port `6389`) and waits for healthchecks. The API and worker are not containerized yet — run them locally, as shown below.

### 3. Run migrations

```bash
make migrate
```

This applies all Alembic migrations and seeds the capability vocabulary.

### 4. Load mock data (optional, for testing)

```bash
curl -X POST http://localhost:8000/sources/mock/load
```

This ingests all mock sources from `/mock-data` (Acme, Globex, and incident scenarios), extracts context, and triggers validation. For dev-only use; not for production.

### 5. Start the API and worker

In separate terminals:

```bash
cd api && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd api && uv run python -m app.worker
```

The API listens on `http://localhost:8000`. Health check: `GET http://localhost:8000/health`.

### Service ports (from `docker-compose.yml`)

| Service | Port | Default creds |
|---------|------|---|
| PostgreSQL | `5544` | user=`milieu`, password=`milieu`, db=`milieu` |
| Redis | `6389` | no auth |

---

## Testing

### Fast checks (no services required)

```bash
make check-fast
```

Runs:
- **Ruff linter** on `app` and `tests`.
- **mypy** type checker.
- **pytest** unit tests only (skips `integration` marker).

Completes in ~10 s.

### Full checks (requires docker-compose)

```bash
make check
```

Runs all of the above **plus** integration tests that hit PostgreSQL and Redis. Includes:
- Extraction and evidence-span validation.
- Entity resolution, dedup, and lifecycle state.
- Handoff validation and gap detection.
- API endpoint contracts.

Completes in ~1–2 min after services are ready.

---

## Repository layout

| Path | Purpose |
|------|---------|
| `/api` | FastAPI application, worker, Alembic migrations, Pydantic models. Entry point: `app.main:app`. Worker: `python -m app.worker`. |
| `/api/app` | Main package: `pipeline/` (extraction, entity resolution, lifecycle), `llm/`, `embedding/`, `queue/`, `connectors/`, `slack/`, `api/` (routes), `main.py`. |
| `/api/tests` | Unit and integration tests. Marked with `@pytest.mark.integration` for container tests. |
| `/api/alembic/versions` | Database migrations, `0001`-`0009` (entities/context/lifecycle → lifecycle/dedup columns → actor roles → gap nullability → capability vocab review → simulation/tenants/connections → leadership stage → sync runs → incidents). |
| `/docs` | `SPEC.md` (product + technical spec), `ARCHITECTURE-v2.md` (org-agnostic connector-first redesign, proposed), `DECISIONS.md` (concrete bugs found/fixed and the tradeoffs behind each fix, chronological). |
| `/mock-data` | Test fixtures: `drive/`, `email/`, `calls/`, `directory.json`, `slack/seed.json`. Mirrored into Postgres (`simulation_seed_sources`) by `app/pipeline/seed_simulation_sources.py`. |
| `/contracts` | Context Contract YAML seeds. |
| `/scripts` | Developer/ops scripts: `slot_probe.py` (how the extraction model/prompt were chosen), `import_mock_data_to_postgres.py` (standalone `simulation_seed_sources` refresh), `reconcile_false_contradictions.py` (re-evaluates existing `contradicts` relations under the current dedup logic and corrects false positives), `declare_seed_incidents.py`. |
| `docker-compose.yml` | Postgres (pgvector) and Redis only. The API and worker run locally via `uv`. |
| `Makefile` | `make up`, `make down`, `make migrate`, `make check-fast`, `make check`. |
| `.env.example` | Template for environment variables. Always keep empty-valued. Copy to `api/.env` and fill only locally. |

---

## Environment variables

See `.env.example`. Key vars for local dev:

```bash
# Database (matches docker-compose)
DATABASE_URL=postgresql+psycopg://milieu:milieu@localhost:5544/milieu
REDIS_URL=redis://localhost:6389/0

# LLM (required for extraction). LLM_PROVIDER is "openrouter" or "groq" (app/core/factories.py).
# OpenRouter's free models share OpenRouter's own pooled capacity across every free-tier
# user -- the real cause of sustained 429s, not the specific model. Groq's free tier runs on
# its own hardware with per-account limits, so it's the fallback when OpenRouter's pool
# saturates. Groq's real constraint is a tokens-per-minute budget (8000 TPM on the free
# tier), not request count -- see app/llm/groq.py's docstring before changing LLM_MODEL
# under Groq; qwen/qwen3.8-27b is ~2.2x more token-efficient than the gpt-oss-* reasoning
# models at the same extraction quality.
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=<your free API key from https://openrouter.ai>
GROQ_API_KEY=<optional, from https://console.groq.com>
LLM_MODEL=nvidia/nemotron-3.5-lightning:free

# Embeddings (local, no key needed)
EMBEDDING_PROVIDER=fastembed
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5

# Slack (optional; only for real Events API integration)
SLACK_BOT_TOKEN=<your bot token>
SLACK_SIGNING_SECRET=<your signing secret>

# Misc
TENANT_ID=00000000-0000-0000-0000-000000000001
PROMPT_VERSION=1
SCHEMA_VERSION=1
```

**Important:** Real secrets belong in `api/.env` (git-ignored). The tracked `.env.example` must always have empty values. Never commit secrets.

---

## API endpoints (core)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/sources/slack/events` | Slack Events API webhook |
| POST | `/sources/mock/load` | Ingest mock data (dev only) |
| GET | `/context/search` | Query active context |
| GET | `/entities` | List entities, scoped to the configured tenant |
| GET | `/entities/{id}/context` | Entity's grouped context, gaps, conflicts |
| GET | `/dashboard` | Personalized landing page (contradictions, pending decisions, deadlines, gaps, incidents) |
| POST | `/conflicts/{relation_id}/resolve` | Human resolution of an R4 conflict (R5) |
| POST | `/gaps/{id}/review` | Human review of a handoff gap |
| POST | `/incidents` | Declare an incident |
| POST | `/incidents/{id}/link` | Link an entity/context object to an incident |
| POST | `/handoffs/validate` | Run a handoff validation |
| GET | `/handoffs/{id}` | Validation result + gaps |
| POST | `/connections/{kind}/sync` | Sync a connection (`?simulate=rate_limited\|auth_expired\|failed` for a reproducible failure; otherwise a real ingest) |
| GET | `/simulation/search` | Full-text search over simulated source content |
| GET | `/health` | Health check (DB, Redis, queue depth) |

See [SPEC.md § 15](docs/SPEC.md#15-api) for the original endpoint list and `app/api/` for the current full set.

---

## Development notes

- **Python 3.13+** required. Use `uv run` to avoid venv boilerplate.
- **Pydantic v2** for validation; SQLAlchemy 2 for ORM.
- **Alembic** for migrations. Add new columns with `op.add_column()`. Always create a revision; never run raw SQL in production.
- **Redis** caching is enabled for extraction results (keyed by content + prompt version) and retrieval queries (10 min TTL). Clear with `redis-cli FLUSHALL` if stale.
- **Job queue** is Postgres-backed (`processing_jobs` table) with `SELECT ... FOR UPDATE SKIP LOCKED`. Retries use exponential backoff; max 3 attempts before `poison` status.
- **Extraction cache key** in `context_objects.extraction_key` is SHA256 of `(content_hash, prompt_version, model_id, schema_version)`.

---

## References

- **[docs/SPEC.md](docs/SPEC.md)** — Product thesis, data model (§16), all rules (§5–§10), mock scenarios (§18), and evaluation criteria (§19).
- **[docs/ARCHITECTURE-v2.md](docs/ARCHITECTURE-v2.md)** — Proposed org-agnostic, connector-first redesign; what it supersedes in SPEC.md is listed in its §11.
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — Chronological log of concrete bugs found live and fixed, each with the tradeoff accepted.

---

*MVP bootstrap: 2026-09-22. Last updated: 2026-10-01.*
