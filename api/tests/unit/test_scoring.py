"""Unit tests (no DB, no LLM) for `evals/scoring.py` — the shared gap precision/recall
rubric §19.2 requires the engine and the §19.3 retrieval baseline to be scored with,
identically. Exercises `match_gold_case` and `score` directly against hand-built
`GoldCase`/`Prediction` values, independent of where those would really come from
(`evals.handoff_eval.engine_predictions` or `evals.retrieval_baseline.run_baseline_eval`).
"""

from __future__ import annotations

from evals.scoring import GoldCase, Prediction, match_gold_case, score


def _gold(
    id_: str,
    *,
    contract_id: str = "sales_to_product",
    entity_slug: str = "acme",
    subject_match: str = "sso",
    slot: str | None = "protocol",
    outcome: str = "generalized",
) -> GoldCase:
    return GoldCase(
        id=id_,
        scenario="golden1",
        contract_id=contract_id,
        entity_slug=entity_slug,
        subject_match=subject_match,
        slot=slot,
        outcome=outcome,
    )


def _pred(
    *,
    contract_id: str = "sales_to_product",
    entity_slug: str | None = "acme",
    subject: str = "acme:sso",
    slot: str | None = "protocol",
    outcome: str = "generalized",
) -> Prediction:
    return Prediction(
        contract_id=contract_id, entity_slug=entity_slug, subject=subject, slot=slot, outcome=outcome
    )


def test_match_gold_case_finds_exact_match() -> None:
    gold = _gold("g1")
    pred = _pred()
    assert match_gold_case(gold, [pred]) is pred


def test_match_gold_case_returns_none_when_contract_differs() -> None:
    gold = _gold("g1", contract_id="sales_to_product")
    pred = _pred(contract_id="product_to_engineering")
    assert match_gold_case(gold, [pred]) is None


def test_match_gold_case_returns_none_when_slot_differs() -> None:
    gold = _gold("g1", slot="protocol")
    pred = _pred(slot="idp")
    assert match_gold_case(gold, [pred]) is None


def test_match_gold_case_none_slot_only_matches_none_slot_prediction() -> None:
    """object_missing gold cases pin slot=None (§10.1: no slot on a whole-object gap) --
    a prediction that names some other slot must not count as a match just because the
    subject/contract/entity all line up."""
    gold = _gold("g1", slot=None, outcome="object_missing")
    wrong = _pred(slot="protocol", outcome="object_missing")
    right = _pred(slot=None, outcome="object_missing")
    assert match_gold_case(gold, [wrong]) is None
    assert match_gold_case(gold, [right]) is right


def test_match_gold_case_entity_slug_none_is_a_wildcard() -> None:
    """The baseline does no entity resolution (§19.3) -- a prediction with
    `entity_slug=None` must still be eligible to match, generously, on subject/slot/outcome
    alone."""
    gold = _gold("g1", entity_slug="globex", subject_match="data_residency")
    pred = _pred(entity_slug=None, subject="globex:data_residency", slot="protocol")
    assert match_gold_case(gold, [pred]) is pred


def test_match_gold_case_prefers_outcome_match_among_several_candidates() -> None:
    gold = _gold("g1", outcome="missing")
    wrong_outcome = _pred(outcome="contradicted")
    right_outcome = _pred(outcome="missing")
    assert match_gold_case(gold, [wrong_outcome, right_outcome]) is right_outcome


def test_score_perfect_match_yields_full_precision_and_recall() -> None:
    gold = [_gold("g1", outcome="missing", slot="idp"), _gold("g2", outcome="generalized", slot="protocol")]
    preds = [_pred(outcome="missing", slot="idp"), _pred(outcome="generalized", slot="protocol")]
    report = score("engine", gold, preds)
    assert report.per_outcome["missing"].tp == 1
    assert report.per_outcome["missing"].precision == 1.0
    assert report.per_outcome["missing"].recall == 1.0
    assert report.per_outcome["generalized"].precision == 1.0
    assert report.micro.precision == 1.0
    assert report.micro.recall == 1.0


def test_score_no_match_counts_as_false_negative_only() -> None:
    gold = [_gold("g1", outcome="missing", slot="idp")]
    report = score("engine", gold, predictions=[])
    assert report.per_outcome["missing"].tp == 0
    assert report.per_outcome["missing"].fn == 1
    assert report.per_outcome["missing"].fp == 0
    assert report.per_outcome["missing"].precision is None  # 0/0 -- undefined, not 0.0
    assert report.per_outcome["missing"].recall == 0.0


def test_score_wrong_outcome_counts_as_fn_for_gold_and_fp_for_predicted() -> None:
    """Engine fallback risk (dispatch's CRITICAL CONTEXT): a slot pair the deterministic
    classifier can't handle falls back to `contradicted` -- if gold expected `generalized`
    but the engine produced `contradicted` for that same triple, that's one missed
    `generalized` (FN) and one spurious `contradicted` (FP), which is exactly what tanks
    `contradicted`'s precision without gold ever containing a `contradicted` row."""
    gold = [_gold("g1", outcome="generalized", slot="protocol")]
    preds = [_pred(outcome="contradicted", slot="protocol")]
    report = score("engine", gold, preds)
    assert report.per_outcome["generalized"].fn == 1
    assert report.per_outcome["generalized"].tp == 0
    assert report.per_outcome["contradicted"].fp == 1
    assert report.fallback_contradicted_surprises[0].gold.id == "g1"


def test_score_micro_aggregates_across_outcomes() -> None:
    gold = [
        _gold("g1", outcome="missing", slot="idp"),
        _gold("g2", outcome="contradicted", slot="region", subject_match="data_residency"),
    ]
    preds = [
        _pred(outcome="missing", slot="idp"),
        _pred(outcome="missing", slot="region", subject="data_residency"),  # wrong outcome
    ]
    report = score("engine", gold, preds)
    micro = report.micro
    assert micro.tp == 1
    assert micro.fn == 1  # the contradicted gold case, missed
    assert micro.fp == 1  # the spurious `missing` on the region slot
