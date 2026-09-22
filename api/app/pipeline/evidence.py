"""Evidence-span invariant (§4.3): `source.text[evidence_span] == evidence_quote`.
Validated on write; the object is rejected if it fails."""

from __future__ import annotations


class EvidenceSpanError(ValueError):
    """Raised when `evidence_quote` is not an exact substring of the source text."""


def compute_evidence_span(source_text: str, evidence_quote: str) -> tuple[int, int]:
    if not evidence_quote:
        raise EvidenceSpanError("evidence_quote is empty")
    start = source_text.find(evidence_quote)
    if start == -1:
        raise EvidenceSpanError(
            f"evidence_quote is not an exact substring of the source text: {evidence_quote!r}"
        )
    end = start + len(evidence_quote)
    return start, end
