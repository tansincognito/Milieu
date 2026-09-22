"""Entity resolution — MINIMAL for Day 1 (§7.1 steps 1-2 only): normalize + exact alias
match, else create a new entity and alias. Fuzzy `pg_trgm` matching (steps 3-4) is Day 2.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from app.models.orm import Entities, EntityAliases
from app.pipeline.seed_directory import normalize_alias

DEFAULT_ENTITY_HINT = "Unknown"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "entity"


def resolve_entity(db: Session, tenant_id: uuid.UUID, entity_hint: str | None) -> uuid.UUID:
    hint = entity_hint or DEFAULT_ENTITY_HINT
    normalized = normalize_alias(hint)

    alias = (
        db.query(EntityAliases)
        .filter(EntityAliases.tenant_id == tenant_id, EntityAliases.alias_normalized == normalized)
        .first()
    )
    if alias is not None:
        return alias.entity_id

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
        )
    )
    # Flush (not commit) so a second extracted item with the same entity_hint, resolved
    # later in the same job, sees this alias instead of racing to insert a duplicate entity.
    db.flush()
    return entity.id
