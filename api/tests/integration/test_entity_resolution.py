"""Entity resolution, full version (§7.1) — needs real Postgres for pg_trgm `similarity()`."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import engine
from app.models.orm import Entities, EntityAliases
from app.pipeline.entity_resolution import resolve_entity

pytestmark = pytest.mark.integration


@pytest.fixture
def db_session() -> Session:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    tenant_id = uuid.uuid4()
    yield session, tenant_id  # type: ignore[misc]
    session.rollback()
    session.execute(text("DELETE FROM entity_aliases WHERE tenant_id = :t"), {"t": tenant_id})
    session.execute(text("DELETE FROM entities WHERE tenant_id = :t"), {"t": tenant_id})
    session.commit()
    session.close()


def test_acme_aliases_collapse_at_exact_match_step(db_session: tuple[Session, uuid.UUID]) -> None:
    """§7.1: 'Acme', 'Acme Corp', and 'ACME Inc.' resolve to one entity at step 2 — they all
    normalize (lowercase, strip legal suffixes) to the same string once the first alias is
    seeded, so this never reaches the trgm step."""
    db, tenant_id = db_session

    first_id = resolve_entity(db, tenant_id, "Acme")
    second_id = resolve_entity(db, tenant_id, "Acme Corp")
    third_id = resolve_entity(db, tenant_id, "ACME Inc.")
    db.commit()

    assert first_id == second_id == third_id
    assert db.query(Entities).filter(Entities.tenant_id == tenant_id).count() == 1
    assert (
        db.query(EntityAliases)
        .filter(EntityAliases.tenant_id == tenant_id, EntityAliases.status == "confirmed")
        .count()
        == 1
    )


def test_trgm_auto_links_above_threshold(db_session: tuple[Session, uuid.UUID]) -> None:
    """similarity('acme global holdings', 'acme global holding') = 0.86 (verified live) —
    above AUTO_LINK_THRESHOLD, so the second hint auto-links to the first entity instead of
    creating a new one."""
    db, tenant_id = db_session

    first_id = resolve_entity(db, tenant_id, "Acme Global Holdings")
    second_id = resolve_entity(db, tenant_id, "Acme Global Holding")
    db.commit()

    assert first_id == second_id
    assert db.query(Entities).filter(Entities.tenant_id == tenant_id).count() == 1
    aliases = db.query(EntityAliases).filter(EntityAliases.tenant_id == tenant_id).all()
    assert len(aliases) == 2
    assert all(a.status == "confirmed" for a in aliases)


def test_trgm_near_miss_routes_to_review(db_session: tuple[Session, uuid.UUID]) -> None:
    """similarity('quantum dynamics', 'quantum dynamic') = 0.83 (verified live) — in the
    0.6-0.85 candidate band. The hint gets its OWN new entity (so it resolves instantly next
    time), plus a 'candidate' alias row against the near-match entity for review."""
    db, tenant_id = db_session

    seed_id = resolve_entity(db, tenant_id, "Quantum Dynamics Corp")
    candidate_id = resolve_entity(db, tenant_id, "Quantum Dynamic Corp")
    db.commit()

    assert candidate_id != seed_id  # a new entity was created, not auto-merged
    assert db.query(Entities).filter(Entities.tenant_id == tenant_id).count() == 2

    candidate_rows = (
        db.query(EntityAliases)
        .filter(EntityAliases.tenant_id == tenant_id, EntityAliases.status == "candidate")
        .all()
    )
    assert len(candidate_rows) == 1
    assert candidate_rows[0].entity_id == seed_id  # suggests merging into the near-match
    assert candidate_rows[0].alias_normalized == "quantum dynamic"
    assert 0.6 <= float(candidate_rows[0].similarity) < 0.85

    # The candidate hint still resolves to its own entity on a repeat call.
    assert resolve_entity(db, tenant_id, "Quantum Dynamic Corp") == candidate_id


def test_unrelated_hint_creates_new_entity_no_candidate(
    db_session: tuple[Session, uuid.UUID],
) -> None:
    db, tenant_id = db_session

    resolve_entity(db, tenant_id, "Acme")
    other_id = resolve_entity(db, tenant_id, "Globex")
    db.commit()

    assert db.query(Entities).filter(Entities.tenant_id == tenant_id).count() == 2
    assert (
        db.query(EntityAliases)
        .filter(EntityAliases.tenant_id == tenant_id, EntityAliases.status == "candidate")
        .count()
        == 0
    )
    other = db.get(Entities, other_id)
    assert other is not None and other.slug == "globex"
