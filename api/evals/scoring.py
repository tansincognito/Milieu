"""Shared gap precision/recall scoring (§19.2: "Gap precision/recall per outcome") for the
degradation set. Used by both the engine (`evals/handoff_eval.py`, the real §10 validator
running on real ingested `context_objects`) and the retrieval baseline
(`evals/retrieval_baseline.py`, §19.3), so the two are scored with the exact same rubric —
required by the dispatch: "score its output on the degradation set with the SAME rubric as
the engine."

A `GoldCase` is one pinned row from the §19.1 golden tables (G1-G6, O3-O6, I2-I6), read
straight out of `evals/cases/degradation.yaml` so there is exactly one place these are
defined. A `Prediction` is one thing either scorer produced (a `context_gaps` row for the
engine, a `BaselineFinding` for the baseline) normalized to the same shape.

Scoring is per-gold-case, not a global sweep of every gap the engine/baseline ever
produces: each of the 15 pinned (subject, slot, handoff) triples is matched against the
best candidate prediction for that exact triple, and the comparison is "did the outcome
match". That means the precision/recall reported here measure classification accuracy on
the fifteen already-known-lossy triples the golden tables pin -- not a global false-positive
audit across every field the validator (or baseline) touches. This is what §19.2 asks the
degradation set to measure; it is a narrower claim than "the engine never hallucinates a
gap anywhere", and the report says so explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The 5 "loss" outcomes the degradation set's golden rows use. `preserved`/`equivalent`
# never appear as an *expected* outcome in a degradation case (there'd be no gap to grade),
# so they're intentionally absent from this list, matching the seven §10.1 outcomes minus
# the two "no gap" ones.
SCORED_OUTCOMES: tuple[str, ...] = (
    "missing",
    "contradicted",
    "generalized",
    "stale_reference",
    "object_missing",
)

NO_MATCH = "no_match"  # sentinel actual-outcome when nothing matched the gold triple at all


@dataclass(frozen=True)
class GoldCase:
    """One pinned §19.1 golden-table row, as declared in `evals/cases/degradation.yaml`."""

    id: str
    scenario: str
    contract_id: str
    entity_slug: str
    subject_match: str  # substring to look for in a prediction's subject/subject_key
    slot: str | None
    outcome: str


@dataclass(frozen=True)
class Prediction:
    """One normalized output from a scorer (an engine `context_gaps` row, or one baseline
    `BaselineFinding`). `entity_slug=None` means the producer didn't/couldn't attribute an
    entity (the baseline, unlike the engine, does no entity resolution -- see
    `retrieval_baseline.py`) -- such a prediction is still eligible to match any gold case
    on subject/slot/outcome alone, which is deliberately generous to the baseline."""

    contract_id: str
    entity_slug: str | None
    subject: str
    slot: str | None
    outcome: str
    source: str = ""  # free-text provenance for the printed detail line (gap id, etc.)


def _slot_matches(gold_slot: str | None, pred_slot: str | None) -> bool:
    if gold_slot is None:
        # object_missing gold cases pin slot=None (the whole downstream object is gone, not
        # one slot on it) -- only match predictions that likewise have no slot.
        return pred_slot is None
    return pred_slot == gold_slot


def match_gold_case(gold: GoldCase, predictions: list[Prediction]) -> Prediction | None:
    """Finds the prediction (if any) for the same (contract, entity, subject, slot) triple
    as `gold`. When several match (shouldn't normally happen — one triple, one gap), prefers
    one whose outcome equals the gold outcome so a correct classification isn't shadowed by
    a coincidental duplicate."""
    candidates = [
        p
        for p in predictions
        if p.contract_id == gold.contract_id
        and (p.entity_slug is None or p.entity_slug == gold.entity_slug)
        and gold.subject_match.lower() in p.subject.lower()
        and _slot_matches(gold.slot, p.slot)
    ]
    if not candidates:
        return None
    for c in candidates:
        if c.outcome == gold.outcome:
            return c
    return candidates[0]


@dataclass
class OutcomeScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        denom = self.tp + self.fp
        return None if denom == 0 else self.tp / denom

    @property
    def recall(self) -> float | None:
        denom = self.tp + self.fn
        return None if denom == 0 else self.tp / denom


@dataclass
class CaseOutcome:
    gold: GoldCase
    actual_outcome: str  # one of SCORED_OUTCOMES or NO_MATCH
    matched_prediction: Prediction | None


@dataclass
class ScoreReport:
    label: str  # "engine" or "baseline", for the printed table header
    per_outcome: dict[str, OutcomeScore] = field(default_factory=dict)
    cases: list[CaseOutcome] = field(default_factory=list)

    @property
    def micro(self) -> OutcomeScore:
        total = OutcomeScore()
        for s in self.per_outcome.values():
            total.tp += s.tp
            total.fp += s.fp
            total.fn += s.fn
        return total

    @property
    def fallback_contradicted_surprises(self) -> list[CaseOutcome]:
        """Cases where the actual outcome was `contradicted` but the gold outcome was
        something else -- the exact failure mode the dispatch's CRITICAL CONTEXT flags as
        the validator's biggest precision risk (an unclassifiable slot pair falling back to
        `contradicted` by default). Surfaced separately so the report can't bury it inside
        an aggregate number."""
        return [
            c
            for c in self.cases
            if c.actual_outcome == "contradicted" and c.gold.outcome != "contradicted"
        ]


def score(label: str, gold_cases: list[GoldCase], predictions: list[Prediction]) -> ScoreReport:
    per_outcome: dict[str, OutcomeScore] = {o: OutcomeScore() for o in SCORED_OUTCOMES}
    cases: list[CaseOutcome] = []
    for gold in gold_cases:
        pred = match_gold_case(gold, predictions)
        actual = pred.outcome if pred is not None else NO_MATCH
        cases.append(CaseOutcome(gold=gold, actual_outcome=actual, matched_prediction=pred))
        if actual == gold.outcome:
            per_outcome[gold.outcome].tp += 1
        else:
            per_outcome[gold.outcome].fn += 1
            if actual in per_outcome:
                per_outcome[actual].fp += 1
    return ScoreReport(label=label, per_outcome=per_outcome, cases=cases)


def format_score_table(report: ScoreReport) -> str:
    lines: list[str] = []
    lines.append(f"\n{report.label.upper()} — gap precision/recall per outcome (§19.2)")
    lines.append("-" * 72)
    lines.append(f"{'OUTCOME':<18}{'TP':>4}{'FP':>4}{'FN':>4}{'PRECISION':>12}{'RECALL':>10}")
    lines.append("-" * 72)

    def _fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.2f}"

    for outcome in SCORED_OUTCOMES:
        s = report.per_outcome[outcome]
        if s.tp == 0 and s.fp == 0 and s.fn == 0:
            continue
        lines.append(
            f"{outcome:<18}{s.tp:>4}{s.fp:>4}{s.fn:>4}{_fmt(s.precision):>12}{_fmt(s.recall):>10}"
        )
    lines.append("-" * 72)
    micro = report.micro
    lines.append(
        f"{'MICRO (all)':<18}{micro.tp:>4}{micro.fp:>4}{micro.fn:>4}"
        f"{_fmt(micro.precision):>12}{_fmt(micro.recall):>10}"
    )

    surprises = report.fallback_contradicted_surprises
    if surprises:
        lines.append("")
        lines.append(
            f"NOTE: {len(surprises)} case(s) landed on `contradicted` where the gold outcome "
            "was something else -- consistent with the deterministic classifier's unclassified-"
            "pair fallback (see app/pipeline/handoff.py's module docstring: 'Slot pairs a "
            "deterministic rule can't classify fall back to `contradicted`'):"
        )
        for c in surprises:
            lines.append(f"  - {c.gold.id}: expected {c.gold.outcome!r}, got `contradicted`")

    unmatched = [c for c in report.cases if c.actual_outcome == NO_MATCH]
    if unmatched:
        lines.append("")
        lines.append(f"NOTE: {len(unmatched)} gold case(s) had no matching prediction at all:")
        for c in unmatched:
            lines.append(f"  - {c.gold.id} (expected {c.gold.outcome!r})")

    return "\n".join(lines)
