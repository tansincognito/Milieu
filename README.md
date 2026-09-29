# Milieu — Context Continuity Engine

A system that extracts structured, versioned, source-linked Context Objects from Slack, email, documents, and call transcripts. It resolves them into a single state per customer and detects when important context is lost, contradicted, or degraded across organizational handoffs (Sales → Product → Engineering, plus onboarding and incident flows).

**Not enterprise search. Not a RAG chatbot.** See [SPEC.md § 1 & § 39](docs/SPEC.md#1-product) for the full framing.

---

## Status

**MVP in progress** (`feature/mvp-1.0`):
- **Day 1** ✓ Complete: multi-source ingestion (Slack, email, drive, calls) → Context Objects with live-verified extraction and Slack endpoints.
- **Day 2** ✓ Complete: entity resolution, dedup, lifecycle/supersession/conflict rules, lineage, query + review APIs.
- **Day 3** ⏸ Not started: Context Contracts, handoff validator, degradation detection, evals.
- **Dashboard** ⏸ Not started.

---

## Architecture at a glance

```
ingest → normalize → queue → extract → resolve entity → dedupe → 
lifecycle/supersession/conflict → persist → lineage → maybe handoff validation
```

**Stack:**
- **API & Worker:** FastAPI + async Python, `uv` for dependency management.
- **Database:** PostgreSQL 17 (pgvector for embeddings, pg_trgm for entity resolution).
- **Cache & Queue:** Redis (retrieval cache, job queue backing).
- **LLM:** OpenRouter free tier (OpenAI-compatible API, swappable via `LLMClient` interface).
- **Embeddings:** fastembed local model (BAAI/bge-small-en-v1.5, 384 dims).
- **Sources:** Real Slack endpoints + mock connectors for Drive, email, and call transcripts.
- **Orchestration:** Docker Compose for local dev.

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
| `/api/alembic/versions` | Database migrations. Current: `0001_initial_schema.py`, `0002_lifecycle_dedup_columns.py`. |
| `/docs` | `SPEC.md` (product + technical spec, source of truth for data model and rules). |
| `/mock-data` | Test fixtures: `drive/`, `email/`, `calls/`, `directory.json`, `slack/seed.json`. |
| `/contracts` | Context Contract YAML seeds (Day 3; directory exists, contracts not written yet). |
| `/scripts` | One-off developer scripts, e.g. `slot_probe.py` (how the extraction model and prompt were chosen). |
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

# LLM (required for extraction)
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=<your free API key from https://openrouter.ai>
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b:free

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
| GET | `/entities` | List entities |
| GET | `/entities/{id}/context` | Entity's grouped context, gaps, conflicts |
| POST | `/handoffs/validate` | Run a handoff validation |
| GET | `/handoffs/{id}` | Validation result + gaps |
| GET | `/health` | Health check (DB, Redis, queue depth) |

See [SPEC.md § 15](docs/SPEC.md#15-api) for the full endpoint list.

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

- **SPEC.md** — Product thesis, data model (§16), all rules (§5–§10), mock scenarios (§18), and evaluation criteria (§19).
- **design.md** — User journeys and state flows (if it exists).
- **architecture.md** — System topology and API contracts (if it exists).

---

*MVP bootstrap: 2026-09-22. Last updated: 2026-09-25.*
