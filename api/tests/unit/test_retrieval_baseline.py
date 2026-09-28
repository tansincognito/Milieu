"""Unit tests (no DB, no live LLM) for the pure retrieval/prompting/normalization logic in
`evals/retrieval_baseline.py` (§19.3). `evals.retrieval_baseline.Chunk` is constructed
directly here rather than via `load_chunks`, so these run with zero infrastructure —
proof the retrieval math and prompt assembly are correct independent of whether a live
Postgres/LLM/embedder is available. The DB- and LLM-touching half (`run_baseline_for_contract`
/`run_baseline_eval`, with a stubbed `LLMClient`) is proven in
`tests/integration/test_retrieval_baseline.py`, matching the split the rest of the codebase
already uses (e.g. `tests/unit/test_evidence_span.py` vs.
`tests/integration/test_extraction_pipeline.py`).
"""

from __future__ import annotations

import uuid

import pytest

from evals.retrieval_baseline import (
    BASELINE_CONTRACTS,
    BASELINE_OUTCOMES,
    Chunk,
    build_prompt,
    cosine_similarity,
    top_k_upstream,
)


def _chunk(kind: str, stage: str, external_id: str, text: str) -> Chunk:
    return Chunk(uuid.uuid4(), kind, stage, external_id, text)


def test_cosine_similarity_identical_vectors_is_one() -> None:
    v = [0.1, 0.2, 0.3, 0.4]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_similarity_zero_vector_does_not_divide_by_zero() -> None:
    # A zero vector has no direction -- similarity is defined as 0, not a ZeroDivisionError.
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0


def test_cosine_similarity_opposite_vectors_is_negative_one() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_top_k_upstream_ranks_by_similarity_descending() -> None:
    close = _chunk("drive", "sales", "close.md#0", "close")
    mid = _chunk("drive", "sales", "mid.md#0", "mid")
    far = _chunk("drive", "sales", "far.md#0", "far")
    downstream_vector = [1.0, 0.0]
    vectors = {
        close.source_id: [0.99, 0.01],
        mid.source_id: [0.5, 0.5],
        far.source_id: [-1.0, 0.0],
    }
    ranked = top_k_upstream(downstream_vector, [far, mid, close], vectors, k=2)
    assert [c.source_id for c in ranked] == [close.source_id, mid.source_id]


def test_top_k_upstream_respects_k() -> None:
    chunks = [_chunk("drive", "sales", f"c{i}.md#0", f"chunk {i}") for i in range(10)]
    vectors = {c.source_id: [float(i)] for i, c in enumerate(chunks)}
    ranked = top_k_upstream([9.0], chunks, vectors, k=3)
    assert len(ranked) == 3


def test_top_k_upstream_empty_upstream_returns_empty() -> None:
    assert top_k_upstream([1.0, 0.0], [], {}, k=4) == []


def test_build_prompt_includes_every_downstream_doc_and_its_own_upstream_matches() -> None:
    downstream_a = _chunk("drive", "product", "a.md#0", "Support SSO, target December")
    downstream_b = _chunk("drive", "product", "b.md#0", "SCIM deferred to Q1")
    upstream_for_a = _chunk("call", "sales", "call-1", "We need SAML through Okta by Dec 15")
    upstream_for_b = _chunk("email", "sales", "email-1", "Customer confirms SCIM required")

    prompt = build_prompt([(downstream_a, [upstream_for_a]), (downstream_b, [upstream_for_b])])

    assert "Support SSO, target December" in prompt
    assert "SCIM deferred to Q1" in prompt
    assert "We need SAML through Okta by Dec 15" in prompt
    assert "Customer confirms SCIM required" in prompt
    # Each downstream doc's own label appears, so a human/LLM can tell d1 from d2.
    assert downstream_a.label in prompt
    assert downstream_b.label in prompt


def test_build_prompt_names_the_full_outcome_vocabulary() -> None:
    prompt = build_prompt([])
    for outcome in BASELINE_OUTCOMES:
        assert outcome in prompt


def test_baseline_contracts_reuse_the_same_ids_as_the_engine_requires() -> None:
    """§19.3: the baseline must be scored on the *same* gold cases as the engine
    (`evals.handoff_eval.REQUIRED_VALIDATIONS`) -- their `contract_id`s must line up 1:1 or
    `evals.scoring.score` can never match a baseline prediction to a gold case."""
    from evals.handoff_eval import REQUIRED_VALIDATIONS

    engine_contract_ids = {contract_id for _, contract_id in REQUIRED_VALIDATIONS}
    baseline_contract_ids = {contract_id for contract_id, _, _ in BASELINE_CONTRACTS}
    assert engine_contract_ids == baseline_contract_ids
