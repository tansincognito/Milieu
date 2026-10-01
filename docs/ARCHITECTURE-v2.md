# Architecture v2 — org-agnostic, connector-first

Status: proposed. Supersedes the parts of `SPEC.md` named in §11; everything not named there
still stands.

## 1. What v1 got wrong

v1 proved the engine works. It also baked one company into the code, has no notion of a user,
and leads with the engine's internals instead of the user's problem.

**The org is hardcoded.** Four places:

| Where | What is hardcoded |
|---|---|
| `api/app/models/orm.py:34` | `STAGES = ("sales", "product", "engineering", "customer_success")`, enforced by a CHECK constraint |
| `api/app/models/orm.py:45` | `ACTOR_ROLES` — a fixed seven-value list |
| `api/app/pipeline/prompts.py` | `CAPABILITY_VOCAB` — `sso`, `scim`, `uptime_sla`, `incident`… one company's product surface |
| `mock-data/directory.json` | people, departments, roles and domains, hand-authored |

An org whose delivery flow is Sales → Solutions → Platform cannot be represented at all. The
CHECK constraint rejects it at write time.

**There is no notion of connecting anything.** `POST /sources/mock/load` reads a directory off
disk in one shot. No account, no consent, no incremental sync, no cursor, no failure state —
so the demo path and any real path share no code.

**There is no user.** Permissions exist (§14) but the caller passes `principal` as a query
parameter. Nobody logs in, so nothing can be scoped to *you*.

**The UI leads with the data model.** First screen: a list of "entities". Second: "context
objects" grouped by type, with a "subject key" and a raw authority integer. Those are the
engine's words. The screen stating the actual value — what got lost between teams — is three
clicks deep and only populates if you press a button.

## 2. The shift

**The org becomes data, sourced from the directory, and everything downstream derives from
it.** No department, role, capability or contract named in code or constrained by schema.

The engine's two jobs stay as they are and stay org-agnostic:

1. **Get the context accurately** — extract with evidence, resolve who said it and what it is about.
2. **Analyse it accurately** — track state, and detect what degrades across a handoff.

Everything org-specific becomes tenant configuration those two jobs read.

## 3. This is not a RAG system

The design principle that governs every choice below. Stating it because the surfaces in §9
are easy to mistake for search, and building them as search would destroy the product.

| | RAG | This |
|---|---|---|
| Unit | text chunk | versioned Context Object with typed slots |
| Answer | retrieved and re-derived per query | persisted state that is true *now* |
| Time | none — all chunks equal | supersession, corrections, validity windows (R1–R5) |
| Disagreement | both chunks returned, silently | an explicit conflict, routed to a human |
| Authority | none — the loudest or nearest chunk wins | organizational legitimacy, separate from extraction confidence |
| **Absence** | **invisible** | **the product** |

That last row is the whole thing. **Retrieval can only surface what exists.** It cannot tell
you the customer's December deadline never reached Engineering, because there is no chunk
saying so — the evidence of the loss is an *absence*, and you cannot embed an absence. Milieu
detects it by holding a structured expectation (the contract) and checking it slot by slot
across a handoff.

Consequences that follow, and must not be traded away:

- **Never answer from chunks.** Every surface reads Context Objects and gaps. If a screen
  would be easier to build with a similarity search over raw text, it is the wrong screen.
- **Absence needs a schema.** You can only detect a missing `due_date` if `due_date` is a
  slot. This is why typed slots exist and why free-text contracts were cut in v1.
- **Every claim carries its evidence.** A gap the user cannot trace to a quote is
  indistinguishable from a hallucination. The evidence-span invariant is a product feature,
  not a database constraint.
- **The retrieval baseline (§19.3) is the proof.** It exists to be beaten on *precision*, and
  keeping it honest matters more than winning — if a naive retrieval baseline matches us,
  there is no product.

## 4. Layers

```
Connections   Gmail · Drive · Slack · Directory (AD/Entra/Okta/Workspace)
                  auth, cursor, sync runs, per-connector health
                                    |
Org graph     departments · people · roles · delivery flow · domains
                  (synced from Directory, confirmed once by an admin)
                                    |
Identity      session · email domain -> tenant · person -> department -> scope
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
Surfaces      org status · open items · account view · new-hire brief · review · Slack
```

New: **Org graph**, **Identity**. **Connections** replaces the mock loader. Ingestion down
already exists and does not change shape.

## 5. Connections

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
same Protocol over seed files, so a demo tenant and a real tenant differ by one row. Today's
mock loader exercises code that will never run in production — the demo proves nothing about
the real path.

## 6. The directory is the keystone

Stage, role, authority and internal-vs-external all derive from it. Get it wrong and every
downstream number is wrong — so it syncs **first**, and content processing waits on it.

Per person it yields: name, email, Slack id, **department**, **title/role**, manager, account
status. From that:

- **department** → the stage a source belongs to. No enum.
- **title + department** → authority, via a tenant-editable policy (§7.3).
- **email domain** → internal, or an external party mapped to a customer entity.
- **the same rows back the login** (§8) — one directory serves both.

Ordering rule: content connectors may be authorized any time, but a source whose author cannot
be resolved is held `pending_directory` rather than processed with a null stage. Processing an
email before you know who sent it is how v1 produced objects whose actor was the string
`"null"` and eight objects on an `unknown` entity.

## 7. The three org-agnostic mechanisms

### 7.1 Departments replace stages

Drop the `STAGES` CHECK constraint. Add:

```
departments
  id, tenant_id, slug, name, source (directory|manual)
  flow_position int | null      -- order in the delivery flow; null = not in the flow
```

`sources.stage` and `context_objects.stage` become FKs. Validation moves from a global CHECK
to a per-tenant FK — stronger, not weaker.

**Who decides flow order?** The admin, once, at onboarding. AD gives department names but no
ordering, and the ordering is what makes a handoff a handoff. Inferring it from communication
patterns is a later refinement, not a v2 requirement — guessing wrong silently corrupts every
gap the engine reports, with no way for the user to see it.

### 7.2 Capability vocabulary becomes per-tenant and learned

`capability_vocab` exists but is global and pre-seeded. Add `tenant_id`, start **empty**, grow
from extraction: a capability the model proposes that isn't in the tenant's vocabulary lands
as `candidate` for review. That routing already exists (`_determine_status`).

The hierarchy the validator's generalization check needs (`saml`, `oidc` refine `sso`) becomes
a per-tenant edge a reviewer confirms, not a dict literal in `api/app/pipeline/handoff.py`.
This also fixes the v1 finding that `CAPABILITY_GENERALIZES` and `SLOT_EQUIVALENTS` hold one
entry each, shaped to the golden fixtures.

### 7.3 Authority becomes a policy, not a function

```
authority_rules
  id, tenant_id, priority int
  match (department_slug | title_pattern | is_external | is_customer)
  authority 0..4
```

Ship a default set; let the admin adjust. `assign_authority` evaluates rules in priority order
instead of hardcoding role→authority. The 0–4 scale and its separation from confidence do not
change — only where the mapping comes from.

### 7.4 Contracts bind to departments, not names

Contracts today are keyed `sales_to_product`. They become `(from_department, to_department)` in
the tenant's own graph, instantiated at onboarding from a template library by flow position:
"the handoff from position 1 to position 2 carries the customer's stated requirements." Field
definitions stay as they are.

## 8. Identity and access

Login sits at the top of the landing page. Email in, and the person sees their own org.

```
sessions
  id, tenant_id, person_id, email, issued_at, expires_at, last_seen_at
```

**Domain → tenant.** `org_domains` already maps a domain to a tenant and carries
`is_internal`. A login at `dana@tenet.com` resolves to Tenet through that table. An unknown
domain gets a "no workspace found" path, never a silent empty dashboard.

**Person → scope.** The session's `person_id` is a directory row, so login yields the user's
department and role for free. That drives:

- **What they see first** — an Engineering lead lands on their own inbound handoffs, not a
  global list.
- **What they may see at all** — §14's ACL filtering stops being a query parameter a caller
  asserts and becomes a fact derived from the session. This is a security improvement, not
  only a UX one: today any caller can claim any principal.

**How they authenticate.** Google Workspace is already a connection for Gmail and Drive, so
Google SSO adds no new consent and guarantees the email is real. Email magic-link as the
fallback for orgs on another directory. No passwords.

**Least privilege.** A member sees their own org's status, items and briefs. Connecting a
source, confirming the delivery flow, and editing authority rules are admin-only — they change
how every number is computed.

## 9. Surfaces

The engine keeps its vocabulary internally and stops showing it. Never display "context
object", "subject key", or a bare authority integer. Say *requirement*, *decision*,
*commitment*; say *"stated by the customer"* and *"decided by Product"*.

### 9.1 Landing — login, then org status

Unauthenticated: what the product does, and a login field.

Authenticated, the org's state in one screen:

- **What's slipping** — ranked live losses across accounts. *"Northwind: the December deadline
  the customer stated never reached Engineering."* Each row opens its evidence.
- **Open items** (§9.2) — with deadlines, soonest first.
- **Needs a human** — conflicts and review candidates, counted.
- **Coverage** — which connectors are syncing, when they last ran, what failed. A stale Slack
  connection must be visible, because every other number silently depends on it.

### 9.2 Open items

One ranked list, assembled from state already in the database — not a new subsystem:

| Item | Source |
|---|---|
| Unanswered question | `open_question` objects, still `active` |
| Contested decision | unresolved `contradicts` relations |
| Lost context | `context_gaps`, `status='open'` |
| Commitment coming due | `commitment` with an `attributes.due_date` |
| Deadline passed, nothing shipped | `due_date` in the past, no downstream resolution |

Deadlines render at their stated precision — `due_date` already carries day/month/quarter, so
"Q1" must never be shown as a false "Jan 1". Sort by date, then severity. Each row states who
said it, when, and links to the quote.

### 9.3 New-hire onboarding

The clearest expression of §3, and the one that is impossible with retrieval.

A new hire's real question is *"what does everyone here already know that nobody wrote down in
one place?"* Search cannot answer it: they do not know the keywords, and the answer is a
*state* assembled from many sources, none of which contains it.

Scoped to their department from the directory, generated from current state:

- **In force now** — the decisions and constraints governing their area, each with who decided
  and when. Current versions only, superseded ones hidden by default.
- **Why, not just what** — the lineage chain behind each decision, so *"we don't do X"* comes
  with the reversal that produced it. This is the institutional memory that normally leaves
  with the person who has it.
- **Still open** — questions and conflicts nobody has resolved.
- **What we owe** — commitments their team carries, with deadlines.
- **Who decides what** — the departments and people upstream and downstream of them.

Generated on demand from `active` objects plus lineage, evidence links throughout. A new hire
who cannot verify a claim has been handed folklore.

### 9.4 Account view and review queue

Account view: one customer — what they asked for, what was promised, what each team recorded,
where the chain broke. The handoff grid lives here, already populated. Review queue unchanged
in function.

## 10. Seed org: Tenet

Replaces `mock-data/` as `seed-orgs/tenet/`, served through the `demo` provider.

- **Directory**: ~14 people across Sales, Product, Engineering, plus a Finance department
  deliberately *not* in the delivery flow, to prove flow position is data. Real titles, so
  authority rules have something to match.
- **Customers**: two, with domains and aliases, so entity resolution has real work.
- **Gmail**: internal and customer threads, including one where a rep restates a customer
  requirement incorrectly.
- **Drive**: requirements doc, PRD, technical design, postmortem.
- **Slack**: `#sales`, `#product`, `#eng`, `#incidents`, including a same-day self-correction
  and a genuine two-person disagreement.
- **A login-able person per department**, so the personalized landing and the new-hire brief
  can be demoed as three different people.

The three narratives from `SPEC.md` §18 carry over intact — they are good tests — re-cast onto
Tenet. The scenarios are the asset; the company names were never the point.

## 11. What this supersedes in SPEC.md

- **§3** stages — replaced by departments from the directory (§7.1).
- **§3.1** people directory — replaced by directory sync (§6).
- **§9** contract identity — contracts bind to department pairs (§7.4).
- **§14** principal-as-query-param — replaced by session-derived identity (§8).
- **§17** dashboard — replaced by the surfaces in §9.
- **§18** mock data — replaced by `seed-orgs/tenet/` (§10).

Unchanged and still authoritative: §4 Context Object, §5 the authority *scale*, §6 sources and
the evidence-span invariant, §7 lifecycle R1–R5, §8 lineage, §10 the validator and its
outcomes, §11 extraction, §13 pipeline, §19 evaluation.

## 12. Migration

Ordered so the suite stays green. Each step independently shippable.

| # | Step | Risk |
|---|---|---|
| 1 | Add `departments`; migrate the four current stages into rows; CHECK → FK | Low — mechanical |
| 2 | Add `connections` + `sync_runs`; move mock loading behind a `demo` provider | Low — additive |
| 3 | Directory connector + the `pending_directory` hold | Medium — changes when jobs run |
| 4 | `sessions` + login + session-derived ACLs | Medium — replaces the `principal` param everywhere |
| 5 | `authority_rules`; `assign_authority` reads the policy | Medium — changes existing numbers; re-pin lifecycle tests |
| 6 | Per-tenant `capability_vocab`; validator hierarchy reads from it | Medium — touches validator classification |
| 7 | Contracts bind to department pairs | Low once 1 lands |
| 8 | Seed org Tenet; retire `mock-data/` | Low |
| 9 | Open items + org status | Low — queries over existing state |
| 10 | New-hire brief | Low — same state, different assembly |

**Sequencing note:** steps 5 and 6 change engine *outputs*, so the eval harness needs a clean
baseline run before them. That baseline is blocked on the LLM tier, which makes the model-tier
decision a prerequisite for the middle of this plan, not a side quest.

## 13. Open questions

1. Which directory first — Google Workspace, Entra, or Okta? Workspace pairs with Gmail and
   Drive and is likely one OAuth consent instead of two.
2. Does an org without a clean department→flow mapping (a matrix org, or one shared "Delivery"
   department) break the handoff model, or just produce fewer handoffs?
3. Gmail scope: full mailbox, or only threads with external participants? The narrower scope is
   a much easier security conversation and probably loses little.
4. Does the new-hire brief need a freshness guarantee? A brief assembled from state that is a
   week stale is worse than none, because the reader cannot tell.
