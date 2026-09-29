"""Retrieval baseline (§19.3, C10): "The baseline embeds all source chunks, then for each
downstream doc retrieves the top-k upstream chunks and asks the same LLM 'which important
upstream details are missing or changed downstream?'. Its output is scored on the
degradation set with the same rubric. The MVP claim holds if the engine beats the baseline
on gap precision without losing recall."

Deliberately dumber than the engine on purpose -- that's the point of a baseline:
 - No entity resolution: a "chunk" is just one ingested `sources` row's raw text (the same
   per-stage granularity the real connectors already produce -- drive is split per markdown
   section, calls/emails/Slack messages are already atomic, §6.2), for *every* entity in the
   tenant at once. Acme and Globex chunks are never filtered apart before retrieval, so nothing
   stops top-k similarity from pulling in the wrong customer's chunk.
 - No subject_key/slot vocabulary grounding beyond what's given in the prompt as a hint.
 - No deterministic classification (§10.1's vocabulary hierarchy, date-precision order,
   dict sub-key comparison, `context_versions` supersession lookup) -- purely the LLM's own
   judgment over whatever top-k retrieval handed it.

Chunk granularity is per `sources` row (one call per stage-pair contract, §19.3's "for each
downstream doc" is honored per-document at the *retrieval* step -- each downstream chunk
gets its own top-k upstream lookup -- but batched into one LLM call per contract, not one
per document, to keep the live-call budget to 4 calls total given the OpenRouter free-tier
rate-limit constraints noted in the dispatch).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.embedding.base import EmbeddingClient
from app.llm.base import LLMClient
from app.models.orm import Sources
from evals.scoring import Prediction

# Same 5 "loss" outcomes the degradation set's gold rows use (§10.1 minus the two
# no-gap outcomes) -- the baseline is asked to classify into exactly this vocabulary so its
# output is scoreable with `evals.scoring.score`, the same rubric as the engine.
BASELINE_OUTCOMES: tuple[str, ...] = (
    "missing",
    "contradicted",
    "generalized",
    "stale_reference",
    "object_missing",
)

# The slot vocabulary the §10 contracts actually check (see `/contracts/*.yaml`), given to
# the LLM as a hint so its output slot names line up with the engine's for scoring — this is
# the one piece of "help" the baseline gets beyond raw text, matching how a real product
# would prompt a generic LLM-only version of this feature (it would still need to be told
# what fields matter).
KNOWN_SLOTS: tuple[str, ...] = (
    "protocol",
    "idp",
    "due_date",
    "priority",
    "region",
    "quantity",
    "integrations",
    "plan",
    "impact",
    "time_window",
    "root_cause",
    "sla_impact",
    "remediation",
)

KNOWN_ENTITY_SLUGS: tuple[str, ...] = ("acme", "globex")

# (contract_id, from_stage, to_stage) — the same four pairs `evals.handoff_eval.
# REQUIRED_VALIDATIONS` runs the engine against, so gold-case `contract_id` values line up
# for `evals.scoring.score` regardless of which side (engine or baseline) produced the
# prediction.
BASELINE_CONTRACTS: tuple[tuple[str, str, str], ...] = (
    ("sales_to_product", "sales", "product"),
    ("product_to_engineering", "product", "engineering"),
    ("sales_to_customer_success", "sales", "customer_success"),
    ("engineering_to_customer_facing::sales", "engineering", "sales"),
)

_CHUNK_CHAR_LIMIT = 700  # keeps the prompt bounded; mock-data chunks are already short


@dataclass(frozen=True)
class Chunk:
    source_id: uuid.UUID
    kind: str
    stage: str | None
    external_id: str
    text: str

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.external_id}"


def load_chunks(db: Session, tenant_id: uuid.UUID, stage: str) -> list[Chunk]:
    """§19.3 "embeds all source chunks" — every ingested `sources` row for `stage`,
    regardless of entity. Order is deterministic (by id) so a repeated run over the same
    ingested data retrieves the same top-k every time."""
    rows = (
        db.query(Sources)
        .filter(Sources.tenant_id == tenant_id, Sources.stage == stage)
        .order_by(Sources.id)
        .all()
    )
    return [Chunk(r.id, r.kind, r.stage, r.external_id, r.text or "") for r in rows]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def embed_chunks(embedder: EmbeddingClient, chunks: list[Chunk]) -> dict[uuid.UUID, list[float]]:
    if not chunks:
        return {}
    vectors = embedder.embed([c.text for c in chunks])
    return dict(zip((c.source_id for c in chunks), vectors, strict=True))


def top_k_upstream(
    downstream_vector: list[float],
    upstream_chunks: list[Chunk],
    upstream_vectors: dict[uuid.UUID, list[float]],
    k: int = 4,
) -> list[Chunk]:
    """§19.3's retrieval step: rank every upstream chunk against one downstream chunk's
    embedding and keep the top `k`. Pure function of its inputs (no I/O) so it's directly
    unit-testable without an embedder or DB — see `tests/unit/test_retrieval_baseline.py`."""
    scored = sorted(
        upstream_chunks,
        key=lambda c: cosine_similarity(downstream_vector, upstream_vectors[c.source_id]),
        reverse=True,
    )
    return scored[:k]


class BaselineFinding(BaseModel):
    entity_slug: str = "unknown"  # "acme" | "globex" | "unknown" -- no strict enum: an
    # out-of-vocabulary value here is itself a scoreable miss (no entity match), not a
    # schema violation worth failing the whole LLM call over.
    subject: str
    slot: str
    outcome: str
    explanation: str


class BaselineResponse(BaseModel):
    findings: list[BaselineFinding] = Field(default_factory=list)


def build_prompt(pairs: list[tuple[Chunk, list[Chunk]]]) -> str:
    """`pairs` is one (downstream chunk, its retrieved top-k upstream chunks) per downstream
    document — the same shape §19.3 describes ("for each downstream doc retrieve the top-k
    upstream chunks"), assembled into a single prompt covering every downstream document in
    one contract's `to_stage` (see module docstring for why: one LLM call per contract, not
    per document, to bound the live-call budget)."""
    known_subjects_line = (
        "Known company subjects (use one of these for `entity_slug` if it applies, else "
        f'"unknown"): {", ".join(KNOWN_ENTITY_SLUGS)}'
    )
    known_slots_line = (
        "Known slot vocabulary (use one of these for `slot` if it applies, else your own "
        f"short slug): {', '.join(KNOWN_SLOTS)}"
    )
    known_outcomes_line = (
        "Known outcome vocabulary (`outcome` MUST be exactly one of these): "
        f"{', '.join(BASELINE_OUTCOMES)}"
    )
    object_missing_slot_line = (
        'If `outcome` is "object_missing", set `slot` to the literal string "none" (the loss '
        "applies to the whole item, not one slot on it)."
    )
    instructions_line = (
        "Below are DOWNSTREAM documents, each followed by the upstream documents retrieved as "
        "most similar to it. For each downstream document, compare it against ONLY its own "
        "retrieved upstream documents and report every important upstream detail that is "
        "missing, contradicted, generalized, a stale reference, or an upstream item with no "
        "downstream counterpart at all. Do not report anything that is merely reworded with "
        "the same meaning."
    )
    parts: list[str] = [
        "You are auditing a work handoff between two teams for lost or changed information.",
        "",
        known_subjects_line,
        known_slots_line,
        known_outcomes_line,
        "  - missing: the upstream detail is absent downstream",
        "  - contradicted: the downstream value conflicts with the upstream value",
        "  - generalized: the downstream value is a less specific/broader version of the upstream value",
        "  - stale_reference: the downstream value matches an upstream value that a later upstream message corrected/superseded",
        "  - object_missing: the entire upstream item (not just one detail) has no downstream counterpart at all",
        "",
        object_missing_slot_line,
        "",
        instructions_line,
        "",
    ]
    for i, (downstream, upstream) in enumerate(pairs, start=1):
        parts.append(f"=== DOWNSTREAM DOC d{i} ({downstream.label}) ===")
        parts.append(downstream.text[:_CHUNK_CHAR_LIMIT])
        parts.append(f"--- top-{len(upstream)} retrieved upstream docs for d{i} ---")
        for j, up in enumerate(upstream, start=1):
            parts.append(f"[u{i}.{j}] ({up.label}): {up.text[:_CHUNK_CHAR_LIMIT]}")
        parts.append("")
    parts.append(
        "Respond with a `findings` list, one entry per lost/changed/missing detail you found."
    )
    return "\n".join(parts)


def run_baseline_for_contract(
    db: Session,
    tenant_id: uuid.UUID,
    llm: LLMClient,
    embedder: EmbeddingClient,
    *,
    from_stage: str,
    to_stage: str,
    k: int = 4,
) -> BaselineResponse:
    """One live LLM call: retrieves top-k upstream chunks per downstream chunk (real
    embedding similarity, §19.3), then asks `llm.judge` the same "what's missing or
    changed" question the engine's `context_gaps` output answers deterministically."""
    upstream = load_chunks(db, tenant_id, from_stage)
    downstream = load_chunks(db, tenant_id, to_stage)
    if not upstream or not downstream:
        return BaselineResponse(findings=[])

    upstream_vectors = embed_chunks(embedder, upstream)
    downstream_vectors = embed_chunks(embedder, downstream)

    pairs = [
        (d, top_k_upstream(downstream_vectors[d.source_id], upstream, upstream_vectors, k=k))
        for d in downstream
    ]
    prompt = build_prompt(pairs)
    return llm.judge(BaselineResponse, prompt)


def run_baseline_eval(
    db: Session, tenant_id: uuid.UUID, llm: LLMClient, embedder: EmbeddingClient, *, k: int = 4
) -> list[Prediction]:
    """Runs `run_baseline_for_contract` for every `BASELINE_CONTRACTS` pair and normalizes
    the results into `Prediction`s, scoreable by `evals.scoring.score` against the same gold
    cases the engine is scored against."""
    predictions: list[Prediction] = []
    for contract_id, from_stage, to_stage in BASELINE_CONTRACTS:
        response = run_baseline_for_contract(
            db, tenant_id, llm, embedder, from_stage=from_stage, to_stage=to_stage, k=k
        )
        for finding in response.findings:
            slot = finding.slot.strip().lower()
            normalized_slot = None if slot in ("none", "null", "n/a", "") else finding.slot
            predictions.append(
                Prediction(
                    contract_id=contract_id,
                    entity_slug=finding.entity_slug if finding.entity_slug in KNOWN_ENTITY_SLUGS else None,
                    subject=finding.subject,
                    slot=normalized_slot,
                    outcome=finding.outcome,
                    source=f"baseline:{contract_id}",
                )
            )
    return predictions
