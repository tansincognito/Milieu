# Decisions log

Concrete bugs found live, the fix made, and the tradeoff accepted — in the order they
happened. Each entry is grounded in a real observation (a log line, a failing test, a query
against real data), not a hypothetical. See `docs/SPEC.md` for the product spec and
`docs/ARCHITECTURE-v2.md` for the proposed org-agnostic redesign; this doc is the record of
what actually changed underneath both while getting the MVP to hold up under real use.

---

## 1. Job-queue reclaim + Groq as a second LLM provider

**Problem.** OpenRouter's free-tier models share OpenRouter's own pooled capacity across
every free user hitting that route — the actual cause of this project's sustained 429s, not
any one specific model. Switching models within OpenRouter just moves to a different shared
queue. Separately, a worker killed mid-job (`pkill`, a crash) left its claimed job stuck in
`running` forever, because `claim()` only ever looks at `queued` rows — nothing reclaimed it.

**Decision.** Added `GroqLLMClient` as a second provider behind the existing `LLMClient`
Protocol, selected by `LLM_PROVIDER=groq` (`app/core/factories.py`). Groq's free tier runs on
its own hardware with per-account limits, not a multi-tenant marketplace pool, so it doesn't
inherit OpenRouter's contention. Added `JobQueue.reclaim_stale(timeout_seconds)`, called once
at worker startup, resetting any job stuck in `running` longer than the client's own request
timeout back to `queued`.

**Tradeoff.** A second provider abstraction to maintain (`app/llm/groq.py` mirrors
`openrouter.py`'s retry/validation shape closely, but diverges where Groq's actual behavior
diverges — see §2). `reclaim_stale`'s 600s timeout is a judgment call: comfortably longer than
the LLM client's 180s request timeout so it never reclaims a job that's still genuinely in
flight, but long enough that a real crash sits unrecovered for up to 10 minutes.

**Files.** `app/llm/groq.py`, `app/core/factories.py`, `app/queue/base.py`,
`app/queue/postgres.py`, `app/worker.py`.

---

## 2. Token-budget pacing moved into the LLM client, not the worker loop

**Problem.** Groq's real constraint, found live by reading `x-ratelimit-*` response headers,
is an **8,000 tokens/minute** budget — not the `x-ratelimit-limit-requests: 1000` header,
which was barely touched. A first attempt paced the worker loop itself
(`MIN_JOB_INTERVAL_SECONDS = 2.0`, a floor between job claims). It still 429'd on every cycle.
The actual burst was inside a single job: `GroqLLMClient._call`'s own validation-retry (a
second HTTP request fired immediately when the first response fails schema validation) has no
gap between its two requests — a pattern worker-loop-level pacing, which only ever measures
the gap *between job claims*, could never see.

Two more false starts on the way to the real constraint:
- `openai/gpt-oss-120b` is a **reasoning model** — it emits a hidden `reasoning` field that
  consumes completion tokens before any `content`. Measured: 487 of 941 total tokens on one
  small extraction call. A `max_tokens` cap can truncate before content is produced at all.
- A static token-cost estimate for the pacing check (first 1500, then 4000) kept guessing
  wrong, because real job content (full source documents) varies far more in size than a
  fixed test prompt.

**Decision.** Pacing lives inside `GroqLLMClient._post`, checked before *every* outbound
request regardless of caller: it reads the live `x-ratelimit-remaining-tokens` /
`x-ratelimit-reset-tokens` headers from the previous response and sleeps out the window when
the remaining balance drops below a conservative margin (half the known 8000 TPM limit — a
size-agnostic buffer, not a per-call estimate). Also switched the default Groq model to
`qwen/qwen3.8-27b`: not a reasoning model, measured ~2.2x more token-efficient than
`gpt-oss-120b` on the identical extraction call (422 vs 941 total tokens), same extraction
quality (verified: correct 3-object extraction, evidence spans matching source verbatim).

**Tradeoff.** The pacing logic is specific to `GroqLLMClient`, not a generic worker-level
mechanism reusable across providers — OpenRouter's client carries no equivalent, since its
failure mode (pooled capacity) isn't a per-account budget a client can self-regulate against.
The worker-loop `MIN_JOB_INTERVAL_SECONDS` floor was kept as a cheap first-pass safety net even
though it wasn't sufficient alone.

**Verified live.** Full queue drain (15 jobs, then confirmed again) with zero 429s after the
fix; before it, the same drain re-poisoned jobs twice over.

**Files.** `app/llm/groq.py` (`_wait_for_budget`, `_parse_reset_seconds`), `app/worker.py`.

---

## 3. Worker crash on a transient DB error

**Problem.** Found live while draining the queue under Groq: Postgres briefly entered
recovery mode mid-run (self-resolved within seconds), but `run_once`'s `db = SessionLocal()`
call sat outside the function's try/except. The transient error killed the entire worker
process — with a job already claimed and no `queue.fail` call, leaving it `running` until
`reclaim_stale`'s 600s timeout.

**Decision.** Wrapped `SessionLocal()` in its own try/except inside `run_once`; on failure,
calls `queue.fail(job.id, ...)` (which opens its own independent session) and returns, keeping
the worker loop alive instead of crashing the process.

**Tradeoff.** None significant — this closes a real gap with no real downside; the only cost
is a few more lines of exception handling around a call that's expected to essentially never
fail outside of this exact scenario.

**Files.** `app/worker.py`.

---

## 4. First-class incident table replacing a subject_key-pattern heuristic

**Problem.** Incident correlation was previously done by matching a `subject_key` pattern plus
a time window — a heuristic that couldn't represent one incident touching multiple entities at
once (e.g. an outage affecting both Acme and Globex), and had no way to answer "previous
similar incidents" (hardcoded to `[]`).

**Decision.** Migration `0009` adds `incidents`, `incident_entities` (many-to-many), and
`incident_context_objects` (with a `linked` bool). `POST /incidents` declares one, `POST
/incidents/{id}/link` attaches entities/context objects, `POST /incidents/{id}/resolve` closes
it. Candidate-discovery for "previous similar" is scoped to the incident's declared entities
plus an "Unknown" allowance (fixed after a real cross-test contamination bug: an earlier
version let one test's incident candidates leak into another's because the query wasn't scoped
tightly enough).

**Tradeoff.** Three new tables and a migration, against a pattern-match heuristic with zero
schema footprint. Accepted because the heuristic structurally couldn't represent the
multi-entity case at all — this isn't a precision/recall tradeoff, it's a capability the old
approach never had.

**Files.** `alembic/versions/0009_incidents.py`, `app/api/incidents.py`,
`scripts/declare_seed_incidents.py`.

---

## 5. Mock connectors unified onto `simulation_seed_sources`

**Problem.** `app/connectors/mock_*.py` always read `/mock-data` directly off disk, while
`/connections/{kind}/sync` and `/simulation/search` read a separate copy of the same content,
imported into Postgres (`simulation_seed_sources`) by a standalone script. Two representations
of the same data that could silently drift apart — and the import script's own docstring
flagged this as a deliberately deferred gap when it was first written.

**Decision.** Extracted the import script's per-kind upsert logic into
`app.pipeline.seed_simulation_sources` (importable, not just a CLI). `load_mock_data` and
`evals/seed.py` now call it before constructing connectors; each `MockXConnector` takes
`(db, tenant_id)` and queries the table instead of walking a directory. The drive and call
connectors' markdown-splitting and header-parsing logic moved into `fetch()`, since it now runs
against a row's `payload` rather than a freshly-read file.

**Fixed in the process.** `import_slack` never stamped `workspace_id` onto each channel's
stored payload (only present at the seed file's top level) — a connector reading from the
table alone couldn't have resolved it. Now injected at import time.

**Tradeoff.** Connectors now require a DB session instead of being pure-filesystem, and every
`load_mock_data` run pays a small upsert step (idempotent, cheap in practice — a no-op update
for unchanged content). The standalone script (`scripts/import_mock_data_to_postgres.py`)
became a thin wrapper, kept for refreshing seed rows without a full ingest.

**Verified live.** Re-ran `load_mock_data` against the existing default tenant: all 83 sources
matched by content hash (byte-identical text through the new path vs. the old direct disk
reads), 0 consent rejections, 0 errors.

**Files.** `app/pipeline/seed_simulation_sources.py`, `app/connectors/mock_*.py`,
`app/pipeline/load_mock_data.py`, `evals/seed.py`.

---

## 6. `/connections/{kind}/sync` does a real ingest

**Problem.** `sync_connection` reported `items_ingested = seed_count` without ever calling
`ingest_source` — clicking "sync" updated a status row (`connections.status`, a `sync_runs`
row) but produced no new `Sources` rows or extraction jobs. The simulated connection surface
modeled a real API's auth/cursor/failure shape but didn't do the one thing a sync is for.

**Decision.** Refreshes the kind's `simulation_seed_sources` rows from disk, then runs the
real connector through the same normalize → hash → enqueue path `load_mock_data` uses,
reporting real `items_seen`/`items_ingested`/`items_failed`. Added the `partial` status
(already defined in `SyncStatus`, previously unused) for a sync where some items ingested and
some failed. A simulated failure (`?simulate=rate_limited|auth_expired|failed`) still never
touches real data — only the non-simulated path does real work, preserving "nothing here
randomly flakes": a demo or test that wants to reproduce a specific failure needs to do so on
command, not have it entangled with whatever real ingestion happens to do that run.

**Tradeoff.** Two branches in one endpoint (simulated-failure vs. real-ingest) instead of one
uniform code path. `directory` kind has no connector of its own (identity data is seeded
separately via `seed_directory`) and keeps its prior honest "no simulation seed data
available" failure rather than gaining a connector it doesn't have.

**Verified live.** Drive/call/slack/email each ingest their real counts and are idempotent on
a second sync (content-hash match, no duplicate `Sources` or jobs); `directory` and
`?simulate=` behave exactly as before.

**Files.** `app/api/simulation.py`.

---

## 7. False contradictions from literal slot comparison

**Problem.** The dashboard's "Contradictions" widget accumulated near-identical restatements
of the same fact next to genuine contradictions. Traced to three distinct bugs in
`slots_compatible()`, each routing a true duplicate into R4 ("conflicting") instead of dedup:

- `due_date` at matching `"quarter"`/`"month"` precision compared by exact literal day —
  two independent "deferred to Q1" extractions normalized to `2027-01-01` and `2027-03-31`
  respectively; same quarter, same stated precision, flagged incompatible anyway.
- `quantity_unit` compared by exact string — `"users"` vs `"provisioned users"` vs `"users per
  org"`, same unit, different wording.
- `rationale` (free-text prose explaining a stance, not a categorical slot) compared by exact
  string at all.

**Decision.** `due_date` is now bucketed by its own stated precision (still an exact match at
day precision). `quantity_unit` uses substring containment, lowercased (still treats
genuinely different units — `"seats"` vs `"users"` — as incompatible, since neither contains
the other). `rationale` is excluded entirely, matching how `extra.corrects`/`corrects_hint`
were already excluded for the same reason (they describe the statement, not a slot value).

**A real test caught overreach.** `root_cause` and `remediation` initially went into the same
free-text exclusion set as `rationale` — same string-typed shape, same "incident slot"
grouping in the schema. A real test
(`test_golden3_incident_stale_reference_and_engineering_to_sales_losses`) failed: a
corrected `root_cause` must still route through R3 supersession, not be waved through as
compatible. Unlike `rationale` (explains *why* a decision was made — explanatory, never the
fact itself), `root_cause`/`remediation` state *what actually happened*/*is being done* — a
genuine factual slot. Narrowed the exclusion to `rationale` alone.

**Tradeoff.** Each new free-text-shaped field discovered needs its own judgment call, not a
blanket "ignore all string slots" rule — more precise, but doesn't generalize automatically to
the next field that turns out to have the same problem.

**Reconciliation.** `scripts/reconcile_false_contradictions.py` re-evaluates existing
`contradicts` relations under the fixed logic: a pair that's now compatible either gets run
through the real `apply_dedup` (if cosine similarity says it's actually a duplicate) or
reverted to independently `active` (if the fix only cleared a false slot conflict, not an
actual duplicate) — in both cases via the normal versioned `_transition`, nothing deleted, and
already-human-resolved relations left untouched. Verified live: dashboard contradictions
dropped from 19 to 9, confirmed all 9 remaining are genuinely distinct facts.

**Files.** `app/pipeline/dedup.py`, `scripts/reconcile_false_contradictions.py`.

---

## 8. Cross-tenant leakage in `/entities` and `/dashboard`

**Problem.** What looked like "duplicate names and Unknown" in the entities list was `GET
/entities` querying `Entities` with **zero tenant_id filter** — every tenant (the real default,
the fixed eval tenant from `evals/seed.py`, leftover random test tenants) got interleaved in
one alphabetized list, so each tenant's own `"Acme Corp"`/`"Globex Corporation"`/`"Unknown"`
appeared to duplicate. `GET /entities/{id}/context` had the same gap on its PK lookup. `GET
/dashboard` had it across every query it runs (contradictions, decisions_pending, deadlines,
gaps, incidents) — only a separate function in the same file (`resolve_person`) filtered by
tenant.

**Decision.** All of the above now scope to `uuid.UUID(get_settings().tenant_id)`, matching
every other tenant-aware endpoint in this codebase. `ContextGaps` has no `tenant_id` of its own
(not `TenantMixin`), so its query now joins through its upstream/downstream `ContextObjects` FK
unconditionally (previously only when a team filter was given) to scope it.

**Test fallout.** Two integration tests had seeded their fixtures under a fresh `uuid.uuid4()`
tenant specifically *because* nothing filtered by tenant before. That's incompatible with the
fix — there's no auth/session layer to make a request "belong" to another tenant, so an
endpoint can only ever see the one configured tenant. Rather than repoint the shared `seeded`
fixture (used by many other tests against endpoints that still don't filter, whose teardown
does a blanket `DELETE WHERE tenant_id = :t` that would be destructive if pointed at the real
default tenant), gave just the two affected tests their own inline setup against the real
tenant with precise, explicit-id cleanup.

**Also found and fixed while investigating.** Two genuinely malformed entities in the real
default tenant: `"Acme, Globex"` (a comma-joined `entity_hint` from an incident affecting both
companies at once) and `"EU"` (a region extracted as if it were a named customer). Reassigned
their `context_objects` to a correct entity (Acme Corp / Unknown respectively), removed the two
bad rows, and tightened the extraction prompt's `entity_hint` rule (§11 in `prompts.py`) to
reject both shapes going forward: never a comma-joined list, never a region/market segment,
null when the text doesn't name one specific company.

**Re-checked, not fixed: the "subject_key entity-resolution gap."** The working theory going
in was that dedup only ever compares siblings sharing `(entity_id, type, subject_key)`, so two
extractions of the same real fact that resolve to different entities (one correctly, one
falling back to "Unknown") would never be compared at all. Checked this against live data: the
concrete cross-entity-*looking* pairs found during the §7 investigation turned out, on closer
inspection, to be same-entity, same-subject_key siblings — already caught by the §7 fix. No
live instance of a genuine cross-subject_key missed-dedup case was found, so nothing was
changed here. The architectural limitation (dedup's candidate pool is scoped strictly to
matching siblings) is real and could still bite with different data; worth another look if a
concrete instance turns up.

**Tradeoff.** Scoping to one hardcoded tenant is correct for the single real tenant this app
currently serves, but is a known ceiling — every API caller sees the same tenant until a real
session/auth layer exists (§8 of `ARCHITECTURE-v2.md`, not built).

**Files.** `app/api/entities.py`, `app/api/dashboard.py`, `app/pipeline/prompts.py`,
`tests/integration/test_context_api.py`, `tests/integration/test_dashboard_api.py`.

---

## Open, not yet acted on

- Real OAuth connectors (Gmail/Slack/Drive) — `tenants.mode` accepts `"production"` as a
  value but `PATCH /tenant/mode` refuses to switch into it, since no real connector exists.
- Server-side session/auth — every request is implicitly the one configured tenant.
- Gmail compose-time contradiction intercept.
- Departments/stages as tenant-configurable data (`TEAM_TO_STAGE`/`TEAM_TO_ACTOR_ROLE` are
  still hardcoded maps).
- New-hire brief feature.
- Semantic retrieval as a secondary "related context" feature inside the explorer — decided
  against a primary search bar, not yet implemented.
