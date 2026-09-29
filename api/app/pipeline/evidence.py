"""Evidence-span invariant (§4.3): `source.text[evidence_span] == evidence_quote`.
Validated on write; the object is rejected if it fails."""

from __future__ import annotations

_EMPHASIS_CHARS = "*_"
_DASH_VARIANTS = "‐‑‒–—―"  # hyphen..horizontal bar -> "-"
_SINGLE_SMART_QUOTES = "‘’"  # ' '  -> "'"
_DOUBLE_SMART_QUOTES = "“”"  # " "  -> '"'


class EvidenceSpanError(ValueError):
    """Raised when `evidence_quote` is not an exact substring of the source text."""


def _build_normalized(text: str) -> tuple[str, list[int]]:
    """Returns `(normalized_text, mapping)` where `mapping[i]` is the index into `text` of
    the character that produced `normalized_text[i]`.

    Normalization only ever drops or 1:1-substitutes characters -- it never inserts
    anything not present in `text` -- so `mapping` is strictly increasing and a match found
    in `normalized_text` can always be re-sliced from the original `text` byte-for-byte.

    What it tolerates, because an LLM quoting rendered markdown routinely "sees through" it:
    - markdown emphasis markers (`*`, `_`) are dropped entirely.
    - Unicode dash variants (en/em dash etc.) fold to a plain ASCII hyphen.
    - smart/curly quotes fold to straight quotes.
    - any run of whitespace (including newlines) collapses to a single space.
    """
    out_chars: list[str] = []
    mapping: list[int] = []
    prev_was_space = False
    for i, ch in enumerate(text):
        if ch in _EMPHASIS_CHARS:
            continue
        if ch.isspace():
            if not prev_was_space:
                out_chars.append(" ")
                mapping.append(i)
                prev_was_space = True
            continue
        prev_was_space = False
        if ch in _DASH_VARIANTS:
            out_chars.append("-")
        elif ch in _SINGLE_SMART_QUOTES:
            out_chars.append("'")
        elif ch in _DOUBLE_SMART_QUOTES:
            out_chars.append('"')
        else:
            out_chars.append(ch)
        mapping.append(i)
    return "".join(out_chars), mapping


def compute_evidence_span(source_text: str, evidence_quote: str) -> tuple[int, int]:
    """Locate `evidence_quote` in `source_text` and return its `(start, end)` char span.

    Tries an exact substring match first (the common, cheap case). If that fails, it
    tolerantly re-locates the quote against a normalized view of both strings (markdown
    emphasis stripped, dash/quote variants folded, whitespace collapsed -- see
    `_build_normalized`) and maps the match back onto the ORIGINAL `source_text`. The
    returned span always indexes the original text, so `source_text[start:end]` is the
    persisted `evidence_quote` and the §4.3 invariant holds byte-for-byte even when the
    LLM's own quote wasn't a literal substring.
    """
    if not evidence_quote:
        raise EvidenceSpanError("evidence_quote is empty")

    start = source_text.find(evidence_quote)
    if start != -1:
        return start, start + len(evidence_quote)

    normalized_source, mapping = _build_normalized(source_text)
    normalized_quote, _ = _build_normalized(evidence_quote)
    if not normalized_quote:
        raise EvidenceSpanError(f"evidence_quote normalizes to empty: {evidence_quote!r}")

    norm_start = normalized_source.find(normalized_quote)
    if norm_start == -1:
        raise EvidenceSpanError(
            f"evidence_quote is not an exact substring of the source text: {evidence_quote!r}"
        )

    orig_start = mapping[norm_start]
    orig_end = mapping[norm_start + len(normalized_quote) - 1] + 1
    return orig_start, orig_end
