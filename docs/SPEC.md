# Context Continuity Engine — MVP v0.1 Spec (revised)

Status: draft r2 · 2026-09-22
Supersedes: PRD + Technical Specification v0.1 (500-line numbered version)

---

## 0. What changed from v0.1 and why

| # | Change | Reason |
|---|--------|--------|
| C1 | Handoffs are defined explicitly as `(entity, from_stage, to_stage)`, and every source is assigned a **stage** at ingestion. | v0.1 never said how the validator knows which context is "upstream" and which is "downstream". |
| C2 | Context Contracts are **typed slot checks**, not lists of free-text field names. | "Golden test passes reliably" is not achievable if an LLM must decide on its own that SAML matters. |
| C3 | Loss is **attributed to the handoff where it occurred** (chain view). | Without it, a gap that originates in Sales→Product also shows up in Product→Engineering, and the report double counts. |
| C4 | `Confirmed` is removed as a lifecycle status and becomes an orthogonal review field. | v0.1 conflated "a human accepted it" with "it is currently valid". A confirmed item can later be superseded. |
| C5 | Deadline is a slot (`due_date`), not a type. | v0.1 left this open (§9.135). A slot makes deadline preservation checkable per requirement. |
| C6 | Authority is a 0–4 enum with fixed rules. | "High/lower" is not implementable or testable. |
| C7 | Explicit supersession vs. conflict rule. | v0.1 required the distinction (§16, §20) but gave no rule. |
| C8 | AWS, Terraform, Floci, and SQS are moved **out** of the 3-day MVP. A Postgres job queue sits behind a `JobQueue` interface. | Day 3 had 11 tasks. The core claim is still unproven. The interface keeps the SQS swap cheap. |
| C9 | Gmail is **mocked** for the MVP (JSON threads in `/mock-data/email`). Only Slack is a real integration. | v0.1 contradicted itself (§5.72 vs. §5.80). Gmail restricted-scope OAuth review is slow. |
| C10 | A retrieval **baseline** is required in the eval harness. | Success criterion §39.498 ("more reliably than generic retrieval") is unmeasurable without one. |
| C11 | Eval minimums are cut: golden test + ≥10 degradation cases + 3–5 per other category. | v0.1 asked for ≥100 cases in 3 days. |
| C12 | Slack interaction model is one app (`@context`) with a message shortcut plus mention commands. | `@mark` and `@context` as literal mentions would need two Slack apps. |
| C13 | `context_gaps` table added, plus a Slack interactivity endpoint. | Gaps need Review/Ignore state. Slack buttons need an endpoint. Both were missing. |
| C14 | "Reliably" is defined: 5/5 consecutive runs, temperature 0, extraction cache disabled. | v0.1 used "reliably" without a definition. |
| C16 | Two more scenarios beside IAM: customer onboarding with a sales error, and a cloud-provider outage at peak hours reported by engineering. This adds a `customer_success` stage, two contracts, and incident/onboarding slots. No new types. | The IAM flow alone only tests forward Sales→Product→Engineering loss. The new scenarios test a same-stage human error and a reverse Engineering→Sales/CS handoff. |
| C17 | The LLM provider for the MVP is OpenRouter's free tier, behind the same `LLMClient` interface. | Zero cost for the MVP. The interface keeps a paid or other vendor a config swap. |
| C15 | Email stage and every source's `actor_role` are resolved through a people directory (person → team → role). | Internal email must map to the sender's team. The same lookup gives Slack authors a correct role. |

Unchanged: the product thesis, the non-goals, the Sales → Product → Engineering flow, Acme as the test entity, and the React / FastAPI / Postgres + pgvector / Redis / Docker Compose stack.

---

## 1. Product

The Context Continuity Engine keeps organizational context intact across handoffs. It extracts structured, versioned, source-linked **Context Objects** from Slack, email, documents, and call transcripts. It resolves them into one state per entity. It then checks each handoff between teams against a **Context Contract** and reports what was lost, generalized, or contradicted, with evidence.

Enterprise search answers "where is the information?". This product answers "did the important meaning survive?".

**Core demo:** A customer says "We need SAML through Okta by Dec 15". Product writes "Support SSO". Engineering writes "Implement SSO". The system reports that SAML and Okta were lost at the Sales→Product handoff, shows that the deadline was generalized, and links every finding to the customer's original words.

### Questions the system must answer

| Question | Mechanism |
|---|---|
| What is currently true? | Active objects, filtered by authority (§5, §7) |
| Why do we believe this? | Evidence quote + provenance (§6) |
| What changed? | `context_versions` + `supersedes` relations (§7) |
| What was lost? | Handoff validator gaps (§10) |
| Where does this come from? | Lineage traversal to root evidence (§8) |
| Is the documentation consistent? | Conflict detection (§9) |
| Did this handoff satisfy its contract? | Handoff validation (§10) |

### Users

Sales captures customer requirements and uses Slack and source systems. Product turns them into decisions and uses Slack and the dashboard. Engineering consumes requirements and uses Slack. Managers investigate gaps and new hires explore context, both through the dashboard. Admins configure sources, stage mappings, and contracts.

---

## 2. MVP scope

### In scope (3 days)

- **Real Slack integration:** Events API, message shortcut, mention commands, interactive buttons.
- **Mock sources:** Drive (markdown), email (JSON threads), and call transcripts (text with speaker labels). All loaded from `/mock-data` through the same ingestion interface a real connector would use.
- **Unified pipeline:** normalize → extract → resolve entity → dedupe → lifecycle → persist → lineage.
- Entity resolution, deduplication, versioning, supersession, and conflict detection.
- Sales→Product and Product→Engineering contracts, the handoff validator, and chain attribution.
- Dashboard: Context Explorer, lineage/evidence panel, handoff report, and review queue.
- Human review actions: Confirm, Edit, Ignore, Resolve Conflict, Mark Stale.
- Eval harness: golden test, degradation set, retrieval baseline, and small sets for the other categories.
- Local run with Docker Compose.

### Deferred (post-MVP, architecture must allow it)

- Real Gmail and Drive API connectors. The `SourceConnector` interface is fixed now.
- SQS behind the `JobQueue` interface, Floci for local AWS testing, Terraform, and the ECS/Fargate deploy.
- Full ≥10-case eval sets for every category.
- Context Continuity Rate and Context Debt metrics. The data model supports them now, but there is no UI.

### Stretch (Day 3, only if the golden tests are green)

- Proactive Slack gap alerts (§12.3).

### Non-goals (unchanged)

Generic enterprise search, a RAG chatbot, autonomous agents, a knowledge-graph DB, MCP, audio transcription, multi-tenant billing, a large ontology, autonomous doc editing, enterprise RBAC, premature infra optimization, and integrations that don't serve the continuity experiment.

---

## 3. Stages and handoffs (C1)

A **stage** is the organizational function that owns a piece of context. For the MVP the stages are `sales`, `product`, `engineering`, and `customer_success` (onboarding and account management).

Every source record gets a stage at ingestion, from config:

| Source | Stage rule |
|---|---|
| Drive | Folder → stage (`/mock-data/drive/sales` → `sales`, and so on) |
| Slack | Channel → stage mapping in config (e.g. `#acme-deal` → sales, `#eng-auth` → engineering). Unmapped channels → `null` stage (ingested, but excluded from handoffs). |
| Email | Sender lookup in the **people directory** (§3.1). An internal sender takes their team's stage. An external sender from a known customer domain → `sales`. An unknown sender → `null` stage + review. |
| Call | The team of the internal participants (via the directory). Default `sales`. An onboarding kickoff run by CS → `customer_success`. |

### 3.1 People directory

`people(email, slack_user_id, name, team, role, is_external, company_entity_id)` maps each person to a team and role. The team maps directly to a stage (`sales`, `product`, `engineering`, or `other` → `null`).

- **Email:** the sender's `From` address is looked up by exact email. If there is no match, the domain is looked up: an internal domain with an unknown person → `null` stage + review; a domain that matches a customer entity's domain → `is_external`, `actor_role = customer`, stage `sales`.
- **Slack:** the stage still comes from the channel map, but `actor_role` comes from the directory. So an engineer posting in `#acme-deal` is `actor_role = engineering` in a sales-stage source.
- **All sources:** `actor_role` resolves through the directory first, then falls back to source-specific hints (call speaker labels, email domain).
- The MVP seeds the directory from `/mock-data/directory.json`. After the MVP it can sync from Slack `users.list` or Google Workspace Directory.

A **handoff** is `(entity_id, from_stage, to_stage, contract_id)`. Validation compares all **active** Context Objects for the entity in `from_stage` against those in `to_stage`, as of a point in time (default: now).

Customer statements (from calls and email) belong to the `sales` stage but keep `actor_role = customer`. That keeps the customer's original words in the upstream set for Sales→Product.

---

## 4. Context Object

### 4.1 Types (8)

| Type | Meaning |
|---|---|
| `requirement` | Something must be supported or satisfied |
| `decision` | An organizational choice |
| `constraint` | Limits acceptable solutions |
| `commitment` | A promise by an actor or organization |
| `problem` | An identified issue |
| `open_question` | Unresolved information |
| `resolution` | How an issue was resolved |
| `dependency` | Something a component or entity relies on |

Deadline is **not** a type (C5). It is the `due_date` slot on `requirement`, `commitment`, and `decision`. Adding a type requires a written product reason in this doc.

### 4.2 Subject and slots (C2)

Each object has a **subject key** that identifies what it is about, independent of wording:

```
subject_key = "{entity_slug}:{capability}"      e.g. "acme:sso", "acme:scim"
```

`capability` comes from a small controlled vocabulary (seeded, editable) with an LLM fallback to a new slug, which is then routed to review. Seed vocabulary for the MVP: `sso`, `scim`, `audit_logs`, `data_residency`, `rbac`, `api_access`, `uptime_sla`, `pricing`, `integration`, `onboarding`, `seats`, `incident`. The vocabulary also defines **generalization**: the extractor knows that `SAML`, `OIDC`, and `Okta` refine `sso`, so it never treats "SSO" as unrelated to "SAML through Okta".

Slots are typed attributes stored in `attributes` (jsonb, validated by Pydantic):

| Slot | Kind | Example |
|---|---|---|
| `protocol` | specificity | `SAML` |
| `idp` | dependency | `Okta` |
| `due_date` | deadline | `2026-12-15` (plus `due_date_precision`: `day` \| `month` \| `quarter`) |
| `priority` | priority | `P0` \| `P1` \| `P2` |
| `stance` | state | `required` \| `deferred` \| `in_progress` \| `done` \| `dropped` \| `rejected` |
| `acceptance_criteria` | acceptance | list of strings |
| `rationale` | rationale | string |
| `region` | constraint | `eu-west-1` / `EU` (hierarchy: `EU` ⊃ `eu-west-1`) |
| `quantity` | specificity | `500` (with unit, e.g. seats) |
| `integrations` | dependency | `[Salesforce]` |
| `plan` | specificity | `Enterprise` |
| `impact` | incident | `{customers: [Acme, Globex], error_rate: 0.12, data_loss: "~300 writes, replayed"}` |
| `time_window` | incident | `{start, end, timezone}`, from which duration is derived |
| `root_cause` | incident | `AWS us-east-1 ELB degradation` |
| `sla_impact` | incident | `{breached: true, contract_uptime: 99.95, credit_owed: true}` |
| `remediation` | incident | `multi-AZ failover by 2026-12-15` |
| `extra` | specificity | free key-values the extractor found that don't fit other slots |

Instance subjects: for capabilities that have instances, the subject key carries the instance id, e.g. `acme:incident:INC-2311`. The incident id is taken from the source text, or assigned by the extractor from `(date, root system)` and routed to review.

The slot **kind** drives degradation labels. A lost `protocol` is "specificity loss". A lost `idp` is "dependency loss". A `due_date` whose precision drops from `day` to `month` is "deadline generalized".

### 4.3 Fields

```
id                 uuid, stable
tenant_id          uuid (single tenant in the MVP; column present everywhere)
entity_id          fk entities
type               enum §4.1
subject_key        text
content            normalized one-sentence statement (LLM-written; never replaces evidence)
attributes         jsonb slots §4.2
actor_label        text  ("Dana Kim (Acme)", "SALES", ...)
actor_role         enum customer | sales | product | engineering | other | system
stage              enum sales | product | engineering | null
authority          smallint 0–4 (§5)
confidence         real 0–1 (extraction certainty)
status             enum candidate | active | conflicting | superseded | stale | ignored
confirmed_by       fk users null      ─┐ review state, orthogonal
confirmed_at       timestamptz null   ─┘ to status (C4)
valid_from         timestamptz (default = source_ts)
valid_to           timestamptz null
source_id          fk sources, NOT NULL
evidence_quote     text, exact substring of source text
evidence_span      int4range, char offsets into source text
embedding          vector(384)
extraction_key     text (cache key §13)
version            int
created_at, updated_at
```

Invariant: `source.text[evidence_span] == evidence_quote`. It is validated on write, and the object is rejected if it fails. This is how "LLM summaries never replace evidence" is enforced.

**Every Context Object has a source. There are no exceptions.**
- `source_id` and `evidence_quote` are `NOT NULL` at the DB level.
- Text typed in `@context mark [text]` or `POST /context/mark` is first stored as its own source record (Slack message or API submission, with author and timestamp). Objects extracted from it point to that record.
- A human **edit** creates a new version of the object and a `reviews` row. The object keeps its original `source_id` and evidence, and the version records who changed what.
- A human **conflict resolution** or **confirm** never creates a sourceless object. It changes status on existing objects and is logged in `reviews`.
- LLM-inferred objects (authority 0) still carry the exact source span they were inferred from.
- If an object is supported by duplicates from several sources, every source stays linked through `supported_by`.

---

## 5. Authority and confidence

These are never combined into one score.

**Confidence** (0–1) is how sure the extractor is that the text says this.

**Authority** (0–4) is how legitimate the claim is organizationally:

| Level | Assigned when |
|---|---|
| 4 | The customer states the requirement directly (call speaker `CUSTOMER`, or an email from a customer domain). A `decision` by anyone on the product team (directory team = `product`). Every team member is accountable, so there is no approver list. A human-confirmed object. |
| 3 | A statement by the owning function inside its own stage (an engineering decision in an engineering source). |
| 2 | Sales restating the customer (a sales-stage summary without a direct customer quote). |
| 1 | Sales suggestion or speculation ("they'd probably want...", "we could offer..."). A sales `commitment` never raises the authority of a customer requirement. |
| 0 | LLM inference not stated in the text. |

Review routing:

- `confidence < 0.6` → review.
- `authority ≤ 1` **and** the object would affect a contract field of importance `critical` → review.
- New capability slug outside the vocabulary → review. Any reviewer can approve it, and it is added to `capability_vocab`.
- Everything else → `active`, unless a conflict rule applies (§7.3).

---

## 6. Sources and provenance

### 6.1 Connector interface

```python
class SourceConnector(Protocol):
    kind: Literal["slack", "email", "drive", "call"]
    def fetch(self, since: datetime | None) -> Iterable[RawSource]: ...
    def normalize(self, raw: RawSource) -> NormalizedSource: ...
```

The MVP ships `SlackConnector` (real, event-driven) plus `MockEmailConnector`, `MockDriveConnector`, and `MockCallConnector` (file-based). The extraction layer only sees `NormalizedSource`.

### 6.2 NormalizedSource

```
kind, external_id, version, content_hash (sha256 of text)
text               plain text used for extraction and evidence spans
source_ts          authored/modified time
stage              §3
acl                list of principal ids, or ["*"] (§14)
provenance         kind-specific, Pydantic-validated:
  slack  → workspace_id, channel_id, message_ts, thread_ts, author_id
  email  → thread_id, message_id, from, to[], cc[], subject
  drive  → document_id, path, section_heading, section_index, modified_ts
  call   → call_id, speaker, utterance_index, transcript_path, consent{given, by, at}
```

Drive documents are split on markdown headings. Each section is one source record.

Calls without `consent.given = true` are rejected at ingestion. No audio is stored.

Email attachments are stored as metadata only.

---

## 7. State: entities, dedup, lifecycle, supersession, conflict

### 7.1 Entity resolution

1. Normalize: lowercase, strip punctuation and legal suffixes (`inc`, `corp`, `corporation`, `llc`, `ltd`, `co`).
2. Exact match against `entity_aliases`.
3. Otherwise, `pg_trgm` similarity ≥ 0.85 → auto-link and add the alias.
4. Otherwise, 0.6–0.85 → create a candidate link and send it to review.
5. Otherwise, create a new entity.

Test: "Acme", "Acme Corp", and "ACME Inc." resolve to one entity at step 2 after the first alias is seeded.

Thresholds are starting guesses. Tune them against the entity eval set.

### 7.2 Deduplication

Two objects are duplicate candidates when they share `entity_id`, `type`, and `subject_key`, **and** have compatible slots (no slot with conflicting values).

- Cosine similarity ≥ 0.92 → automatic `duplicate_of` relation.
- 0.80–0.92 → review.

Source records and evidence are never merged. Duplicates **support** each other: the canonical object gains `supported_by` relations and its authority becomes the max across the group.

### 7.3 Lifecycle, supersession, conflict (C7)

Statuses: `candidate` → `active` → (`superseded` | `stale` | `conflicting` | `ignored`). Every transition writes a `context_versions` row. Nothing is deleted.

When a new object N arrives with the same `subject_key` as an active object O, with a different `stance` or conflicting slot values:

Rules are evaluated in order. The first match wins.

| # | Condition | Result |
|---|---|---|
| R1 | N and O are in **different stages** with incompatible stances (see matrix) | Both → `conflicting`. A stage disagreement is a conflict, not a supersession. |
| R2 | `N.authority < O.authority` | Both → `conflicting`, a `contradicts` relation is created, and the pair goes to review. |
| R3 | `N.source_ts > O.source_ts`, `N.authority ≥ O.authority`, **and** (N has the same author as O, **or** N is an explicit correction) | N `supersedes` O. O → `superseded`, `O.valid_to = N.valid_from`. |
| R4 | Anything else, i.e. **different authors** in the same stage disagree and neither is an explicit correction | Both → `conflicting`, sent to review. Two accountable people disagreeing is surfaced, never silently resolved by recency. |
| R5 | Human resolves | The winning object stays `active`, the loser → `superseded`, and a `reviews` row is written. |

**Same author** means the same `people.id` (resolved through the directory, so one person's Slack and email identities match).

**Explicit correction:** the extractor sets `corrects: true` when the text signals it is replacing an earlier statement ("update:", "correction", "actually", "rolled back", "back in scope", "supersedes", a reply in the same thread that contradicts its parent, or a document revision of the same section). It also sets `corrects_hint` (a quote of what is being corrected). A correction with `confidence < 0.7` falls back to R4.

Stance compatibility (cross-stage):

| | required | deferred | in_progress | done | dropped |
|---|---|---|---|---|---|
| **required** | ok | conflict | ok | ok | conflict |
| **deferred** | conflict | ok | **conflict** | conflict | ok |
| **in_progress** | ok | **conflict** | ok | ok | conflict |

SCIM example: email (customer, required, Oct 3), product doc (deferred, Oct 10), eng Slack (in_progress, Oct 14). Product "deferred" vs. the customer's "required" is a cross-stage conflict. Engineering "in_progress" vs. Product "deferred" is a cross-stage conflict. The system flags both conflicts, showing all three sources.

Supersession example: SCIM required → deferred → required, all product-stage and all authority 4. The PM who owns Acme writes all three, and the last one says "SCIM back in scope". Each supersedes the previous one (R3). Current = required. History shows all three. No active conflict remains.

Same-stage disagreement example: PM A writes "SCIM deferred to Q1" and PM B writes "SCIM is in the Dec release" a day later, with no correction language. Both → `conflicting` (R4), and neither wins by recency.

A current-state query returns `status = active`, ordered by authority, then recency. A historical query also includes `superseded`.

---

## 8. Lineage

`context_relations(from_id, to_id, relation, created_by, confidence, created_at, resolved_at)`

Relations: `derived_from`, `supported_by`, `decided_by`, `implemented_by`, `supersedes`, `contradicts`, `duplicate_of`.

`derived_from` links are created when a downstream-stage object shares `subject_key` with an upstream-stage object and its `valid_from` is later. This is the link the handoff validator walks. Traversal uses recursive CTEs, with no graph DB. The dashboard shows the chain from the current object back to root evidence.

---

## 9. Context Contracts (C2)

Contracts are stored as data (seeded from YAML into `context_contracts`). There are two check kinds:

- **propagate:** every qualifying upstream object must have a downstream counterpart (same `subject_key`), and the listed slots must survive.
- **present:** every downstream object of the given type must have the listed slots, with no upstream comparison needed.

```yaml
id: sales_to_product
from_stage: sales
to_stage: product
fields:
  - name: requirements
    check: propagate
    types: [requirement]
    min_upstream_authority: 2
    slots: [protocol, idp, due_date, priority, extra]
    importance: critical
  - name: commitments
    check: propagate
    types: [commitment]
    slots: [due_date]
    importance: high
  - name: constraints
    check: propagate
    types: [constraint]
    importance: high
  - name: problem
    check: propagate
    types: [problem]
    importance: normal
  - name: evidence
    check: present          # downstream requirements must link to upstream evidence
    types: [requirement]
    rule: has_derived_from
    importance: high
```

```yaml
id: product_to_engineering
from_stage: product
to_stage: engineering
fields:
  - {name: requirements, check: propagate, types: [requirement], slots: [protocol, idp, due_date, priority, extra], importance: critical}
  - {name: decisions,    check: propagate, types: [decision], slots: [rationale], importance: high}
  - {name: dependencies, check: propagate, types: [dependency], importance: high}
  - {name: constraints,  check: propagate, types: [constraint], importance: high}
  - {name: acceptance_criteria, check: present, types: [requirement], slots: [acceptance_criteria], importance: high}
```

```yaml
id: sales_to_customer_success        # onboarding handoff
from_stage: sales
to_stage: customer_success
fields:
  - {name: requirements, check: propagate, types: [requirement, constraint], slots: [region, quantity, integrations, plan, due_date], importance: critical}
  - {name: commitments,  check: propagate, types: [commitment], slots: [due_date, plan], importance: critical}
  - {name: contacts,     check: present, types: [dependency], rule: has_customer_contact, importance: normal}
```

```yaml
id: engineering_to_customer_facing   # incident handoff, run for to_stage = sales and customer_success
from_stage: engineering
to_stage: [sales, customer_success]
fields:
  - {name: incident,    check: propagate, types: [problem], slots: [impact, time_window, root_cause, sla_impact], importance: critical}
  - {name: resolution,  check: propagate, types: [resolution], slots: [remediation, due_date], importance: high}
```

For the incident contract, the downstream set includes customer-facing email written by sales/CS after the incident. That is where losses reach the customer.


"Customer" and "priority" from v0.1 are covered by `entity_id` and the `priority` slot.

---

## 10. Handoff validator

### 10.1 Algorithm

For each contract field:

1. Select upstream objects U (`active` **or `conflicting`**, `from_stage`, matching types, authority ≥ min). When a subject group has an unresolved conflict, the highest-authority object is the reference. The gap is tagged `upstream_conflict` so the report shows that the error started inside the upstream stage (e.g. a sales AE contradicting the customer).
2. For each u ∈ U, find the downstream counterpart d: same `subject_key` in `to_stage`. If there are several, take the highest authority, then the most recent.
3. Compare each listed slot and assign one outcome:

| Outcome | Rule | Method |
|---|---|---|
| `preserved` | Equal after normalization | deterministic |
| `equivalent` | Different wording, same meaning (paraphrase) | LLM judge, structured output |
| `generalized` | d's value is broader (SSO vs. SAML; `month` vs. `day` precision) | vocabulary hierarchy + date precision, LLM judge fallback |
| `missing` | The slot is set on u and absent on d | deterministic |
| `contradicted` | Incompatible values | deterministic for stance/date, LLM judge fallback |
| `object_missing` | No d at all | deterministic |
| `stale_reference` | d's value matches a **superseded** version of u (e.g. sales still cites the old root cause) | deterministic against `context_versions`, LLM judge fallback |

4. `equivalent` and `preserved` produce no gap. This is how the validator avoids flagging every paraphrase.
5. Each other outcome becomes a `context_gap` row.

### 10.2 Severity

```
severity = importance_weight(field) × authority_weight(u) × outcome_weight
importance: critical 3, high 2, normal 1
authority:  4→1.0, 3→0.8, 2→0.6, 1→0.3, 0→0.1
outcome:    object_missing 1.0, contradicted 1.0, stale_reference 0.9, missing 0.9, generalized 0.6
bands:      ≥2.0 high · ≥1.0 medium · else low
```

These weights are initial values. Tune them on the degradation set.

### 10.3 Explanation

Every gap stores a template-rendered explanation, not free LLM prose:

> **Specificity loss** — upstream requirement "SAML through Okta by Dec 15" (customer, authority 4, call 2026-10-02 00:14:32) specifies `protocol = SAML`. Downstream "Support SSO" (acme-prd.md § Requirements) generalizes this to `SSO`. Contract `sales_to_product.requirements` (critical) requires this slot to survive.

### 10.4 Chain attribution (C3)

For Product→Engineering, a gap is marked `inherited` if the same slot was already missing or generalized at Sales→Product. The report shows each loss once, at its **origin** handoff, and lists inherited losses separately. The dashboard chain view shows, per root requirement, each slot's state at each stage.

### 10.5 Triggers

- `POST /handoffs/validate`.
- Automatically after ingesting a source whose stage is the `to_stage` of any contract for the resolved entity. The job is debounced per entity for 30 s.

---

## 11. Extraction

- `LLMClient` interface: `extract(schema, system, content) -> PydanticModel`, `judge(schema, prompt) -> PydanticModel`. The MVP implementation is OpenRouter (OpenAI-compatible API, a `:free` model set by `LLM_MODEL`) using JSON-schema structured output, with a JSON-mode fallback. Free-tier HTTP 429s are retried through the job queue backoff. The provider is selected by env var, so no vendor is hard-coded.
- `EmbeddingClient` is a separate interface. The default is a local `fastembed` model (`BAAI/bge-small-en-v1.5`, 384 dims), so dev works without a key. (Verify the current model availability.)
- One extraction call per source record returns `list[ExtractedContext]`, with evidence as exact quote plus span. Pydantic validates the output. Invalid output → one retry with the validation error appended → otherwise the job fails.
- Call transcripts: the prompt receives speaker labels. Rules: `CUSTOMER` statements of need → `requirement`, authority 4. `SALES` promises → `commitment`. `SALES` hedged proposals → `requirement` or `open_question`, authority 1. A commitment never creates or upgrades a customer requirement.
- Temperature 0.

---

## 12. Slack experience (C12)

One Slack app, `@context`.

### 12.1 Capture

- **Message shortcut "Save as context"**, available on any message. The message becomes a source, extraction runs, and results are created as `candidate` objects. The bot replies ephemerally with the extracted items and a dashboard link.
- **`@context mark [text]`** in a thread marks the thread's parent message, plus any added text.

### 12.2 Retrieval

- **`@context <entity> [topic]`** (any mention not starting with `mark`) returns active objects for the entity, ordered by authority. Each result shows the type, content, authority, a source link, and open gaps or conflicts. Results are filtered by the caller's permissions **before** any LLM summary is generated. The MVP reply is a list, with no generated summary.

### 12.3 Proactive alerts (stretch)

- Off by default and enabled per channel.
- Alerts fire only for new gaps with severity `high`, at most one alert per `(entity, subject_key, slot)` ever, unless the gap is reopened.
- The alert posts in the channel mapped to the gap's `to_stage`, with **Review** (opens the dashboard) and **Ignore** buttons (writes a `reviews` row, gap → `ignored`).

### 12.4 Plumbing

- The Slack request signature is verified on every request, and the `url_verification` challenge is handled.
- Local dev uses a tunnel (`cloudflared` or `ngrok`) for the Slack request URL.
- The bot token and signing secret are env vars and never committed.

---

## 13. Processing, caching, jobs

### 13.1 Pipeline

```
ingest → normalize → content_hash → dedupe source version (tenant, kind, external_id, content_hash)
      → enqueue job → [worker] extract → validate (Pydantic) → evidence-span check
      → resolve entity → assign authority/confidence → embed
      → dedupe context → lifecycle/supersession/conflict → persist + versions
      → lineage (derived_from) → maybe trigger handoff validation → maybe route to review
```

Ingestion endpoints only normalize, hash, and enqueue. They return 200 in under 300 ms (Slack requires a response within 3 s).

### 13.2 Jobs (C8)

- `JobQueue` interface with `enqueue`, `claim`, `ack`, and `fail`. The MVP implementation is Postgres `processing_jobs` using `SELECT ... FOR UPDATE SKIP LOCKED`. SQS is a later drop-in.
- Jobs are idempotent: the idempotency key is `(job_type, source_id, content_hash)`.
- Retries use exponential backoff, max 3 attempts. After that the job moves to `status = poison` with the error stored.
- Job status is visible in the dashboard (queued / running / done / failed / poison counts).

### 13.3 Caches

| Cache | Key | Store |
|---|---|---|
| Source processing | `(source_id, version, content_hash)` | Postgres unique constraint |
| Extraction | `sha256(content_hash, prompt_version, model_id, schema_version)` | Redis + `extraction_key` column |
| LLM judge | `sha256(u.id, u.version, d.id, d.version, slot, prompt_version, model_id)` | Redis |
| Retrieval | `(principal_scope_hash, query, max(context version) for entity)` | Redis, TTL 10 min |

Retrieval results are never cached without the principal scope in the key.

---

## 14. Security and permissions (MVP level)

- Each source row has an `acl`. Context objects inherit the ACL from their source (the intersection when supported by several). Queries filter by `acl && user_principals`. Slack private-channel ACL = channel members at ingest time. Mock sources = `["*"]`.
- Provenance returned to a user who lacks access to the source is redacted to `{kind, stage, source_ts}`.
- Tokens and secrets live only in env vars (Secrets Manager post-MVP). Structured logging with a redaction filter keeps message bodies and tokens out of logs, which log IDs and hashes only.
- Every review action is an append-only `reviews` row with the reviewer and timestamp.
- `tenant_id` is on every table and in every query path, even though the MVP runs a single tenant.

---

## 15. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health (DB, Redis, queue depth) |
| POST | `/sources/slack/events` | Slack Events API |
| POST | `/sources/slack/interactions` | Slack shortcuts and buttons **(new)** |
| POST | `/sources/mock/load` | Ingest `/mock-data` (dev only) **(new)** |
| POST | `/context/mark` | Create candidate context from text + source ref |
| GET | `/context/search?entity=&q=&type=&status=` | Search current context (permission-filtered) |
| GET | `/context/{id}` | Object + evidence + provenance |
| GET | `/context/{id}/history` | Versions + supersession chain |
| GET | `/context/{id}/lineage` | Upstream/downstream traversal **(new)** |
| GET | `/entities` | List entities **(new)** |
| GET | `/entities/{id}/context` | Grouped current context, gaps, conflicts, source counts |
| POST | `/context/{id}/review` | Confirm / edit / ignore / mark_stale |
| POST | `/conflicts/{relation_id}/resolve` | Pick a winner or supersede both **(new)** |
| POST | `/handoffs/validate` | `{entity_id, contract_id, as_of?}` → validation id |
| GET | `/handoffs/{id}` | Validation result + gaps |
| GET | `/gaps?entity=&status=` | Review queue for gaps **(new)** |
| POST | `/gaps/{id}/review` | Accept / ignore gap **(new)** |
| GET | `/jobs/stats` | Queue observability **(new)** |

---

## 16. Data model

```
users(id, tenant_id, person_id, role)                      -- app login accounts
people(id, tenant_id, email unique, slack_user_id unique, name, team, role, is_external,
       company_entity_id null)                              -- directory §3.1
org_domains(domain, tenant_id, is_internal, entity_id null) -- internal domains + customer domains
entities(id, tenant_id, name, slug, kind)
entity_aliases(entity_id, alias_normalized unique per tenant)
sources(id, tenant_id, kind, external_id, version, content_hash, stage, acl, provenance jsonb,
        text, source_ts, ingested_at; unique(tenant_id, kind, external_id, content_hash))
context_objects(§4.3)
context_versions(id, context_id, version, status, attributes, content, changed_by, reason, created_at)
context_relations(id, from_id, to_id, relation, created_by, confidence, created_at, resolved_at)
capability_vocab(slug, parent_slug, synonyms[])
context_contracts(id, from_stage, to_stage, spec jsonb, version)
handoff_validations(id, entity_id, contract_id, as_of, input_hash, summary jsonb, created_at)
context_gaps(id, validation_id, contract_field, upstream_id, downstream_id null, slot, outcome,
             severity, severity_band, inherited bool, explanation, status open|ignored|resolved)
reviews(id, reviewer_id, target_kind, target_id, action, before jsonb, after jsonb, note, created_at)
processing_jobs(id, job_type, idempotency_key unique, payload, status, attempts, last_error, run_after)
eval_cases(id, category, input jsonb, expected jsonb, origin seed|review)
eval_results(id, case_id, run_id, passed, actual jsonb, model_id, prompt_version, created_at)
```

`permissions` from v0.1 is folded into `sources.acl` for the MVP. The table returns with real RBAC.

---

## 17. Dashboard

1. **Entity picker** shows entities with source counts per kind and the number of open gaps and conflicts.
2. **Context Explorer** (per entity) has tabs for Current (grouped: requirements, decisions, constraints, dependencies, commitments, problems/questions), Conflicts, Gaps, and History. Deadlines show inline from `due_date`.
3. **Detail panel** opens when you click an item. It shows the evidence quote highlighted in the source text, provenance fields, authority/confidence, versions, and a lineage chain back to root evidence.
4. **Handoff report** has a chain view per root requirement: slot × stage grid (✓ preserved, ~ generalized, ✗ missing, ! contradicted), with origin vs. inherited losses.
5. **Review queue** lists candidates, conflicts, and gaps. Actions: Confirm, Edit, Ignore, Resolve Conflict, Mark Stale. Edits create a new version, and the evidence stays untouched.

---

## 18. Mock data (`/mock-data`)

```
drive/sales/acme-requirements.md        SAML, Okta, Dec 15, SCIM required, audit logs 1y
drive/sales/acme-customer-notes.md      "Acme Corp" alias; priority P0 for SSO
drive/product/acme-prd.md               "Support SSO", target "December"; SCIM deferred
drive/product/acme-product-decisions.md SCIM deferred to Q1 (rationale); audit logs 90d (contradiction)
drive/engineering/acme-technical-design.md   "Implement SSO"; no IdP, no date
drive/engineering/acme-implementation-notes.md  SCIM work started
directory.json                          people (sales, product, engineering) + internal and customer domains
email/acme-security-thread.json         customer confirms SAML + Okta; SCIM required (external → sales)
email/internal-acme-sso-handoff.json    sales AE → PM: "Acme needs SSO by December" (sales stage, degraded restatement)
email/internal-scim-scope.json          PM → eng lead: SCIM deferred (product stage via directory)
calls/acme-discovery-2026-10-02.txt     CUSTOMER: SAML via Okta by Dec 15 (requirement)
                                        SALES: "we can have a beta by Dec 1" (commitment)
                                        SALES: "you'd probably want SCIM too" (suggestion, authority 1)
slack/seed.json                         messages for #acme-deal (sales), #product (product), #eng-auth (engineering)
                                        incl. SCIM required → deferred → required sequence by one PM (supersession, R3)
                                        and a second PM contradicting the first without correction language (conflict, R4)
                                        and a paraphrase that must NOT be flagged ("SAML-based SSO with Okta")
```

**Scenario 2: Globex onboarding with a sales error**

```
calls/globex-closing-2026-11-04.txt     CUSTOMER: EU data residency (eu-west-1), 500 seats, Salesforce integration, go-live Jan 10
                                        SALES: "Salesforce connector is included on Enterprise" (commitment)
email/internal-globex-handoff.json      AE → CS: "Globex, 50 seats, US region fine, go-live January" (AE error: 500→50, EU→US)
drive/customer_success/globex-onboarding-plan.md   provisions us-east-1, 50 seats, no Salesforce, go-live "Q1"
drive/product/pricing-decisions.md      Salesforce connector is a paid add-on, not in Enterprise (contradicts the sales commitment)
slack/seed.json #globex-onboarding (customer_success)  CS: "kickoff done, provisioning US tenant tomorrow"
```

**Scenario 3: Cloud outage at peak hours**

```
slack/seed.json #incidents (engineering)
  2026-11-27 18:10 IST  on-call: "checkout API 5xx spike, suspect our 18:00 deploy"      (root_cause v1)
  2026-11-27 18:40 IST  on-call: "rolled back, no change; AWS us-east-1 ELB degraded"   (root_cause v2 supersedes v1)
  2026-11-27 18:52 IST  "recovered. 12% of requests failed 18:05–18:52, Acme + Globex affected, ~300 writes queued, replaying"
drive/engineering/postmortem-INC-2311.md   window 18:05–18:52 IST (peak), root cause AWS ELB, Acme contract 99.95% → monthly SLA breached,
                                           remediation: multi-AZ failover by Dec 15
email/acme-incident-update.json         Sales → Acme: "brief blip this evening, fully resolved, no data impact"
                                        (loses the window and the SLA breach, contradicts data_loss)
slack/seed.json #acme-deal (sales)      AE: "outage was our bad deploy, apologised to Acme"  (stale root cause, v1)
```

Across the three scenarios, the mock data deliberately contains overlap, loss, one paraphrase that should not be flagged, supersession chains (SCIM, incident root cause), a cross-stage conflict (SCIM, pricing), a same-stage human error (AE vs. customer), and a reverse-direction handoff (incident).

---

## 19. Evaluation (C10, C11, C14)

### 19.1 Golden tests (must pass)

There are three golden scenarios. Each is its own test with its own fixture subset.

**Golden 1: IAM requirement degradation (Acme SSO)**

Inputs: the call, email, sales doc, product PRD, and engineering design from §18.

Expected results:

| # | Handoff | Subject | Slot | Outcome | Origin |
|---|---|---|---|---|---|
| G1 | sales→product | acme:sso | protocol | generalized (SAML→SSO) | origin |
| G2 | sales→product | acme:sso | idp | missing (Okta) | origin |
| G3 | sales→product | acme:sso | due_date | generalized (day→month) | origin |
| G4 | product→eng | acme:sso | protocol | generalized | inherited |
| G5 | product→eng | acme:sso | idp | missing | inherited |
| G6 | product→eng | acme:sso | due_date | missing | origin |
| G7 | — | acme:sso | — | the product "Support SSO" object has `derived_from` → the customer call requirement (not an unrelated requirement) | — |
| G8 | — | — | — | every gap resolves to evidence whose span matches the call transcript or email text | — |
| G9 | — | acme:sso | — | the Slack paraphrase "SAML-based SSO with Okta" produces no gap | — |

**Golden 2: Globex onboarding with a sales error**

| # | Where | Subject | Slot | Expected | Notes |
|---|---|---|---|---|---|
| O1 | within sales | globex:data_residency | region | customer `EU` (auth 4) vs. AE `US` (auth 2) → `conflicting`, sent to review | same-stage error; must **not** supersede (lower authority) |
| O2 | within sales | globex:seats | quantity | 500 vs. 50 → `conflicting` | same |
| O3 | sales→CS | globex:data_residency | region | contradicted, severity high, tagged `upstream_conflict` | reference is the customer's EU statement, not the AE note |
| O4 | sales→CS | globex:seats | quantity | contradicted, tagged `upstream_conflict` | |
| O5 | sales→CS | globex:integration | integrations | object_missing (Salesforce) | |
| O6 | sales→CS | globex:onboarding | due_date | generalized (Jan 10 → Q1) | |
| O7 | cross-stage | globex:pricing | plan | sales commitment "Salesforce included on Enterprise" `contradicts` product pricing decision | commitment never overrides a product decision |
| O8 | Slack alert (if stretch done) | globex:data_residency | region | one high-severity alert in #globex-onboarding before provisioning | |

**Golden 3: Cloud outage at peak hours (INC-2311)**

| # | Where | Subject | Slot | Expected | Notes |
|---|---|---|---|---|---|
| I1 | within engineering | acme:incident:INC-2311 | root_cause | v2 (AWS ELB) supersedes v1 (our deploy); history keeps v1 | R3: same on-call author, and "rolled back... AWS" is an explicit correction |
| I2 | eng→sales | acme:incident:INC-2311 | time_window | missing in customer email ("brief blip") | 47 min at peak hours |
| I3 | eng→sales | acme:incident:INC-2311 | sla_impact | missing; severity high | SLA breach + credit owed not communicated |
| I4 | eng→sales | acme:incident:INC-2311 | impact.data_loss | contradicted ("no data impact" vs. ~300 writes replayed) | |
| I5 | eng→sales | acme:incident:INC-2311 | root_cause | AE Slack message cites superseded v1 → flagged as **stale context in use** | uses the `superseded` status, not a gap on active context |
| I6 | eng→sales | acme:incident:INC-2311 | remediation | object_missing | |
| I7 | entity | — | — | the incident links to **both** Acme and Globex (`impact.customers`), and Globex shows it in the Explorer even though no Globex email was sent | |

**Pass definition:** every row in all three golden tables holds on 5 consecutive runs, at temperature 0, with the extraction and judge caches disabled.

### 19.2 Sets

| Category | MVP minimum | Measures |
|---|---|---|
| Degradation | **≥10** (at least 3 per scenario) | Gap precision/recall per outcome |
| Extraction | 5 | Type, slot, and authority field accuracy |
| Entity | 5 | Alias resolution accuracy |
| Dedup | 3 | Grouping accuracy |
| Lifecycle / supersession | 3 | Correct current state |
| Conflict | 3 | Contradiction detection vs. supersession |
| Provenance | 3 | Span/evidence correctness |
| Permission | 3 | No leak across ACL |
| Lineage | 3 | Correct `derived_from` edges |
| Contract | 3 | Required-field check correctness |

Every `reviews` row with action `edit` or `resolve_conflict` can be exported to `eval_cases` with `origin = review` (`make eval-export`).

### 19.3 Retrieval baseline (C10)

The baseline embeds all source chunks, then for each downstream doc retrieves the top-k upstream chunks and asks the same LLM "which important upstream details are missing or changed downstream?". Its output is scored on the degradation set with the same rubric. The MVP claim holds if the engine beats the baseline on gap **precision** without losing recall. Report both numbers.

---

## 20. Stack and repo

```
/api        FastAPI, SQLAlchemy 2, Alembic, Pydantic v2
/worker     same package, `python -m app.worker`
/web        React + Vite + TypeScript
/mock-data
/contracts  YAML seeds
/evals      cases + runner (pytest-based) + baseline
/docs       this spec
docker-compose.yml: postgres (pgvector image, pg_trgm enabled), redis, api, worker, web
.env.example   all config; .env git-ignored
```

The LLM provider, embedding provider, queue implementation, and Slack credentials are all selected by env var.

---

## 21. Revised 3-day plan

**Day 1: one pipeline, all sources → Context Objects**

- Compose stack, schema + Alembic, Pydantic models, `SourceConnector`, `JobQueue` (Postgres), `LLMClient`, `EmbeddingClient`.
- People directory + domain table, seeded from `directory.json`. Stage/role resolution for every source.
- Mock connectors (drive, email, call) plus the mock data from §18.
- Extraction with evidence-span validation, authority rules, extraction cache.
- Slack: Events API, signature verification, message shortcut → candidate objects.
- Exit: `POST /sources/mock/load` plus one Slack shortcut produce objects whose evidence spans all verify. Extraction eval (5 cases) runs.

**Day 2: unified state**

- Entity resolution, dedup, lifecycle, versioning, supersession/conflict rules, `derived_from` lineage.
- `@context <entity>`, `/entities/{id}/context`.
- Dashboard: entity picker, Context Explorer, detail panel with evidence + lineage.
- Exit: the SCIM supersession and conflict scenarios produce the expected current state. Lifecycle, conflict, and entity evals pass.

**Day 3: prove the differentiation**

- Contracts (YAML → DB), handoff validator, severity, explanations, chain attribution.
- Handoff report + review queue UI, review actions → `reviews` → eval export.
- Golden test + degradation set + retrieval baseline.
- Stretch: proactive Slack alerts.
- Exit: all three golden tests pass 5/5. The degradation set is scored against the baseline.

**Post-MVP (next):** SQS + Floci, Terraform + ECS/Fargate/RDS/S3/Secrets Manager/CloudWatch, real Gmail/Drive connectors, full eval sets, and Context Continuity Rate / Context Debt metrics.

---

## 22. Open questions

None open.

Resolved:
- Email stage comes from sender → directory (§3.1).
- The Slack app is named `@context`.
- Any reviewer can approve new capability slugs.
- Any product-team member's decision has authority 4, because every person is accountable.
- Every Context Object has a source (§4.3).
