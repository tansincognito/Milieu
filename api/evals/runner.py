"""Eval harness runner (§19). Loads every case file under `evals/cases/`, runs one real,
deterministic seeded ingest (§19.2's Day-2 sets: entity, dedup, lifecycle, conflict,
lineage), scores each case against the resulting database state, and prints a per-category
pass/fail table.

Usage: `make eval` (see the Makefile target), or directly:
    cd api && uv run python -m evals.runner
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db import engine
from evals.checks import run_check
from evals.handoff_eval import (
    engine_predictions,
    load_degradation_gold_cases,
    run_required_handoff_validations,
)
from evals.scoring import format_score_table, score
from evals.seed import run_seeded_ingest

CASES_DIR = Path(__file__).resolve().parent / "cases"


@dataclass
class CaseResult:
    case_id: str
    category: str
    description: str
    passed: bool
    details: list[str]


def load_cases(cases_dir: Path = CASES_DIR) -> list[dict]:
    cases: list[dict] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for case in data.get("cases", []):
            case["_file"] = path.name
            cases.append(case)
    return cases


def run_case(db: Session, tenant_id: uuid.UUID, case: dict) -> CaseResult:
    details: list[str] = []
    passed = True
    for check in case["checks"]:
        result = run_check(db, tenant_id, check["kind"], check.get("params", {}))
        marker = "ok" if result.passed else "FAIL"
        details.append(f"    [{marker}] {check['kind']}: {result.detail}")
        passed = passed and result.passed
    return CaseResult(
        case_id=case["id"],
        category=case["category"],
        description=case.get("description", ""),
        passed=passed,
        details=details,
    )


def _print_table(results: list[CaseResult]) -> None:
    categories: dict[str, list[CaseResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    print()
    print("=" * 78)
    print("EVAL RESULTS")
    print("=" * 78)
    header = f"{'CATEGORY':<14} {'CASE':<28} {'RESULT':<8} DESCRIPTION"
    print(header)
    print("-" * 78)
    for category in sorted(categories):
        for r in categories[category]:
            status = "PASS" if r.passed else "FAIL"
            print(f"{category:<14} {r.case_id:<28} {status:<8} {r.description}")
            if not r.passed:
                for line in r.details:
                    print(line)
    print("-" * 78)

    print()
    print(f"{'CATEGORY':<14} {'PASS':>5} {'TOTAL':>6}")
    print("-" * 28)
    total_pass = 0
    total_count = 0
    for category in sorted(categories):
        cat_results = categories[category]
        cat_pass = sum(1 for r in cat_results if r.passed)
        total_pass += cat_pass
        total_count += len(cat_results)
        print(f"{category:<14} {cat_pass:>5} {len(cat_results):>6}")
    print("-" * 28)
    print(f"{'TOTAL':<14} {total_pass:>5} {total_count:>6}")
    print()


def main() -> int:
    settings = get_settings()
    print(f"eval harness: model={settings.llm_model}")
    print("running seeded ingest (real pipeline, real LLM, real embedder)...")
    start = time.monotonic()
    report = run_seeded_ingest(settings=settings)
    elapsed = time.monotonic() - start
    print(
        f"ingest done in {elapsed:0.1f}s: {report.load_counts} "
        f"-> {report.total_objects} context objects created, "
        f"{report.total_rejected} rejected (evidence-span)"
    )
    sources_with_rejections = [s for s in report.sources if s.objects_rejected > 0]
    if sources_with_rejections:
        print(
            f"NOTE: {len(sources_with_rejections)} source(s) had extracted item(s) rejected "
            "on the §4.3 evidence-span check (see per-source warnings above):"
        )
        for s in sources_with_rejections:
            print(f"  - {s.kind}:{s.external_id}: {s.objects_rejected} rejected")
    if report.infra_failures:
        print(f"WARNING: {len(report.infra_failures)} source(s) hit infra failures (see above):")
        for s in report.infra_failures:
            print(f"  - {s.kind}:{s.external_id}: {s.error}")
    if report.extraction_failures:
        print(f"WARNING: {len(report.extraction_failures)} source(s) hit extraction failures:")
        for s in report.extraction_failures:
            print(f"  - {s.kind}:{s.external_id}: {s.error}")
    if report.harness_errors:
        print(f"WARNING: {len(report.harness_errors)} source(s) hit a harness (not pipeline) bug:")
        for s in report.harness_errors:
            print(f"  - {s.kind}:{s.external_id}: {s.error}")

    cases = load_cases()
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = session_factory()
    try:
        # §10/§19.2: run the real (deterministic, no-LLM) handoff validator against
        # whatever the ingest above actually produced, for the four (entity, contract)
        # pairs the golden tables and the degradation set pin -- before any case (including
        # the `handoff_gap_outcome` degradation cases) reads `context_gaps`.
        handoff_report = run_required_handoff_validations(db, report.tenant_id)
        if handoff_report.errors:
            print(f"WARNING: {len(handoff_report.errors)} handoff validation(s) could not run:")
            for err in handoff_report.errors:
                print(f"  - {err}")

        results = [run_case(db, report.tenant_id, case) for case in cases]

        # §19.2: aggregate gap precision/recall for the engine over the same 15 pinned
        # (subject, slot, handoff) triples the `degradation` cases above already checked
        # pass/fail on -- printed regardless of whether every case passed, since a partial
        # score is itself the useful signal when ingest hit infra failures.
        gold_cases = load_degradation_gold_cases(cases)
        if gold_cases:
            preds = engine_predictions(db, report.tenant_id)
            engine_report = score("engine", gold_cases, preds)
            print(format_score_table(engine_report))
    finally:
        db.close()

    _print_table(results)

    any_infra_issue = bool(report.infra_failures) or bool(report.harness_errors)
    any_real_failure = any(not r.passed for r in results)

    if any_infra_issue:
        print(
            f"NOTE: {len(report.infra_failures)} source(s) never finished extraction due to "
            f"infra failures (OpenRouter rate limits/timeouts) and {len(report.harness_errors)} "
            "hit a harness-side bug -- neither is an engine bug. Any case failure above whose "
            "[SELECTOR] detail names one of those sources may be a downstream consequence of "
            "that gap, not a pipeline defect."
        )

    if any_real_failure:
        print(f"RESULT: {sum(1 for r in results if not r.passed)} case(s) failed.")
        return 1

    print("RESULT: all eval cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
