"""People directory + stage/role resolution (§3, §3.1).

Order of resolution for a person, matching §3.1:
  1. Exact match on `people.email` (or `people.slack_user_id` for Slack-sourced identities).
  2. Domain fallback: `org_domains` tells us whether an unmatched email's domain is an
     internal domain (unknown person -> null stage + review) or a known customer domain
     (external, `actor_role = customer`, `stage = sales`).
  3. No match at all -> null stage, `actor_role = other`, no person.

`actor_role` for internal people comes from `people.team`. Note: the `actor_role` enum
(§4.3) is `customer | sales | product | engineering | other | system` — it has no
`customer_success` value, even though `customer_success` is a valid `stage` (added in
C16). A person on the `customer_success` team therefore currently resolves to
`actor_role = "other"`. Flagged as a spec gap in the dispatch report.

`PeopleDirectory` is a small lookup interface so this resolution logic is unit-testable
without a database (see tests/unit/test_directory_resolve.py); `SqlAlchemyPeopleDirectory`
is the one production implementation, backed by `people` / `org_domains`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from sqlalchemy.orm import Session

from app.models.orm import OrgDomains, People

TEAM_TO_STAGE: dict[str, str] = {
    "sales": "sales",
    "product": "product",
    "engineering": "engineering",
    "customer_success": "customer_success",
}

TEAM_TO_ACTOR_ROLE: dict[str, str] = {
    "sales": "sales",
    "product": "product",
    "engineering": "engineering",
    # customer_success has no actor_role counterpart in §4.3 — see module docstring.
}

# Call transcripts label speakers directly (§18 mock data uses CUSTOMER/SALES).
SPEAKER_LABEL_TO_ACTOR_ROLE: dict[str, str] = {
    "CUSTOMER": "customer",
    "SALES": "sales",
    "PRODUCT": "product",
    "ENGINEERING": "engineering",
    "CS": "other",
}


@dataclass(frozen=True)
class PersonRecord:
    id: uuid.UUID
    team: str | None
    is_external: bool


@dataclass(frozen=True)
class DomainRecord:
    is_internal: bool


class PeopleDirectory(Protocol):
    def get_by_email(self, tenant_id: uuid.UUID, email: str) -> PersonRecord | None: ...

    def get_by_slack_user_id(
        self, tenant_id: uuid.UUID, slack_user_id: str
    ) -> PersonRecord | None: ...

    def get_domain(self, tenant_id: uuid.UUID, domain: str) -> DomainRecord | None: ...


class SqlAlchemyPeopleDirectory:
    """Production `PeopleDirectory`, backed by the `people` / `org_domains` tables."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_email(self, tenant_id: uuid.UUID, email: str) -> PersonRecord | None:
        row = (
            self._db.query(People)
            .filter(People.tenant_id == tenant_id, People.email == email.lower())
            .first()
        )
        return PersonRecord(row.id, row.team, row.is_external) if row else None

    def get_by_slack_user_id(
        self, tenant_id: uuid.UUID, slack_user_id: str
    ) -> PersonRecord | None:
        row = (
            self._db.query(People)
            .filter(People.tenant_id == tenant_id, People.slack_user_id == slack_user_id)
            .first()
        )
        return PersonRecord(row.id, row.team, row.is_external) if row else None

    def get_domain(self, tenant_id: uuid.UUID, domain: str) -> DomainRecord | None:
        row = (
            self._db.query(OrgDomains)
            .filter(OrgDomains.tenant_id == tenant_id, OrgDomains.domain == domain)
            .first()
        )
        return DomainRecord(row.is_internal) if row else None


class DirectoryResolution(NamedTuple):
    stage: str | None
    actor_role: str
    person_id: uuid.UUID | None
    is_external: bool = False


def resolve_person(
    directory: PeopleDirectory,
    tenant_id: uuid.UUID,
    *,
    email: str | None = None,
    slack_user_id: str | None = None,
) -> DirectoryResolution:
    """Resolve (stage, actor_role, person_id) for an email address and/or Slack user id."""
    person = None
    if email:
        person = directory.get_by_email(tenant_id, email)
    if person is None and slack_user_id:
        person = directory.get_by_slack_user_id(tenant_id, slack_user_id)

    if person is not None:
        if person.is_external:
            # Customer statements belong to the sales stage regardless of `team` (§3.1).
            return DirectoryResolution("sales", "customer", person.id, True)
        stage = TEAM_TO_STAGE.get(person.team or "")
        actor_role = TEAM_TO_ACTOR_ROLE.get(person.team or "", "other")
        return DirectoryResolution(stage, actor_role, person.id, False)

    if email:
        domain = email.rsplit("@", 1)[-1].lower()
        org = directory.get_domain(tenant_id, domain)
        if org is not None:
            if org.is_internal:
                # Internal domain, unknown person -> null stage + review.
                return DirectoryResolution(None, "other", None, False)
            # Known customer domain -> external, customer-authored, sales stage.
            return DirectoryResolution("sales", "customer", None, True)

    return DirectoryResolution(None, "other", None, False)


def actor_role_from_speaker_label(label: str) -> str:
    """Fallback for call transcripts (§3.1: 'source-specific hints')."""
    return SPEAKER_LABEL_TO_ACTOR_ROLE.get(label.strip().upper(), "other")
