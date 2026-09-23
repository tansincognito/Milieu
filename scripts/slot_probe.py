"""One-off probe: model x prompt-variant -> slot fill rate, on the fixtures named in the
"empty attributes" bug dispatch. Not wired to CI, not a permanent benchmark harness —
kept only to document how the winning (model, prompt) pair was chosen. Requires
OPENROUTER_API_KEY (reads api/.env).

Usage:
    cd /Users/ajaymac/Projects/Milieu
    api/.venv/bin/python scripts/slot_probe.py --fixture acme --models m1,m2 --variants v1,v2
    api/.venv/bin/python scripts/slot_probe.py --fixture acme --models qwen/qwen3.8-27b:free --variants v2 --runs 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "api"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / "api" / ".env")

from datetime import date  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.llm.openrouter import OpenRouterLLMClient  # noqa: E402
from app.pipeline.prompts import CAPABILITY_VOCAB  # noqa: E402
from app.pipeline.prompts import build_extraction_prompt as build_prompt_v2  # noqa: E402
from app.schemas.extraction import ExtractionResult  # noqa: E402

# ---------------------------------------------------------------------------
# Prompt variants. "v1" is a frozen snapshot of the pre-fix prompt (kept only so this
# probe can still demonstrate the regression); "v2" imports the live, shipped prompt from
# app.pipeline.prompts so the probe stays honest against whatever is actually deployed.
# ---------------------------------------------------------------------------

V1_BASE = f"""You extract structured Context Objects from a single source document for
a context-continuity engine. Read the text and return every distinct requirement, decision,
constraint, commitment, problem, open_question, resolution, or dependency it states.

Rules:

1. `evidence_quote` MUST be an exact, verbatim substring of the provided text — copy the
   characters exactly, including punctuation and capitalization. Do not paraphrase it. It
   is validated as an exact substring and the object is discarded if it doesn't match.
2. `content` is a short, normalized one-sentence statement of what was said — this can (and
   should) paraphrase; it never replaces `evidence_quote` as the source of truth.
3. `subject_capability` must be one of this controlled vocabulary when the text matches one:
   {CAPABILITY_VOCAB}. SAML, OIDC, and Okta all refine `sso` — use `subject_capability=sso`
   and put the specific protocol/IdP in the `protocol`/`idp` slots, never treat "SAML" as an
   unrelated capability from "SSO". Only propose a new slug if nothing in the vocabulary fits.
4. `entity_hint` is the customer/company name the statement is about (e.g. "Acme", "Globex"),
   taken from the text or from context in the document (title, participants).
5. Fill only the slots that are actually present in the text: protocol, idp, due_date (plus
   due_date_precision: day/month/quarter — use the precision actually stated, e.g. "December"
   with no day is due_date_precision=month), priority, stance, acceptance_criteria, rationale,
   region, quantity/quantity_unit, integrations, plan. For incidents also fill: impact
   (customers, error_rate, data_loss), time_window (start, end, timezone), root_cause,
   sla_impact (breached, contract_uptime, credit_owed), remediation. If the text names an
   incident id (e.g. "INC-2311"), put it in attributes.extra.incident_id.
6. `corrects` is true when the text explicitly signals it is replacing an earlier statement
   ("update:", "correction", "actually", "rolled back", "back in scope", "supersedes", or a
   reply that contradicts an earlier one). Set `corrects_hint` to a quote of what's being
   corrected. If you are not confident it's a correction, leave `corrects=false`.
7. Call transcripts are speaker-labelled (e.g. "CUSTOMER:", "SALES:"). Copy the speaker label
   verbatim into `actor_label` for each statement you extract from a labelled line (e.g.
   "CUSTOMER" or "SALES"), so the caller can tell who said it. For other source kinds,
   `actor_label` is a free-text description of who's speaking (e.g. a name from the doc).
8. Call-transcript rules: a CUSTOMER statement of need is a `requirement`. A SALES promise
   ("we can have X by Y") is a `commitment`. A SALES hedged proposal or speculation ("you'd
   probably want X", "we could offer X") should be extracted with `speculative=true` — set
   this whenever the statement is a suggestion/guess rather than something the speaker is
   confident and committed to. A sales commitment must never be extracted as if it were a
   customer requirement.
9. `confidence` (0-1) is how sure you are the text actually says this — not how important it
   is.

Return every distinct item as one entry in `items`. Do not invent facts not present in the
text."""


VARIANTS = {"v1": V1_BASE}


def build_prompt(variant: str, source_kind: str, stage: str | None, reference_date: date) -> str:
    if variant == "v2":
        # Live prompt, as shipped in app.pipeline.prompts (post-fix).
        return build_prompt_v2(source_kind, stage, reference_date)
    context_line = f"\n\nThis source is a `{source_kind}` document, stage `{stage}`."
    return VARIANTS[variant] + context_line


# ---------------------------------------------------------------------------
# Fixtures + expectations
# ---------------------------------------------------------------------------


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _call_body(rel: str) -> str:
    """Strip the '# key: value' header the mock call connector strips before extraction."""
    text = _read(rel)
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    return "".join(lines[i:])


FIXTURES = {
    "acme": {
        "kind": "call",
        "stage": "sales",
        "reference_date": date(2026, 10, 2),
        "text": _call_body("mock-data/calls/acme-discovery-2026-10-02.txt"),
        "match": {"type": "requirement", "subject_capability": "sso"},
        "expect": {
            "protocol": "SAML",
            "idp": "Okta",
            "due_date": "2026-12-15",
            "due_date_precision": "day",
        },
    },
    "globex": {
        "kind": "call",
        "stage": "sales",
        "reference_date": date(2026, 11, 4),
        "text": _call_body("mock-data/calls/globex-closing-2026-11-04.txt"),
        "match": {"type": "requirement", "subject_capability": "data_residency"},
        "expect": {"region": "eu-west-1"},
    },
    "globex_seats": {
        "kind": "call",
        "stage": "sales",
        "reference_date": date(2026, 11, 4),
        "text": _call_body("mock-data/calls/globex-closing-2026-11-04.txt"),
        "match": {"type": "requirement", "subject_capability": "seats"},
        "expect": {"quantity": 500},
    },
    "globex_integration": {
        "kind": "call",
        "stage": "sales",
        "reference_date": date(2026, 11, 4),
        "text": _call_body("mock-data/calls/globex-closing-2026-11-04.txt"),
        "match": {"type": "requirement", "subject_capability": "integration"},
        "expect": {"integrations": ["Salesforce"]},
    },
    "globex_due": {
        "kind": "call",
        "stage": "sales",
        "reference_date": date(2026, 11, 4),
        "text": _call_body("mock-data/calls/globex-closing-2026-11-04.txt"),
        "match": {"type": "requirement", "subject_capability": "onboarding"},
        "expect": {"due_date": "2027-01-10"},
    },
    "postmortem": {
        "kind": "drive",
        "stage": "engineering",
        "reference_date": date(2026, 11, 27),
        "text": _read("mock-data/drive/engineering/postmortem-INC-2311.md"),
        "match": {"type": "resolution", "subject_capability": "incident"},
        "expect": {"time_window": "<non-null>", "root_cause": "<non-null>", "sla_impact": "<non-null>"},
    },
}


def find_best_match(result: ExtractionResult, match: dict) -> object | None:
    candidates = [
        item
        for item in result.items
        if item.subject_capability == match["subject_capability"]
    ]
    typed = [c for c in candidates if c.type == match["type"]]
    return (typed or candidates or [None])[0]


def score(item: object | None, expect: dict) -> tuple[int, int, list[str]]:
    if item is None:
        return 0, len(expect), ["no matching item extracted"]
    attrs = item.attributes
    hits = 0
    details = []
    for slot, want in expect.items():
        got = getattr(attrs, slot, None)
        if want == "<non-null>":
            ok = got is not None
        elif slot == "due_date":
            ok = str(got) == want
        else:
            ok = got == want
        hits += int(ok)
        details.append(f"{slot}={got!r}{'OK' if ok else ' MISS(want ' + repr(want) + ')'}")
    return hits, len(expect), details


def run_one(model: str, variant: str, fixture_key: str, settings) -> dict:
    fx = FIXTURES[fixture_key]
    client = OpenRouterLLMClient(
        api_key=settings.openrouter_api_key,
        model=model,
        app_name=settings.openrouter_app_name,
        site_url=settings.openrouter_site_url,
    )
    system = build_prompt(variant, fx["kind"], fx["stage"], fx["reference_date"])
    result = client.extract(ExtractionResult, system, fx["text"])
    item = find_best_match(result, fx["match"])
    hits, total, details = score(item, fx["expect"])
    if item is None:
        details = details + [
            "extracted: " + ", ".join(f"{i.type}/{i.subject_capability}" for i in result.items)
        ]
    return {
        "model": model,
        "variant": variant,
        "fixture": fixture_key,
        "n_items": len(result.items),
        "hits": hits,
        "total": total,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default="acme", help="comma-separated fixture keys")
    parser.add_argument("--models", required=True, help="comma-separated OpenRouter model ids")
    parser.add_argument("--variants", default="v1,v2", help="comma-separated prompt variant ids")
    parser.add_argument("--runs", type=int, default=1, help="repeat each combo N times")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.openrouter_api_key:
        raise SystemExit("OPENROUTER_API_KEY not set (check api/.env)")

    fixtures = args.fixture.split(",")
    models = args.models.split(",")
    variants = args.variants.split(",")

    rows = []
    for fixture_key in fixtures:
        for model in models:
            for variant in variants:
                for run_i in range(args.runs):
                    try:
                        r = run_one(model, variant, fixture_key, settings)
                    except Exception as e:  # noqa: BLE001 - probe script, report and continue
                        rows.append(
                            {
                                "model": model,
                                "variant": variant,
                                "fixture": fixture_key,
                                "run": run_i + 1,
                                "error": str(e)[:120],
                            }
                        )
                        print(f"[ERROR] {model} {variant} {fixture_key} run{run_i + 1}: {e}")
                        continue
                    r["run"] = run_i + 1
                    rows.append(r)
                    print(
                        f"{model:45s} {variant:4s} {fixture_key:18s} run{run_i + 1} "
                        f"{r['hits']}/{r['total']}  n_items={r['n_items']}"
                    )
                    for d in r["details"]:
                        print(f"    {d}")

    print("\n=== SUMMARY (fill rate) ===")
    header = f"{'model':45s} {'variant':6s} {'fixture':18s} {'fill':>8s}"
    print(header)
    print("-" * len(header))
    seen = set()
    for r in rows:
        key = (r["model"], r["variant"], r["fixture"])
        if key in seen or "error" in r:
            continue
        seen.add(key)
        matching = [x for x in rows if (x["model"], x["variant"], x["fixture"]) == key and "error" not in x]
        total_hits = sum(x["hits"] for x in matching)
        total_total = sum(x["total"] for x in matching)
        rate = f"{total_hits}/{total_total}"
        print(f"{r['model']:45s} {r['variant']:6s} {r['fixture']:18s} {rate:>8s}")


if __name__ == "__main__":
    main()
