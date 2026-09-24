"""Entity resolution, full version (§7.1):

1. Normalize: lowercase, strip punctuation and legal suffixes.
2. Exact match against `entity_aliases` (confirmed rows only).
3. Otherwise, `pg_trgm` similarity >= AUTO_LINK_THRESHOLD -> auto-link: add a confirmed
   alias to the best-matching entity.
4. Otherwise, similarity in [CANDIDATE_THRESHOLD, AUTO_LINK_THRESHOLD) -> the hint gets its
   own new entity (so it resolves instantly next time) *and* a 'candidate' alias row is
   recorded against the near-match entity for a human to review and possibly merge later.
5. Otherwise, create a new entity.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.orm import Entities, EntityAliases
from app.pipeline.seed_directory import normalize_alias

DEFAULT_ENTITY_HINT = "Unknown"

AUTO_LINK_THRESHOLD = 0.85
CANDIDATE_THRESHOLD = 0.6


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "entity"


def _best_trgm_match(
    db: Session, tenant_id: uuid.UUID, normalized: str
) -> tuple[uuid.UUID, float] | None:
    """Best-matching *confirmed* alias by trigram similarity, or None if nothing scores
    above CANDIDATE_THRESHOLD. Uses `similarity()` directly (not the `%` operator) so the
    threshold isn't at the mercy of the session's `pg_trgm.similarity_threshold` GUC."""
    row = db.execute(
        text(
            """
            SELECT entity_id, similarity(alias_normalized, :normalized) AS sim
            FROM entity_aliases
            WHERE tenant_id = :tenant_id AND status = 'confirmed'
              AND similarity(alias_normalized, :normalized) >= :threshold
            ORDER BY sim DESC
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "normalized": normalized, "threshold": CANDIDATE_THRESHOLD},
    ).first()
    if row is None:
        return None
    return row.entity_id, float(row.sim)


def _create_entity(db: Session, tenant_id: uuid.UUID, hint: str, normalized: str) -> Entities:
    entity = Entities(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=hint,
        slug=_slugify(hint),
        kind="customer",
    )
    db.add(entity)
    db.add(
        EntityAliases(
            id=uuid.uuid4(),
            entity_id=entity.id,
            tenant_id=tenant_id,
            alias_normalized=normalized,
            status="confirmed",
        )
    )
    return entity


def resolve_entity(db: Session, tenant_id: uuid.UUID, entity_hint: str | None) -> uuid.UUID:
    hint = entity_hint or DEFAULT_ENTITY_HINT
    normalized = normalize_alias(hint)

    # Step 2: exact match against a confirmed alias.
    alias = (
        db.query(EntityAliases)
        .filter(
            EntityAliases.tenant_id == tenant_id,
            EntityAliases.alias_normalized == normalized,
            EntityAliases.status == "confirmed",
        )
        .first()
    )
    if alias is not None:
        return alias.entity_id

    match = _best_trgm_match(db, tenant_id, normalized)

    if match is not None and match[1] >= AUTO_LINK_THRESHOLD:
        # Step 3: auto-link — this hint is a new alias of the matched entity.
        entity_id, _sim = match
        db.add(
            EntityAliases(
                id=uuid.uuid4(),
                entity_id=entity_id,
                tenant_id=tenant_id,
                alias_normalized=normalized,
                status="confirmed",
            )
        )
        db.flush()
        return entity_id

    entity = _create_entity(db, tenant_id, hint, normalized)

    if match is not None:
        # Step 4: candidate link, routed to review. The hint still resolves to its own
        # (new) entity now; the candidate row is a merge suggestion for a human reviewer.
        candidate_entity_id, sim = match
        db.add(
            EntityAliases(
                id=uuid.uuid4(),
                entity_id=candidate_entity_id,
                tenant_id=tenant_id,
                alias_normalized=normalized,
                status="candidate",
                similarity=round(sim, 3),
            )
        )

    # Step 5 falls through here when `match is None`: a plain new entity, no candidate row.

    # Flush (not commit) so a second extracted item with the same entity_hint, resolved
    # later in the same job, sees this alias instead of racing to insert a duplicate entity.
    db.flush()
    return entity.id
