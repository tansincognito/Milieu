"""Eval harness (§19). Runs the real pipeline against the mock data and scores declarative
cases from `evals/cases/*.yaml`. Lives outside `app/` and `tests/` on purpose: `make check`
(unit/integration tests) must never touch a live LLM, and this package always does
(§19.1: "5 consecutive runs ... with the extraction and judge caches disabled" is the
golden-test bar; the Day 2 sets here run against the real, cached-by-default pipeline).

Not collected by pytest (`api/pyproject.toml` scopes `testpaths = ["tests"]`), and not
covered by `make check`'s `ruff check app tests` / `mypy app`. Run via `make eval`.
"""
