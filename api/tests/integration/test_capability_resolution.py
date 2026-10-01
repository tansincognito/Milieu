"""Capability canonicalization — needs real Postgres for pg_trgm `similarity()`.

`capability_vocab` has no `tenant_id` (it is a deliberately global table today — see
`docs/ARCHITECTURE-v2.md` §7.2 for the per-tenant plan). Every fixture here uses a
per-test-run random prefix on its slugs so tests can't collide with each other, with the
migration-seeded baseline vocabulary, or with rows other tests/sessions leave behind in a
shared dev database — and cleans up by that prefix rather than by tenant.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import engine
from app.models.orm import CapabilityVocab
from app.pipeline.capability_resolution import resolve_capability

pytestmark = pytest.mark.integration


@pytest.fixture
def db_session() -> Session:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    prefix = f"t{uuid.uuid4().hex[:8]}"
    yield session, prefix  # type: ignore[misc]
    session.rollback()
    session.execute(text("DELETE FROM capability_vocab WHERE slug LIKE :p"), {"p": f"{prefix}%"})
    session.commit()
    session.close()


def _confirmed(db: Session, slug: str, synonyms: list[str] | None = None) -> None:
    db.add(CapabilityVocab(slug=slug, parent_slug=None, synonyms=synonyms or [], status="confirmed"))
    db.flush()


def test_exact_match_against_seeded_baseline_never_creates_a_new_row(
    db_session: tuple[Session, str],
) -> None:
    """The migration seeds "sso" as confirmed; a proposal that only differs by case/
    whitespace must resolve to it via the exact-match step, not the trgm step, and must
    never mark it as a new capability."""
    db, _prefix = db_session

    resolution = resolve_capability(db, "  SSO ")

    assert resolution.slug == "sso"
    assert resolution.is_new is False
    assert resolution.snapped is False


def test_high_similarity_proposal_snaps_to_the_confirmed_slug(
    db_session: tuple[Session, str],
) -> None:
    """similarity('onboarding_checklist', 'onboarding_checklist_v2') = 0.875, above
    AUTO_LINK_THRESHOLD (0.85) -- measured live against this repo's own Postgres before
    writing this test. The proposal must resolve to the EXISTING slug, not create a new one
    under its own wording."""
    db, prefix = db_session
    canonical = f"{prefix}_onboarding_checklist"
    _confirmed(db, canonical)
    db.commit()

    resolution = resolve_capability(db, f"{prefix} onboarding checklist v2")

    assert resolution.slug == canonical
    assert resolution.is_new is False
    assert resolution.snapped is True


def test_snapped_proposal_is_recorded_as_a_synonym_so_the_next_call_is_an_exact_match(
    db_session: tuple[Session, str],
) -> None:
    """Proves the "vocabulary tightens itself with use" claim in the module docstring: the
    first call pays for the trgm scan and snaps; the second, IDENTICAL call must take the
    cheap exact-match path instead of scanning again."""
    db, prefix = db_session
    canonical = f"{prefix}_onboarding_checklist"
    _confirmed(db, canonical)
    db.commit()

    resolve_capability(db, f"{prefix} onboarding checklist v2")
    db.commit()
    row = db.get(CapabilityVocab, canonical)
    assert row is not None
    assert f"{prefix}_onboarding_checklist_v2" in row.synonyms

    second = resolve_capability(db, f"{prefix} onboarding checklist v2")

    assert second.slug == canonical
    assert second.snapped is False  # exact match this time, not a fresh trgm snap


def test_moderate_similarity_does_not_auto_snap_but_suggests_the_near_match(
    db_session: tuple[Session, str],
) -> None:
    """similarity(prefix + 'data_residency', prefix + 'data_resilience') = 0.645 with an
    8-char test-isolation prefix included -- in [CANDIDATE_THRESHOLD, AUTO_LINK_THRESHOLD),
    measured live against this repo's own Postgres in exactly this shape (the shared prefix
    itself raises similarity, so it had to be measured with the prefix present, not
    estimated from the bare words). This is the case the module docstring calls out as the
    reason capability resolution is more conservative than entity resolution: a wrong
    auto-snap here would be expensive to undo (it IS the subject_key), so this band must
    create a new row and only SUGGEST the near match, never apply it."""
    db, prefix = db_session
    existing = f"{prefix}_data_residency"
    _confirmed(db, existing)
    db.commit()

    resolution = resolve_capability(db, f"{prefix} data resilience")

    assert resolution.is_new is True
    assert resolution.snapped is False
    assert resolution.suggested_match == existing
    assert resolution.slug == f"{prefix}_data_resilience"

    row = db.get(CapabilityVocab, f"{prefix}_data_resilience")
    assert row is not None
    assert row.status == "candidate"
    assert row.suggested_parent_slug == existing


def test_no_match_at_all_creates_a_plain_candidate_with_no_suggestion(
    db_session: tuple[Session, str],
) -> None:
    """similarity('data_residency', 'data_region') = 0.35 -- below CANDIDATE_THRESHOLD,
    measured live. Nothing to suggest; just a new candidate."""
    db, prefix = db_session
    _confirmed(db, f"{prefix}_data_residency")
    db.commit()

    resolution = resolve_capability(db, f"{prefix} data region")

    assert resolution.is_new is True
    assert resolution.suggested_match is None
    row = db.get(CapabilityVocab, f"{prefix}_data_region")
    assert row is not None
    assert row.suggested_parent_slug is None


def test_two_items_in_one_batch_proposing_the_same_new_capability_share_one_candidate_row(
    db_session: tuple[Session, str],
) -> None:
    """A single extraction job can produce several items that all propose the same genuinely
    new capability before anything is committed (`process.py` resolves every item in the job
    against the same uncommitted session). The second call must not try to INSERT a second
    row with the same primary key."""
    db, prefix = db_session
    slug = f"{prefix} brand new topic"

    first = resolve_capability(db, slug)
    second = resolve_capability(db, slug)

    assert first.slug == second.slug
    assert first.is_new is True
    assert second.is_new is True


def test_candidate_rows_are_never_matched_against(db_session: tuple[Session, str]) -> None:
    """An unreviewed candidate must not let a second, unrelated proposal snap onto it --
    only `status = 'confirmed'` rows are ever matched against (see `_best_trgm_match`'s
    WHERE clause and `_exact_match`'s)."""
    db, prefix = db_session
    db.add(CapabilityVocab(slug=f"{prefix}_onboarding_checklist", synonyms=[], status="candidate"))
    db.commit()

    resolution = resolve_capability(db, f"{prefix} onboarding checklist v2")

    assert resolution.is_new is True
    assert resolution.slug == f"{prefix}_onboarding_checklist_v2"
