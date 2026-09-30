"""SQLAlchemy ORM models for all §16 tables.

Enums are enforced via CHECK constraints (not native Postgres ENUM types) so
new values (e.g. a new stage) can be added with a plain migration instead of
`ALTER TYPE`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from psycopg.types.range import Range
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INT4RANGE, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

STAGES = ("sales", "product", "engineering", "customer_success", "leadership")
CONTEXT_TYPES = (
    "requirement",
    "decision",
    "constraint",
    "commitment",
    "problem",
    "open_question",
    "resolution",
    "dependency",
)
ACTOR_ROLES = (
    "customer",
    "sales",
    "product",
    "engineering",
    "customer_success",
    "leadership",
    "other",
    "system",
)
CONTEXT_STATUSES = ("candidate", "active", "conflicting", "superseded", "stale", "ignored")
SOURCE_KINDS = ("slack", "email", "drive", "call", "api")
JOB_STATUSES = ("queued", "running", "done", "failed", "poison")
GAP_STATUSES = ("open", "ignored", "resolved")
GAP_OUTCOMES = (
    "preserved",
    "equivalent",
    "generalized",
    "missing",
    "contradicted",
    "object_missing",
    "stale_reference",
)
REVIEW_ACTIONS = ("confirm", "edit", "ignore", "resolve_conflict", "mark_stale")
RELATION_KINDS = (
    "derived_from",
    "supported_by",
    "decided_by",
    "implemented_by",
    "supersedes",
    "contradicts",
    "duplicate_of",
)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TenantMixin:
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)


class People(Base, TenantMixin):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    slack_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    team: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    is_external: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    company_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_people_tenant_email"),
        UniqueConstraint("tenant_id", "slack_user_id", name="uq_people_tenant_slack_user_id"),
    )


class Users(Base, TenantMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False, default="member")


class OrgDomains(Base, TenantMixin):
    __tablename__ = "org_domains"

    domain: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, nullable=False
    )
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True
    )


class Entities(Base, TenantMixin):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="customer")

    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_entities_tenant_slug"),)


class EntityAliases(Base):
    __tablename__ = "entity_aliases"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String, nullable=False)
    # §7.1 step 4: 'candidate' rows are 0.6-0.85 trgm near-misses awaiting review; only
    # 'confirmed' rows are unique per (tenant, alias_normalized) — see migration 0002.
    status: Mapped[str] = mapped_column(String, nullable=False, default="confirmed")
    similarity: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    __table_args__ = (CheckConstraint("status IN ('confirmed', 'candidate')", name="ck_entity_aliases_status"),)


class Sources(Base, TenantMixin):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    stage: Mapped[str | None] = mapped_column(String, nullable=True)
    acl: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "kind", "external_id", "content_hash", name="uq_source_dedupe"
        ),
        CheckConstraint(f"kind IN {SOURCE_KINDS}", name="ck_sources_kind"),
        CheckConstraint(f"stage IS NULL OR stage IN {STAGES}", name="ck_sources_stage"),
    )


class ContextObjects(Base, TenantMixin):
    __tablename__ = "context_objects"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    subject_key: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    actor_label: Mapped[str] = mapped_column(String, nullable=False)
    actor_role: Mapped[str] = mapped_column(String, nullable=False)
    stage: Mapped[str | None] = mapped_column(String, nullable=True)
    authority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="candidate")
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_span: Mapped[Range] = mapped_column(INT4RANGE, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    extraction_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # §7.3 R3 "same author": resolved through the people directory. Nullable — drive
    # sources carry no author identity (§6.2 gap) and call transcripts label roles only.
    actor_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"type IN {CONTEXT_TYPES}", name="ck_context_objects_type"),
        CheckConstraint(f"actor_role IN {ACTOR_ROLES}", name="ck_context_objects_actor_role"),
        CheckConstraint(f"stage IS NULL OR stage IN {STAGES}", name="ck_context_objects_stage"),
        CheckConstraint(f"status IN {CONTEXT_STATUSES}", name="ck_context_objects_status"),
        CheckConstraint("authority BETWEEN 0 AND 4", name="ck_context_objects_authority"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_context_objects_confidence"
        ),
    )


class ContextVersions(Base):
    __tablename__ = "context_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_objects.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContextRelations(Base):
    __tablename__ = "context_relations"

    id: Mapped[uuid.UUID] = uuid_pk()
    from_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_objects.id"), nullable=False
    )
    to_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_objects.id"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"relation IN {RELATION_KINDS}", name="ck_context_relations_relation"),
    )


class CapabilityVocab(Base):
    __tablename__ = "capability_vocab"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    # Confirmed hierarchy edge (e.g. saml.parent_slug = "sso"), set by a human review action.
    # Distinct from `suggested_parent_slug` below, which is an unconfirmed machine guess.
    parent_slug: Mapped[str | None] = mapped_column(
        String, ForeignKey("capability_vocab.slug"), nullable=True
    )
    # Confirmed alternate spellings/phrasings of this slug. `capability_resolution.py`
    # matches a new proposal against these (as well as `slug` itself) before deciding the
    # proposal is genuinely new.
    synonyms: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    # 'confirmed' rows are real, matchable vocabulary; 'candidate' rows are unreviewed
    # proposals the extractor made that a human hasn't looked at yet. Mirrors
    # entity_aliases.status (§7.1) — same two-state review pattern, applied to capabilities.
    status: Mapped[str] = mapped_column(String, nullable=False, default="candidate")
    # An unconfirmed near-match found at resolution time (similarity in the candidate band,
    # below the auto-link threshold) — "this might be the same thing as X", for a reviewer
    # to accept (promoting this proposal to a synonym of X) or reject (confirming it stands
    # alone). Never used to build a subject_key; only `slug` is.
    suggested_parent_slug: Mapped[str | None] = mapped_column(
        String, ForeignKey("capability_vocab.slug"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'candidate')", name="ck_capability_vocab_status"),
    )


class ContextContracts(Base):
    __tablename__ = "context_contracts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    from_stage: Mapped[str] = mapped_column(String, nullable=False)
    to_stage: Mapped[str] = mapped_column(String, nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class HandoffValidations(Base):
    __tablename__ = "handoff_validations"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=False
    )
    contract_id: Mapped[str] = mapped_column(
        String, ForeignKey("context_contracts.id"), nullable=False
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_hash: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContextGaps(Base):
    __tablename__ = "context_gaps"

    id: Mapped[uuid.UUID] = uuid_pk()
    validation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("handoff_validations.id"), nullable=False
    )
    contract_field: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable: `present`-check gaps (§9's has_derived_from/has_customer_contact/
    # acceptance-criteria rules) have no upstream counterpart to compare against — see
    # migration 0004.
    upstream_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_objects.id"), nullable=True
    )
    downstream_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_objects.id"), nullable=True
    )
    slot: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)
    severity_band: Mapped[str] = mapped_column(String, nullable=False)
    inherited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # §10.1 step 1: "the gap is tagged upstream_conflict so the report shows that the
    # error started inside the upstream stage" — see migration 0004.
    upstream_conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")

    __table_args__ = (
        CheckConstraint(f"status IN {GAP_STATUSES}", name="ck_context_gaps_status"),
        CheckConstraint(f"outcome IN {GAP_OUTCOMES}", name="ck_context_gaps_outcome"),
    )


class Reviews(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    target_kind: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint(f"action IN {REVIEW_ACTIONS}", name="ck_reviews_action"),)


class ProcessingJobs(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint(f"status IN {JOB_STATUSES}", name="ck_jobs_status"),)


class EvalCases(Base):
    __tablename__ = "eval_cases"

    id: Mapped[uuid.UUID] = uuid_pk()
    category: Mapped[str] = mapped_column(String, nullable=False)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expected: Mapped[dict] = mapped_column(JSONB, nullable=False)
    origin: Mapped[str] = mapped_column(String, nullable=False, default="seed")

    __table_args__ = (
        CheckConstraint("origin IN ('seed', 'review')", name="ck_eval_cases_origin"),
    )


class EvalResults(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_cases.id"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actual: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


TENANT_MODES = ("simulation", "production")


class Tenants(Base):
    """First-class tenant row. Until now `tenant_id` was just a UUID scattered across every
    table via `TenantMixin`, with the one tenant anyone actually used hardcoded in
    `Settings.tenant_id` -- there was nothing to attach tenant-level configuration to.

    `mode` is the simulation/production switch: 'simulation' (the only functional value
    today) means every connected source reads from `simulation_seed_sources` below instead
    of a real external API. 'production' is accepted as a value but not yet wired to any
    real connector -- no real Gmail/Slack/Drive OAuth exists yet (architecture v2 §5) -- so
    `PATCH /tenant/mode` refuses to switch into it rather than silently no-op.
    """

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="simulation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint(f"mode IN {TENANT_MODES}", name="ck_tenants_mode"),)


CONNECTION_KINDS = ("slack", "email", "drive", "call", "directory")
CONNECTION_PROVIDERS = ("simulation", "google", "microsoft", "okta", "slack_api")
CONNECTION_STATUSES = ("disconnected", "pending", "connected", "error")


class Connections(Base, TenantMixin):
    """One connected source (§5 of architecture v2). The org setup screen's source
    checkboxes are literally this table: checking "Slack" creates/updates a row here with
    `provider='simulation'` and `status='connected'`; the ingestion path checks this table
    before a connector kind is allowed to run."""

    __tablename__ = "connections"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False, default="simulation")
    status: Mapped[str] = mapped_column(String, nullable=False, default="disconnected")
    external_account: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", name="uq_connections_tenant_kind"),
        CheckConstraint(f"kind IN {CONNECTION_KINDS}", name="ck_connections_kind"),
        CheckConstraint(f"provider IN {CONNECTION_PROVIDERS}", name="ck_connections_provider"),
        CheckConstraint(f"status IN {CONNECTION_STATUSES}", name="ck_connections_status"),
    )


class SimulationSeedSources(Base, TenantMixin):
    """A simulated API response, stored in Postgres instead of a flat file on disk.

    This is the "stored in a Postgres table" requirement: today's mock connectors
    (`app/connectors/mock_*.py`) read `/mock-data/*` directly off disk. This table is where
    that content lives instead, in the same shape a connector's `fetch()` already
    constructs (`kind`, `external_id`, `payload`, `source_ts`) -- the payload is exactly
    what a real API would hand back, so a future real connector and the simulation
    connector can share the same downstream `normalize()` step.

    NOTE (scoped honestly, see the session's dispatch notes): this table is populated by
    `scripts/import_mock_data_to_postgres.py` from the existing `/mock-data` files, but the
    connector read path has NOT been switched over to it yet -- `app/connectors/mock_*.py`
    still reads disk files today, unchanged, so nothing about the already-verified
    ingestion/eval/dashboard path is put at risk by adding this table. The switch is the
    next piece of this work, not done in this pass.
    """

    __tablename__ = "simulation_seed_sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "kind", "external_id", name="uq_simulation_seed_tenant_kind_external"
        ),
        CheckConstraint(f"kind IN {CONNECTION_KINDS}", name="ck_simulation_seed_kind"),
    )


SYNC_RUN_STATUSES = ("ok", "partial", "rate_limited", "auth_expired", "failed")


class SyncRuns(Base):
    """One simulated sync attempt (architecture v2 §5). Makes "authentication states,
    failures, rate limits" an observable log instead of a static field: `POST
    /connections/{kind}/sync` writes one of these per call, with a real (simulated)
    chance of `rate_limited`/`auth_expired`/`failed`, not just always 'ok'."""

    __tablename__ = "sync_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN {SYNC_RUN_STATUSES}", name="ck_sync_runs_status"),
    )


INCIDENT_SEVERITIES = ("P0", "P1", "P2")
INCIDENT_STATUSES = ("open", "resolved")


class Incidents(Base, TenantMixin):
    """First-class incident (migration 0009). Replaces reconstructing "everything about
    this incident" from a subject_key pattern + a time-window guess at read time — see the
    migration docstring for why that broke down beyond a single-incident demo, and how this
    fixes I7 (an incident spanning more than one customer) via `IncidentEntities`."""

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = uuid_pk()
    incident_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="P1")
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    declared_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "incident_id", name="uq_incidents_tenant_incident_id"),
        CheckConstraint(f"severity IN {INCIDENT_SEVERITIES}", name="ck_incidents_severity"),
        CheckConstraint(f"status IN {INCIDENT_STATUSES}", name="ck_incidents_status"),
    )


class IncidentEntities(Base):
    """Many-to-many: which customer(s) an incident affects. Plural by design (I7)."""

    __tablename__ = "incident_entities"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), primary_key=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), primary_key=True
    )


class IncidentContextObjects(Base):
    """Which context objects belong to an incident. `linked=True` is a confirmed tie
    (the object carried the real incident_id, or a human confirmed it); `linked=False` is
    an unreviewed candidate the correlation heuristic suggested — same capability, inside
    the incident's declared window. Mirrors `entity_aliases.status`'s confirmed/candidate
    pattern (§7.1), applied to incident correlation instead of entity aliasing."""

    __tablename__ = "incident_context_objects"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), primary_key=True
    )
    context_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_objects.id"), primary_key=True
    )
    linked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
