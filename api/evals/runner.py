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
        f"-> {report.total_objects} context objects created"
    )
    if report.infra_failures:
        print(f"WARNING: {len(report.infra_failures)} source(s) hit infra failures (see above):")
        for s in report.infra_failures:
            print(f"  - {s.kind}:{s.external_id}: {s.error}")
    if report.extraction_failures:
        print(f"WARNING: {len(report.extraction_failures)} source(s) hit extraction failures:")
        for s in report.extraction_failures:
            print(f"  - {s.kind}:{s.external_id}: {s.error}")

    cases = load_cases()
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = session_factory()
    try:
        results = [run_case(db, report.tenant_id, case) for case in cases]
    finally:
        db.close()

    _print_table(results)

    any_infra_issue = bool(report.infra_failures)
    any_real_failure = any(not r.passed for r in results)

    if any_infra_issue:
        print(
            f"NOTE: {len(report.infra_failures)} source(s) never finished extraction due to "
            "infra failures (OpenRouter rate limits/timeouts), not engine bugs. Any case "
            "failures above whose [SELECTOR] detail names one of those sources may be a "
            "downstream consequence of that infra gap, not a pipeline defect."
        )

    if any_real_failure:
        print(f"RESULT: {sum(1 for r in results if not r.passed)} case(s) failed.")
        return 1

    print("RESULT: all eval cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
