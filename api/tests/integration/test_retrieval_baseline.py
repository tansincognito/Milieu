"""Integration tests for the DB- and LLM-touching half of `evals/retrieval_baseline.py`
(§19.3) — `load_chunks` (real Postgres `sources` rows) and `run_baseline_for_contract`/
`run_baseline_eval` (a stubbed `LLMClient`, no live OpenRouter call), following the same
test-double pattern `tests/integration/test_extraction_pipeline.py` already uses for the
extraction pipeline (`CannedLLMClient`/`FakeEmbeddingClient`). Proves the retrieval ->
prompt -> judge -> normalize-to-`Prediction` pipeline end to end without a live model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import engine
from app.models.orm import Sources
from evals.retrieval_baseline import (
    BASELINE_CONTRACTS,
    BaselineFinding,
    BaselineResponse,
    load_chunks,
    run_baseline_eval,
    run_baseline_for_contract,
)

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 2, 0, 0, 0, tzinfo=UTC)


class CannedLLMClient:
    """Test double: always returns the same canned `BaselineResponse`, regardless of
    prompt -- same pattern as `test_extraction_pipeline.CannedLLMClient`."""

    def __init__(self, response: BaselineResponse) -> None:
        self._response = response
        self.calls = 0
        self.last_prompt: str | None = None

    def extract(self, schema, system, content):
        raise NotImplementedError

    def judge(self, schema, prompt):
        self.calls += 1
        self.last_prompt = prompt
        return self._response


class FakeEmbeddingClient:
    dimensions = 384

    def __init__(self) -> None:
        self.embed_calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        # Deterministic, text-dependent (not identical for every text) so top-k retrieval
        # in `run_baseline_for_contract` has something non-degenerate to rank.
        return [[float(len(t) % 7) / 10.0] * 384 for t in texts]


@pytest.fixture
def db_session() -> Session:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    tenant_id = uuid.uuid4()
    yield session, tenant_id  # type: ignore[misc]
    session.rollback()
    session.execute(text("DELETE FROM sources WHERE tenant_id = :t"), {"t": tenant_id})
    session.commit()
    session.close()


def _make_source(db: Session, tenant_id: uuid.UUID, stage: str, kind: str, text_body: str) -> uuid.UUID:
    source = Sources(
        id=uuid.uuid4(), tenant_id=tenant_id, kind=kind, external_id=str(uuid.uuid4()),
        content_hash=uuid.uuid4().hex, stage=stage, acl=["*"], provenance={"kind": kind},
        text=text_body, source_ts=T0,
    )
    db.add(source)
    db.flush()
    return source.id


def test_load_chunks_reads_every_source_row_for_a_stage_across_entities(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    """§19.3: the baseline embeds *all* source chunks for a stage, with no entity
    filtering -- unlike the engine, which always scopes by `entity_id` (§10.1)."""
    db, tenant_id = db_session
    _make_source(db, tenant_id, "sales", "call", "Acme wants SAML through Okta")
    _make_source(db, tenant_id, "sales", "call", "Globex wants EU data residency")
    _make_source(db, tenant_id, "product", "drive", "Support SSO")

    sales_chunks = load_chunks(db, tenant_id, "sales")
    assert len(sales_chunks) == 2
    assert {c.text for c in sales_chunks} == {
        "Acme wants SAML through Okta",
        "Globex wants EU data residency",
    }

    product_chunks = load_chunks(db, tenant_id, "product")
    assert len(product_chunks) == 1


def test_run_baseline_for_contract_returns_empty_when_a_stage_has_no_chunks(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    db, tenant_id = db_session
    llm = CannedLLMClient(BaselineResponse(findings=[BaselineFinding(
        subject="sso", slot="protocol", outcome="generalized", explanation="x",
    )]))
    embedder = FakeEmbeddingClient()

    response = run_baseline_for_contract(
        db, tenant_id, llm, embedder, from_stage="sales", to_stage="product",
    )
    assert response.findings == []
    assert llm.calls == 0  # no point calling the LLM with nothing retrieved


def test_run_baseline_for_contract_retrieves_and_judges_when_chunks_exist(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    db, tenant_id = db_session
    _make_source(db, tenant_id, "sales", "call", "We need SAML through Okta by Dec 15")
    _make_source(db, tenant_id, "product", "drive", "Support SSO, target December")

    canned = BaselineResponse(findings=[
        BaselineFinding(entity_slug="acme", subject="sso", slot="protocol", outcome="generalized", explanation="x"),
    ])
    llm = CannedLLMClient(canned)
    embedder = FakeEmbeddingClient()

    response = run_baseline_for_contract(
        db, tenant_id, llm, embedder, from_stage="sales", to_stage="product",
    )
    assert response == canned
    assert llm.calls == 1
    assert llm.last_prompt is not None
    assert "SAML through Okta" in llm.last_prompt
    assert "Support SSO" in llm.last_prompt
    assert embedder.embed_calls == 2  # once for upstream chunks, once for downstream


def test_run_baseline_eval_normalizes_findings_into_predictions_per_contract(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    db, tenant_id = db_session
    for contract_id, from_stage, to_stage in BASELINE_CONTRACTS:
        _make_source(db, tenant_id, from_stage, "call", f"{from_stage} upstream text")
        _make_source(db, tenant_id, to_stage, "drive", f"{to_stage} downstream text")

    canned = BaselineResponse(findings=[
        BaselineFinding(entity_slug="acme", subject="sso", slot="protocol", outcome="generalized", explanation="x"),
        BaselineFinding(entity_slug="mars-corp", subject="widget", slot="none", outcome="object_missing", explanation="y"),
    ])
    llm = CannedLLMClient(canned)
    embedder = FakeEmbeddingClient()

    predictions = run_baseline_eval(db, tenant_id, llm, embedder)

    assert llm.calls == len(BASELINE_CONTRACTS)
    assert len(predictions) == 2 * len(BASELINE_CONTRACTS)

    contract_ids = {p.contract_id for p in predictions}
    assert contract_ids == {c for c, _, _ in BASELINE_CONTRACTS}

    generalized = [p for p in predictions if p.outcome == "generalized"]
    assert all(p.entity_slug == "acme" for p in generalized)
    assert all(p.slot == "protocol" for p in generalized)

    # An out-of-vocabulary entity_slug normalizes to None (not silently dropped or kept
    # as a stray free-text value that could accidentally satisfy `entity_slug is None`
    # wildcard matching in `evals.scoring` for the wrong reason).
    object_missing = [p for p in predictions if p.outcome == "object_missing"]
    assert all(p.entity_slug is None for p in object_missing)
    # slot "none" (the literal string the prompt asks for) normalizes to Python None.
    assert all(p.slot is None for p in object_missing)
