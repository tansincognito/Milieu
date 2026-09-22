"""Loads `mock-data/directory.json` into `entities`/`entity_aliases`/`org_domains`/`people`
(§3.1: "The MVP seeds the directory from /mock-data/directory.json"). Idempotent: re-running
upserts by natural key instead of duplicating rows.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import cast

from sqlalchemy.orm import Session

from app.models.orm import Entities, EntityAliases, OrgDomains, People
from app.schemas.sources import Stage

_STRIP_SUFFIXES = ("inc", "corp", "corporation", "llc", "ltd", "co")


def normalize_alias(name: str) -> str:
    """§7.1 step 1: lowercase, strip punctuation and legal suffixes."""
    lowered = re.sub(r"[^\w\s]", "", name.lower())
    words = [w for w in lowered.split() if w not in _STRIP_SUFFIXES]
    return " ".join(words)


def load_directory(db: Session, tenant_id: uuid.UUID, directory_path: Path) -> None:
    data = json.loads(directory_path.read_text(encoding="utf-8"))

    entity_id_by_slug: dict[str, uuid.UUID] = {}
    seen_normalized_aliases: set[str] = set()
    for entity in data.get("entities", []):
        row = (
            db.query(Entities)
            .filter(Entities.tenant_id == tenant_id, Entities.slug == entity["slug"])
            .first()
        )
        if row is None:
            row = Entities(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                name=entity["name"],
                slug=entity["slug"],
                kind=entity.get("kind", "customer"),
            )
            db.add(row)
            db.flush()
        entity_id_by_slug[entity["slug"]] = row.id

        for alias in entity.get("aliases", [entity["name"]]):
            normalized = normalize_alias(alias)
            # Different raw aliases (e.g. "Acme", "Acme Corp", "ACME Inc.") can normalize
            # to the same string once legal suffixes are stripped — skip re-inserting one
            # already handled in this run, in addition to checking what's already in the DB.
            if normalized in seen_normalized_aliases:
                continue
            existing_alias = (
                db.query(EntityAliases)
                .filter(
                    EntityAliases.tenant_id == tenant_id,
                    EntityAliases.alias_normalized == normalized,
                )
                .first()
            )
            seen_normalized_aliases.add(normalized)
            if existing_alias is None:
                db.add(
                    EntityAliases(
                        id=uuid.uuid4(),
                        entity_id=row.id,
                        tenant_id=tenant_id,
                        alias_normalized=normalized,
                    )
                )

    for domain in data.get("org_domains", []):
        domain_row = (
            db.query(OrgDomains)
            .filter(OrgDomains.tenant_id == tenant_id, OrgDomains.domain == domain["domain"])
            .first()
        )
        entity_id = entity_id_by_slug.get(domain.get("entity_slug", ""))
        if domain_row is None:
            db.add(
                OrgDomains(
                    domain=domain["domain"],
                    tenant_id=tenant_id,
                    is_internal=domain["is_internal"],
                    entity_id=entity_id,
                )
            )
        else:
            domain_row.is_internal = domain["is_internal"]
            domain_row.entity_id = entity_id

    for person in data.get("people", []):
        company_entity_id = entity_id_by_slug.get(person.get("company_entity_slug", ""))
        person_row = None
        if person.get("email"):
            person_row = (
                db.query(People)
                .filter(People.tenant_id == tenant_id, People.email == person["email"].lower())
                .first()
            )
        if person_row is None:
            db.add(
                People(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    email=(person.get("email") or "").lower() or None,
                    slack_user_id=person.get("slack_user_id"),
                    name=person["name"],
                    team=person.get("team"),
                    role=person.get("role"),
                    is_external=person.get("is_external", False),
                    company_entity_id=company_entity_id,
                )
            )
        else:
            person_row.slack_user_id = person.get("slack_user_id")
            person_row.name = person["name"]
            person_row.team = person.get("team")
            person_row.role = person.get("role")
            person_row.is_external = person.get("is_external", False)
            person_row.company_entity_id = company_entity_id

    db.commit()


def load_slack_channel_stage_map(directory_path: Path) -> dict[str, Stage | None]:
    data = json.loads(directory_path.read_text(encoding="utf-8"))
    return cast(dict[str, "Stage | None"], dict(data.get("slack_channels", {})))
