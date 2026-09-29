# Milieu dashboard (`web/`)

React + Vite + TypeScript dashboard for the Context Continuity Engine (SPEC.md §17, items
1, 2, 3, 5). Talks only to the existing FastAPI backend in `../api` — no business logic is
duplicated client-side.

## What's implemented

- **Entity picker** (`/`) — entities with per-kind source counts, open conflicts, open gaps.
- **Context Explorer** (`/entities/:entityId`) — tabs Current (grouped into requirements,
  decisions, constraints, dependencies, commitments, problems/questions), Conflicts, Gaps,
  History. Deadlines render inline from `due_date`/`due_date_precision`.
- **Detail panel** (click any item) — evidence quote highlighted inside the full source
  text, provenance fields, authority and confidence as two separate values (never
  combined into one score), version history, and the lineage chain back to root evidence.
- **Review queue** (`/review`) — candidates, conflicts, and gaps in one place. Actions:
  Confirm, Edit, Ignore, Resolve Conflict, Mark Stale. Edit opens the detail panel and
  posts `POST /context/{id}/review` with `action=edit`, which creates a new version and
  never touches `evidence_quote`/`source_id` (enforced server-side).

Not implemented (explicitly out of scope for this build): §17.4, the handoff report /
chain view — it needs the Day 3 Context Contracts and handoff validator.

## Run it

```bash
cd web
npm install
cp .env.example .env   # optional — defaults to http://localhost:8000 if you skip this
npm run dev
```

Opens on `http://localhost:5173`. The API must be running separately (it is not
containerized — see the repo root README):

```bash
make up && make migrate                                        # Postgres + Redis
cd api && uv run uvicorn app.main:app --reload --port 8000      # API
cd api && uv run python -m app.worker                           # worker (extraction)
```

Use the "Load mock data" button in the top bar (or `POST /sources/mock/load`) to ingest
`/mock-data`, then wait for the job counter in the top bar to drain — extraction runs
through the real LLM and each source takes tens of seconds on the OpenRouter free tier.

## API additions made for this build

The dashboard is read/write against the existing API (`api/app/api/`, `api/app/schemas/`).
A few gaps had no endpoint or field to read from; all additions are minimal and covered by
`api/tests/integration/test_dashboard_api.py`:

- `GET /sources/{id}` — the detail panel needs the full source **text** to highlight the
  evidence quote in place. `ContextObjectOut.source` only ever exposed
  `{kind, stage, source_ts, provenance}` (the §14 redaction fields), never the body. Same
  ACL redaction rule as the rest of the API: `text`/`provenance` are `null` unless the
  caller's principals intersect the source's ACL.
- `GET /conflicts?entity=` — pairs an open `contradicts` relation with both of its
  objects. `POST /conflicts/{relation_id}/resolve` needs a `relation_id`, but
  `/context/search` and `/entities/{id}/context` only return a flat list of `conflicting`
  objects with no relation id to act on. Used by the Conflicts tab and the review queue.
- `GET /gaps?entity=&status=` and `POST /gaps/{id}/review` — these were already named in
  SPEC.md §15 but not yet built (the Day 3 handoff validator that populates
  `context_gaps` is out of scope here). Building the read/write surface on the existing
  `context_gaps` table now means the Gaps tab and review-queue gap actions are real code
  paths today; they will start showing rows the moment the validator ships. `POST
  /gaps/{id}/review` reuses the existing `confirm`/`ignore` review actions (no new
  `reviews.action` enum value needed).
- `EntityOut.open_gaps: int` — §17.1 asks for "counts of open gaps and conflicts"; only
  `open_conflicts` existed. Computed from `context_gaps` the same way `open_conflicts` is
  computed from `context_objects`; it will read `0` for every entity until gaps exist.
- CORS middleware in `api/app/main.py`, allowlisting the Vite dev server origin(s) via a
  new `DASHBOARD_ORIGINS` setting (comma-separated, defaults to
  `http://localhost:5173,http://127.0.0.1:5173`). The API has no cookie/session auth in
  the MVP, so this carries no additional risk.

None of these touch `api/app/pipeline/`, `api/evals/`, or the `Makefile`.

## Structure

```
src/
  api.ts          fetch wrapper over the backend, one function per endpoint
  types.ts        hand-written TS types mirroring api/app/schemas/*.py
  components/
    Badges.tsx        authority/confidence/status/due-date badges (shared everywhere)
    ObjectRow.tsx      one-line summary row for a context object, click -> detail panel
    DetailPanel.tsx    §17.3 detail panel (evidence highlight, provenance, versions, lineage)
    ConflictList.tsx   shared conflict-pair list + resolve actions (Explorer + Review queue)
    GapList.tsx        shared gap list + confirm/ignore actions (Explorer + Review queue)
  pages/
    EntityPicker.tsx      §17.1
    ContextExplorer.tsx   §17.2 (tabs)
    ContextStandalone.tsx directly-linkable /context/:id detail view
    ReviewQueue.tsx        §17.5
```
