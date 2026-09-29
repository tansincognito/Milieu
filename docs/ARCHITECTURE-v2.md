# Architecture v2 — org-agnostic, connector-first

Status: proposed. Supersedes the parts of `SPEC.md` named in §9 below; everything not named
there still stands.

## 1. What v1 got wrong

v1 proved the engine works. It also baked one company into the code, and it leads the user
with the engine's internals instead of their problem.

**The org is hardcoded.** Four places:

| Where | What is hardcoded |
|---|---|
| `api/app/models/orm.py:34` | `STAGES = ("sales", "product", "engineering", "customer_success")`, enforced by a CHECK constraint |
| `api/app/models/orm.py:45` | `ACTOR_ROLES` — a fixed seven-value list |
| `api/app/pipeline/prompts.py` | `CAPABILITY_VOCAB` — `sso`, `scim`, `uptime_sla`, `incident`… one company's product surface |
| `mock-data/directory.json` | people, departments, roles and domains, hand-authored |

An org whose delivery flow is Sales → Solutions → Platform cannot be represented at all. The
CHECK constraint rejects it at write time.

**There is no notion of connecting anything.** `POST /sources/mock/load` reads a directory
off disk in one shot. There is no account, no consent, no incremental sync, no cursor, no
failure state — so the demo path and any real path share no code.

**The UI leads with the data model.** The first screen is a list of "entities"; the second
shows "context objects" grouped by type, with a "subject key" and a raw authority integer.
Those are the engine's words, not the user's. The screen that states the actual value — what
got lost between teams — is three clicks deep and only populates if you press a button.

## 2. The shift

**The org becomes data, sourced from the directory, and everything downstream derives from
it.** No department, role, capability or contract is named in code or constrained by schema.

The engine's two jobs stay exactly as they are and stay org-agnostic:

1. **Get the context accurately** — extract with evidence, resolve who said it and what it is about.
2. **Analyse it accurately** — track state, and detect what degrades across a handoff.

Everything org-specific becomes tenant configuration that those two jobs read.

## 3. Layers

```
Connections   Gmail · Drive · Slack · Directory (AD/Entra/Okta/Workspace)
                  auth, cursor, sync runs, per-connector health
                                    |
Org graph     departments · people · roles · delivery flow · domains
                  (synced from Directory, confirmed once by an admin)
                                    |
Ingestion     normalize -> hash -> dedupe -> enqueue
                                    |
Understanding extract (evidence-bound) -> resolve entity -> resolve actor
              -> authority + confidence -> embed
                                    |
State         dedup · supersede/conflict (R1-R5) · lineage
                                    |
Analysis      contracts -> handoff validator -> gaps -> severity -> attribution
                                    |
Surfaces      "What's slipping" · account view · review queue · Slack bot
```

Only **Org graph** is new. **Connections** replaces the mock loader. Everything from
Ingestion down already exists and does not change shape.

## 4. Connections

Two new tables.

```
connections
  id, tenant_id, kind (gmail|drive|slack|directory), provider (google|microsoft|okta|slack|demo)
  status (disconnected|pending|connected|error), external_account, scopes[]
  cursor, last_sync_at, last_error, created_at

sync_runs
  id, connection_id, started_at, finished_at
  status (running|ok|partial|failed), items_seen, items_ingested, items_failed, error
```

The connector Protocol (`api/app/connectors/base.py`) gains incremental sync:

```python
class SourceConnector(Protocol):
    kind: SourceKind

    def list_changes(self, cursor: str | None) -> tuple[Iterable[RawSource], str]: ...
    def fetch(self, since: datetime | None) -> Iterable[RawSource]: ...
    def normalize(self, raw: RawSource) -> NormalizedSource: ...
```

**The demo org is a provider, not a separate code path.** `provider="demo"` implements the
same Protocol over seed files. A demo tenant and a real tenant differ by one row. This is the
main reason to do connections properly now: today's mock loader exercises code that will
never run in production, so the demo proves nothing about the real path.

## 5. The directory is the keystone

Stage, role, authority and internal-vs-external all derive from the directory. Get it wrong
and every downstream number is wrong — so it syncs **first**, and content processing waits
on it.

Directory sync yields, per person: name, email, Slack id, **department**, **title/role**,
manager, and account status. From that:

- **department** → the stage a source belongs to. No enum.
- **title + department** → authority, via a tenant-editable policy (§6.3).
- **email domain** → internal, or an external party mapped to a customer entity.

Ordering rule: content connectors may be authorized at any time, but a source whose author
cannot be resolved is held in a `pending_directory` state rather than processed with a null
stage. Processing an email before you know who sent it is how v1 ended up with objects whose
actor was the string `"null"` and eight objects on an `unknown` entity.

## 6. The three org-agnostic mechanisms

### 6.1 Departments replace stages

Drop the `STAGES` CHECK constraint. Add:

```
departments
  id, tenant_id, slug, name, source (directory|manual)
  flow_position int | null      -- order in the delivery flow; null = not in the flow
```

`sources.stage` and `context_objects.stage` become FKs to `departments`. Validation moves
from a global CHECK to a per-tenant FK — stronger, not weaker.

**Who decides the flow order?** The admin, once, during onboarding. AD gives department names
but no ordering, and the ordering is what makes a handoff a handoff. Present the departments
found and have the admin arrange the ones that form the delivery path. Two or ten, any names.
Inferring order from communication patterns is a later refinement, not a v2 requirement —
guessing wrong here silently corrupts every gap.

### 6.2 Capability vocabulary becomes per-tenant and learned

`capability_vocab` already exists as a table but is global and pre-seeded. Add `tenant_id`,
start it **empty**, and grow it from extraction: a capability the model proposes that isn't
in the tenant's vocabulary lands as `candidate` for review. That routing already exists
(`_determine_status` marks new capabilities as candidates).

The hierarchy that the validator's generalization check needs (`saml`, `oidc` refine `sso`)
becomes a per-tenant edge the reviewer confirms, not a dict literal in
`api/app/pipeline/handoff.py`.

This also fixes the v1 finding that the validator's `CAPABILITY_GENERALIZES` and
`SLOT_EQUIVALENTS` tables hold one entry each, shaped to the golden fixtures.

### 6.3 Authority becomes a policy, not a function

```
authority_rules
  id, tenant_id, priority int
  match (department_slug | title_pattern | is_external | is_customer)
  authority 0..4
```

Ship a sensible default set; let the admin adjust. `assign_authority` evaluates rules in
priority order instead of hardcoding role→authority. The 0–4 scale and its separation from
confidence do not change — only where the mapping comes from.

### 6.4 Contracts bind to departments, not names

Contracts today are keyed `sales_to_product`. They become `(from_department, to_department)`
in the tenant's own graph, instantiated at onboarding from a template library by flow
position: "the handoff from position 1 to position 2 carries the customer's stated
requirements." Field definitions stay as they are.

## 7. The user flow

The engine keeps its vocabulary internally and stops showing it.

**1 · Connect.** Four cards: Directory, Gmail, Drive, Slack. Directory is marked required
and explains why in one line: *"So we know who's who — which team someone is on decides how
their statements are weighed."*

**2 · Confirm your org.** "We found 6 departments and 47 people." Admin arranges the
departments that form the delivery flow and confirms. One screen, skippable defaults.

**3 · Syncing.** Per-connector progress, honest counts, visible failures. Not a spinner.

**4 · Landing: "What's slipping."** Not an entity list. A ranked list of live losses across
all accounts: *"Tenet → Northwind: the December deadline the customer stated never reached
Engineering."* Each row opens the evidence.

**5 · Account view.** One customer: what they asked for, what was promised, what each team
recorded, and where the chain broke. The handoff grid lives here, already populated.

**6 · Review queue.** Unchanged in function.

Language rules: never show "context object", "subject key", or a bare authority integer.
Say *requirement*, *decision*, *commitment*, and *"stated by the customer"* / *"decided by
Product"*. Keep evidence quotes and source links everywhere — that is the trust mechanism
and it is already the strongest part of the product.

## 8. Seed org: Tenet

Replaces `mock-data/` as `seed-orgs/tenet/`, served through the `demo` provider.

- **Directory**: ~14 people across Sales, Product, Engineering (plus a Finance department
  that is deliberately *not* in the delivery flow, to prove flow position is data).
- **Customers**: two, with domains and aliases, so entity resolution has real work.
- **Gmail**: internal threads and customer threads, including one where a rep restates a
  customer requirement incorrectly.
- **Drive**: a requirements doc, a PRD, a technical design, a postmortem.
- **Slack**: `#sales`, `#product`, `#eng`, `#incidents`, including a same-day correction
  (same author) and a genuine two-person disagreement.

The three narratives from `SPEC.md` §18 carry over intact — they are good tests — re-cast
onto Tenet and its customers. The scenarios are the asset; the company names were never the
point.

## 9. What this supersedes in SPEC.md

- **§3** stages — replaced by departments from the directory (§6.1 here).
- **§3.1** people directory — replaced by directory sync (§5).
- **§9** contract identity — contracts bind to department pairs (§6.4).
- **§17** dashboard — replaced by the flow in §7.
- **§18** mock data — replaced by `seed-orgs/tenet/` (§8).

Unchanged and still authoritative: §4 Context Object, §5 authority *scale*, §6 sources and
the evidence-span invariant, §7 lifecycle rules R1–R5, §8 lineage, §10 the validator and its
outcomes, §11 extraction, §13 pipeline, §14 security, §19 evaluation.

## 10. Migration

Ordered so the system stays green throughout. Each step is independently shippable.

| # | Step | Risk |
|---|---|---|
| 1 | Add `departments`; migrate the four current stages into rows; swap the CHECK for an FK | Low — mechanical |
| 2 | Add `connections` + `sync_runs`; move mock loading behind a `demo` provider implementing `list_changes` | Low — additive |
| 3 | Directory connector + the `pending_directory` hold | Medium — changes when jobs run |
| 4 | `authority_rules`; `assign_authority` reads the policy | Medium — changes existing numbers, so re-pin the lifecycle tests |
| 5 | Per-tenant `capability_vocab`; validator hierarchy reads from it | Medium — touches the validator's classification |
| 6 | Contracts bind to department pairs | Low once 1 lands |
| 7 | Seed org Tenet; retire `mock-data/` | Low |
| 8 | New surfaces (§7) | Low — additive UI |

**Sequencing note:** steps 4 and 5 change engine outputs, so the eval harness should produce
a clean baseline run *before* them. That baseline is currently blocked on the LLM tier, which
makes the model-tier decision a prerequisite for the middle of this plan, not a side quest.

## 11. Open questions

1. Which directory first — Google Workspace, Entra, or Okta? Workspace pairs naturally with
   Gmail and Drive and is likely one OAuth consent instead of two.
2. Does an org without a clean department→flow mapping (a matrix org, or one shared
   "Delivery" department) break the handoff model, or does it just produce fewer handoffs?
3. Gmail scope: full mailbox, or only threads with external participants? The narrower scope
   is a much easier security conversation and probably loses little.
