"""Pure unit coverage for `normalize_capability` — no DB needed. The trgm-matching half
(`resolve_capability`) needs real Postgres and lives in
tests/integration/test_capability_resolution.py."""

from __future__ import annotations

from app.pipeline.capability_resolution import normalize_capability


def test_lowercases_and_collapses_separators() -> None:
    assert normalize_capability("Audit Log Retention") == "audit_log_retention"


def test_hyphens_and_spaces_normalize_identically() -> None:
    assert normalize_capability("audit-log-retention") == normalize_capability(
        "audit log retention"
    )


def test_trims_surrounding_whitespace_and_punctuation() -> None:
    assert normalize_capability("  sso!! ") == "sso"


def test_repeated_separators_collapse_to_one_underscore() -> None:
    assert normalize_capability("data___residency") == "data_residency"


def test_empty_or_punctuation_only_falls_back_rather_than_producing_empty_slug() -> None:
    # An empty subject_key segment would be silently wrong (`acme:` instead of a real
    # capability) rather than loudly wrong, so this must never return "".
    assert normalize_capability("") == "capability"
    assert normalize_capability("---") == "capability"


def test_already_normalized_input_is_unchanged() -> None:
    assert normalize_capability("sso") == "sso"
