"""Extraction system prompt (§11).

v2 (bumped from v1 after the "empty attributes" bug): probing against live OpenRouter
free models showed two distinct causes for `attributes` coming back null while `content`/
`evidence_quote` narrated the same facts correctly:

1. The v1 prompt told the model to "fill slots present in the text" but never showed a
   filled-in example, so weak/free models treated `attributes` as optional decoration on
   top of `content` and left it null — the schema (all slots optional-with-null, none
   required) made that the path of least resistance under strict json_schema decoding.
   Fixed by an explicit "narration and slots are both required" rule plus one fully worked
   example (§ worked example below).
2. `due_date` still came back null even after fix (1), independent of `protocol`/`idp`
   filling correctly. Root cause: the source text states a bare day ("December 15th", no
   year) and the prompt never told the model what "today" is, so it had no anchor to
   resolve the year and defaulted to null rather than guess. Fixed by passing the source's
   own timestamp into the prompt as a `reference_date` and adding an explicit year-inference
   rule.
"""

from __future__ import annotations

from datetime import date, datetime

PROMPT_VERSION = "5"

CAPABILITY_VOCAB = (
    "sso, scim, audit_logs, data_residency, rbac, api_access, uptime_sla, pricing, "
    "integration, onboarding, seats, incident"
)

SYSTEM_PROMPT = f"""You extract structured Context Objects from a single source document for
a context-continuity engine. Read the text and return every distinct requirement, decision,
constraint, commitment, problem, open_question, resolution, or dependency it states.

Every item has two parts and BOTH are required whenever the text supports them:
  - narration (`content`, `evidence_quote`): what was said, in prose.
  - slots (`attributes.*`): the same facts, as structured fields.
`attributes` is not a summary of `content` — it is the machine-readable form of the same
facts. A field left null in `attributes` means "this fact is not in the text", never
"I already said it in `content`".

THE CORE RULE: if `evidence_quote` names a specific protocol, vendor/IdP, date, quantity,
region, plan tier, or integration, that exact value MUST also be copied into the matching
`attributes` slot. Never leave a slot null when its value is sitting in the evidence quote.

Slot list by relevance:
  - protocol (e.g. "SAML", "OIDC"), idp (e.g. "Okta", "Azure AD")
  - due_date (ISO date, YYYY-MM-DD) + due_date_precision (day/month/quarter — day when a
    specific date or day-of-month is stated, month when only a month is named, quarter when
    only a quarter is named). `due_date_precision` reflects the granularity actually stated
    in the text — it does NOT drop to a coarser precision just because you had to infer the
    year; see the year-inference rule below.
  - priority (P0/P1/P2), stance (required/deferred/in_progress/done/dropped/rejected)
  - acceptance_criteria (list of strings), rationale (string)
  - region (e.g. "eu-west-1", "EU"), quantity + quantity_unit (e.g. 500 seats),
    integrations (list, e.g. ["Salesforce"]), plan (e.g. "Enterprise")
  - incidents only: impact.customers/error_rate/data_loss, time_window.start/end/timezone,
    root_cause, sla_impact.breached/contract_uptime/credit_owed, remediation. An incident id
    (e.g. "INC-2311") goes in attributes.extra.incident_id.

Year inference for `due_date`: this document's reference date is given at the end of this
prompt. When the text states a date with no year ("December 15th", "March 3rd"), resolve the
year by taking the nearest occurrence of that month/day that is on or after the reference
date. Always resolve a full ISO date when a day-of-month is stated — do not leave `due_date`
null just because the year wasn't spelled out in the text.

Worked example (a different customer, do not reuse these values for any other document):

Reference date for this example: 2026-01-01.

Text: 'CUSTOMER: We need OIDC through Azure AD live by March 3rd — that is a hard date for
our board demo. We are buying 200 seats on the Enterprise plan.'

Correct extraction for that CUSTOMER line:
{{
  "type": "requirement",
  "subject_capability": "sso",
  "entity_hint": null,
  "content": "Customer needs OIDC through Azure AD live by March 3rd for a board demo.",
  "attributes": {{
    "protocol": "OIDC",
    "idp": "Azure AD",
    "due_date": "2026-03-03",
    "due_date_precision": "day",
    "quantity": 200,
    "quantity_unit": "seats",
    "plan": "Enterprise"
  }},
  "actor_label": "CUSTOMER",
  "evidence_quote": "We need OIDC through Azure AD live by March 3rd -- that is a hard date for our board demo. We are buying 200 seats on the Enterprise plan.",
  "confidence": 0.95,
  "corrects": false,
  "corrects_hint": null,
  "speculative": false
}}

Second worked example, an incident document (again a different customer):

Text: 'Window: 09:10-09:35 UTC, 25 minutes. 4% of requests failed. Root cause was a Cloudflare
DNS misconfiguration. Contoso's contract guarantees 99.9% monthly uptime; this breaches it and
a credit is owed.'

Correct extraction for that outage:
{{
  "type": "problem",
  "subject_capability": "incident",
  "entity_hint": "Contoso",
  "content": "A 25-minute outage caused by a Cloudflare DNS misconfiguration breached Contoso's 99.9% uptime SLA.",
  "attributes": {{
    "impact": {{"customers": ["Contoso"], "error_rate": 0.04}},
    "time_window": {{"start": "09:10", "end": "09:35", "timezone": "UTC"}},
    "root_cause": "Cloudflare DNS misconfiguration",
    "sla_impact": {{"breached": true, "contract_uptime": 99.9, "credit_owed": true}}
  }},
  "actor_label": null,
  "evidence_quote": "Window: 09:10-09:35 UTC, 25 minutes. 4% of requests failed. Root cause was a Cloudflare DNS misconfiguration.",
  "confidence": 0.9,
  "corrects": false,
  "corrects_hint": null,
  "speculative": false
}}

The nested incident slots (`impact`, `time_window`, `sla_impact`) are objects and must be
filled as objects whenever the document states those facts — never left null because the
value is structured.

Note every concrete fact in the quote (OIDC, Azure AD, March 3rd, 200, Enterprise) has a
slot filled with that exact value — none of it was left to `content` alone, and the bare
"March 3rd" was resolved to the full ISO date "2026-03-03" using the reference date.

Other rules:

1. `evidence_quote` MUST be an exact, verbatim substring of the provided text — copy the
   characters exactly, including punctuation and capitalization. It is validated as an exact
   substring and the object is discarded if it doesn't match.
2. `content` is a short, normalized one-sentence paraphrase; it never replaces
   `evidence_quote` as the source of truth, and it never replaces `attributes` either.
3. `subject_capability` must be one of this controlled vocabulary when the text matches one:
   {CAPABILITY_VOCAB}. SAML, OIDC, and Okta all refine `sso` — use `subject_capability=sso`
   and put the specific protocol/IdP in the `protocol`/`idp` slots, never treat "SAML" as an
   unrelated capability from "SSO". Only propose a new slug if nothing in the vocabulary fits.
   An outage, downtime, or postmortem is `incident` — including its impact, root cause, SLA
   breach and remediation, which all belong to that one incident's items. `uptime_sla` is only
   for the contractual uptime guarantee itself, never for a specific outage event.
4. `entity_hint` is the customer/company name the statement is about (e.g. "Acme", "Globex"),
   taken from the text or from context in the document (title, participants).
5. `corrects` is true when the text explicitly signals it is replacing an earlier statement
   ("update:", "correction", "actually", "rolled back", "back in scope", "supersedes", or a
   reply that contradicts an earlier one). Set `corrects_hint` to a quote of what's being
   corrected. If you are not confident it's a correction, leave `corrects=false`.
6. Call transcripts are speaker-labelled (e.g. "CUSTOMER:", "SALES:"). Copy the speaker label
   verbatim into `actor_label` for each statement you extract from a labelled line. For other
   source kinds, `actor_label` is a free-text description of who's speaking.
7. Call-transcript rules: a CUSTOMER statement of need is a `requirement`. A SALES promise
   ("we can have X by Y") is a `commitment`. A SALES hedged proposal or speculation ("you'd
   probably want X", "we could offer X") should be extracted with `speculative=true` — set
   this whenever the statement is a suggestion/guess rather than something the speaker is
   confident and committed to. A sales commitment must never be extracted as if it were a
   customer requirement.
8. `confidence` (0-1) is how sure you are the text actually says this — not how important it
   is.

Return every distinct item as one entry in `items`. Do not invent facts not present in the
text."""


def build_extraction_prompt(
    source_kind: str,
    stage: str | None,
    reference_date: date | datetime,
    document_context: str | None = None,
) -> str:
    context_line = (
        f"\n\nThis source is a `{source_kind}` document, stage `{stage}`. "
        f"Reference date for year inference (see year-inference rule above): "
        f"{reference_date.date().isoformat() if isinstance(reference_date, datetime) else reference_date.isoformat()}."
    )
    if document_context:
        # A drive section or thread reply is extracted on its own, so without the parent
        # document's title it cannot tell which capability the section belongs to (a bare
        # "Remediation" section reads as generic work, not as part of an incident).
        context_line += (
            f"\nThis text is one part of: {document_context}. Use that for capability and "
            f"entity inference only — `evidence_quote` must still be an exact substring of "
            f"the text below, never of this line."
        )
    return SYSTEM_PROMPT + context_line
