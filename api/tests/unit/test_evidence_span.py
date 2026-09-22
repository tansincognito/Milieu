import pytest

from app.pipeline.evidence import EvidenceSpanError, compute_evidence_span

SOURCE_TEXT = (
    "CUSTOMER: We need SAML through Okta as our identity provider, live by December 15th."
)


def test_exact_substring_returns_matching_span() -> None:
    quote = "SAML through Okta"
    start, end = compute_evidence_span(SOURCE_TEXT, quote)

    assert SOURCE_TEXT[start:end] == quote


def test_non_substring_is_rejected() -> None:
    with pytest.raises(EvidenceSpanError):
        compute_evidence_span(SOURCE_TEXT, "SAML through Azure AD")


def test_paraphrased_quote_is_rejected() -> None:
    # The LLM must not summarize into the evidence_quote — it must be verbatim.
    with pytest.raises(EvidenceSpanError):
        compute_evidence_span(SOURCE_TEXT, "the customer wants SAML via Okta")


def test_empty_quote_is_rejected() -> None:
    with pytest.raises(EvidenceSpanError):
        compute_evidence_span(SOURCE_TEXT, "")
