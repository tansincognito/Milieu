"""The extraction cache key must account for document-level prompt context (§13.3).

The sibling-subject hint (`process._sibling_subject_key`) is folded into the prompt but is
not part of the source text, so a key built from the text alone collides across the two
cases. That collision silently defeated the incident-binding rule: a section extracted
before any sibling existed was served back to a later run that should have been told to
bind to the established incident subject.
"""

from __future__ import annotations

from app.pipeline.extraction_cache import extraction_cache_key

ARGS = ("content-hash-abc", "7", "some/model:free", "1")


def test_key_is_stable_for_identical_inputs() -> None:
    assert extraction_cache_key(*ARGS) == extraction_cache_key(*ARGS)


def test_context_key_changes_the_cache_key() -> None:
    without = extraction_cache_key(*ARGS)
    with_hint = extraction_cache_key(*ARGS, "acme:incident:INC-2311")

    assert without != with_hint


def test_different_established_subjects_do_not_collide() -> None:
    first = extraction_cache_key(*ARGS, "acme:incident:INC-2311")
    second = extraction_cache_key(*ARGS, "globex:incident:INC-9000")

    assert first != second


def test_empty_context_key_is_the_default() -> None:
    assert extraction_cache_key(*ARGS) == extraction_cache_key(*ARGS, "")
