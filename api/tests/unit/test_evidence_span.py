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


# --- Tolerant matching (markdown/dash/quote/whitespace normalization) -----------------
#
# An LLM quoting rendered markdown routinely "reads through" formatting a naive
# `str.find` won't: bold markers, em-dashes read as hyphens, curly quotes read as
# straight quotes, and reflowed whitespace. `compute_evidence_span` must still locate
# these, but the *persisted* span must index the ORIGINAL text exactly (§4.3): the caller
# is responsible for slicing `source_text[start:end]` as the object of record, so what
# matters here is that the returned span reconstructs to the meaningful text from the
# ORIGINAL source, not to the LLM's (possibly normalized) quote string.

BOLD_SOURCE = "Target date: **December 15, 2026**. Acme's fiscal year closes in December."


def test_bold_stripped_quote_is_located_in_original_text() -> None:
    quote = "Target date: December 15, 2026"
    start, end = compute_evidence_span(BOLD_SOURCE, quote)

    # The original text at this span still contains the markdown the LLM "saw through" --
    # the opening `**` falls inside the matched range; the closing `**` falls just after
    # the last matched character and is correctly excluded from the span.
    assert BOLD_SOURCE[start:end] == "Target date: **December 15, 2026"


EM_DASH_SOURCE = (
    "This is a hard requirement, not a preference — their InfoSec team runs Okta."
)


def test_em_dash_vs_hyphen_quote_is_located_in_original_text() -> None:
    quote = "a hard requirement, not a preference - their InfoSec team"
    start, end = compute_evidence_span(EM_DASH_SOURCE, quote)

    assert EM_DASH_SOURCE[start:end] == (
        "a hard requirement, not a preference — their InfoSec team"
    )
    # The persisted span still contains the real em-dash, not the LLM's hyphen.
    assert "—" in EM_DASH_SOURCE[start:end]


SMART_QUOTE_SOURCE = "Acme said “we need SAML” during the call, per Jamie’s notes."


def test_smart_quotes_vs_straight_quotes_is_located_in_original_text() -> None:
    quote = 'Acme said "we need SAML" during the call, per Jamie\'s notes'
    start, end = compute_evidence_span(SMART_QUOTE_SOURCE, quote)

    assert SMART_QUOTE_SOURCE[start:end] == (
        "Acme said “we need SAML” during the call, per Jamie’s notes"
    )


WHITESPACE_SOURCE = "SCIM provisioning is also required,\nnot optional: it is a compliance gap."


def test_collapsed_whitespace_quote_is_located_in_original_text() -> None:
    # The LLM collapses the newline in the source to a single space.
    quote = "SCIM provisioning is also required, not optional"
    start, end = compute_evidence_span(WHITESPACE_SOURCE, quote)

    assert WHITESPACE_SOURCE[start:end] == "SCIM provisioning is also required,\nnot optional"


def test_genuine_non_match_is_still_rejected_after_normalization() -> None:
    # Normalizing markdown/dashes/quotes/whitespace must not make an unrelated or
    # paraphrased quote match -- this is still a real rejection, not a false positive.
    with pytest.raises(EvidenceSpanError):
        compute_evidence_span(BOLD_SOURCE, "Target date: **January 1, 2027**")
