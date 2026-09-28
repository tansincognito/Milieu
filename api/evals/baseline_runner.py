"""§19.3 retrieval baseline entry point: `make eval-baseline`, or directly:
    cd api && uv run python -m evals.baseline_runner

Kept as its own entry point, separate from `evals.runner` (`make eval`), so a live
comparison run costs exactly the baseline's own 4 `llm.judge` calls (one per
`evals.retrieval_baseline.BASELINE_CONTRACTS` pair) — it does not require the full ~50-call
extraction pass `make eval` needs, and does not re-ingest (destroying `make eval`'s
`context_objects`/`context_gaps`) if that already ran in the same eval tenant.

Composes with `make eval`:
  - Run `make eval` first, then `make eval-baseline`: prints the engine's and the
    baseline's precision/recall side by side (both read the same eval-tenant DB state).
  - Run `make eval-baseline` alone: ingests sources only (no LLM extraction — see
    `evals.seed.ingest_sources_only`), runs the baseline, and prints the baseline table
    alone with a note that no engine run was found to compare against.
"""

from __future__ import annotations

import sys

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db import engine
from app.core.factories import build_embedding_client, build_llm_client
from app.models.orm import Sources
from evals.handoff_eval import engine_predictions, load_degradation_gold_cases
from evals.retrieval_baseline import run_baseline_eval
from evals.runner import load_cases
from evals.scoring import format_score_table, score
from evals.seed import EVAL_TENANT_ID, ingest_sources_only


def _sources_already_ingested(db: Session) -> bool:
    return db.query(Sources.id).filter(Sources.tenant_id == EVAL_TENANT_ID).first() is not None


def _engine_already_scored(db: Session) -> bool:
    """True if a prior `make eval` run in this same eval tenant left `context_gaps` behind
    for at least one of the four required (entity, contract) pairs — i.e. there's something
    to compare the baseline against without re-running the full ingest."""
    preds = engine_predictions(db, EVAL_TENANT_ID)
    return len(preds) > 0


def main() -> int:
    settings = get_settings()
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = session_factory()
    try:
        if _sources_already_ingested(db):
            print(
                "sources already ingested for the eval tenant -- reusing them (not "
                "re-ingesting, so any prior `make eval` context_objects/context_gaps survive)"
            )
            had_engine_data = _engine_already_scored(db)
        else:
            print("no sources ingested yet -- ingesting mock-data sources (no LLM extraction)...")
            db.close()
            ingest_sources_only(settings=settings)
            db = session_factory()
            had_engine_data = False

        cases = load_cases()
        gold_cases = load_degradation_gold_cases(cases)
        if not gold_cases:
            print("RESULT: BLOCKED -- no degradation gold cases found (evals/cases/degradation.yaml)")
            return 1

        print(f"eval harness: model={settings.llm_model}")
        print(
            f"running retrieval baseline: {len(gold_cases)} gold case(s), "
            "4 live LLM judge call(s) (one per contract)..."
        )
        llm = build_llm_client(settings)
        embedder = build_embedding_client(settings)

        try:
            baseline_preds = run_baseline_eval(db, EVAL_TENANT_ID, llm, embedder)
        except Exception as exc:  # noqa: BLE001 - reported as BLOCKED, never fabricated
            print(f"RESULT: BLOCKED -- baseline LLM call failed: {type(exc).__name__}: {exc}")
            print(
                "No baseline numbers were produced. This is expected if the OpenRouter free "
                "tier is rate-limited (429) or another process is holding the key -- see the "
                "dispatch's CRITICAL CONTEXT. Deterministic proof of the baseline's retrieval/"
                "prompting/scoring structure lives in "
                "tests/unit/test_retrieval_baseline.py, run via `make check` (no live LLM)."
            )
            return 1

        baseline_report = score("baseline", gold_cases, baseline_preds)
        print(format_score_table(baseline_report))

        if had_engine_data:
            engine_preds = engine_predictions(db, EVAL_TENANT_ID)
            engine_report = score("engine", gold_cases, engine_preds)
            print(format_score_table(engine_report))

            engine_precision = engine_report.micro.precision or 0.0
            baseline_precision = baseline_report.micro.precision or 0.0
            engine_recall = engine_report.micro.recall or 0.0
            baseline_recall = baseline_report.micro.recall or 0.0
            beats_on_precision = engine_precision > baseline_precision
            no_recall_loss = engine_recall >= baseline_recall
            print(
                f"\nMVP CLAIM (§19.3): engine precision={engine_precision:.2f} vs. baseline "
                f"precision={baseline_precision:.2f} ({'HOLDS' if beats_on_precision else 'DOES NOT HOLD'} "
                "on precision); engine recall="
                f"{engine_recall:.2f} vs. baseline recall={baseline_recall:.2f} "
                f"({'no recall lost' if no_recall_loss else 'RECALL LOST'})"
            )
        else:
            print(
                "\nNo engine context_gaps found for this eval tenant -- run `make eval` first "
                "(full ingest + extraction) to get the engine's numbers printed side by side."
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
