import uuid

from app.directory.resolve import (
    DomainRecord,
    PersonRecord,
    actor_role_from_speaker_label,
    resolve_person,
)

TENANT = uuid.uuid4()


class FakeDirectory:
    def __init__(
        self,
        by_email: dict[str, PersonRecord] | None = None,
        by_slack: dict[str, PersonRecord] | None = None,
        domains: dict[str, DomainRecord] | None = None,
    ) -> None:
        self._by_email = by_email or {}
        self._by_slack = by_slack or {}
        self._domains = domains or {}

    def get_by_email(self, tenant_id: uuid.UUID, email: str) -> PersonRecord | None:
        return self._by_email.get(email)

    def get_by_slack_user_id(self, tenant_id: uuid.UUID, slack_user_id: str) -> PersonRecord | None:
        return self._by_slack.get(slack_user_id)

    def get_domain(self, tenant_id: uuid.UUID, domain: str) -> DomainRecord | None:
        return self._domains.get(domain)


def test_exact_email_match_resolves_stage_and_actor_role() -> None:
    pm_id = uuid.uuid4()
    directory = FakeDirectory(
        by_email={"pm@example.com": PersonRecord(id=pm_id, team="product", is_external=False)}
    )

    result = resolve_person(directory, TENANT, email="pm@example.com")

    assert result.stage == "product"
    assert result.actor_role == "product"
    assert result.person_id == pm_id
    assert result.is_external is False


def test_customer_domain_fallback_when_no_exact_person_match() -> None:
    directory = FakeDirectory(domains={"acme.com": DomainRecord(is_internal=False)})

    result = resolve_person(directory, TENANT, email="dana@acme.com")

    assert result.stage == "sales"
    assert result.actor_role == "customer"
    assert result.person_id is None
    assert result.is_external is True


def test_internal_domain_unknown_person_gets_null_stage() -> None:
    directory = FakeDirectory(domains={"ourcompany.com": DomainRecord(is_internal=True)})

    result = resolve_person(directory, TENANT, email="newhire@ourcompany.com")

    assert result.stage is None
    assert result.actor_role == "other"
    assert result.person_id is None


def test_completely_unknown_email_and_domain() -> None:
    directory = FakeDirectory()

    result = resolve_person(directory, TENANT, email="stranger@nowhere.example")

    assert result.stage is None
    assert result.actor_role == "other"
    assert result.person_id is None


def test_slack_user_id_lookup() -> None:
    eng_id = uuid.uuid4()
    directory = FakeDirectory(
        by_slack={"U123": PersonRecord(id=eng_id, team="engineering", is_external=False)}
    )

    result = resolve_person(directory, TENANT, slack_user_id="U123")

    assert result.stage == "engineering"
    assert result.actor_role == "engineering"
    assert result.person_id == eng_id


def test_external_person_forces_customer_actor_role() -> None:
    directory = FakeDirectory(
        by_email={"dana@acme.com": PersonRecord(id=uuid.uuid4(), team=None, is_external=True)}
    )

    result = resolve_person(directory, TENANT, email="dana@acme.com")

    assert result.actor_role == "customer"
    assert result.stage == "sales"


def test_speaker_label_fallback_for_call_transcripts() -> None:
    assert actor_role_from_speaker_label("CUSTOMER") == "customer"
    assert actor_role_from_speaker_label("SALES") == "sales"
    assert actor_role_from_speaker_label("unknown-speaker") == "other"
