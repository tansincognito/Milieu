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

STAGES = ("sales", "product", "engineering", "customer_success")
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
ACTOR_ROLES = ("customer", "sales", "product", "engineering", "other", "system")
CONTEXT_STATUSES = ("candidate", "active", "conflicting", "superseded", "stale", "ignored")
SOURCE_KINDS = ("slack", "email", "drive", "call", "api")
JOB_STATUSES = ("queued", "running", "done", "failed", "poison")
GAP_STATUSES = ("open", "ignored", "resolved")
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

    __table_args__ = (
        UniqueConstraint("tenant_id", "alias_normalized", name="uq_alias_tenant_normalized"),
    )


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
    parent_slug: Mapped[str | None] = mapped_column(
        String, ForeignKey("capability_vocab.slug"), nullable=True
    )
    synonyms: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)


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
    upstream_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_objects.id"), nullable=False
    )
    downstream_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_objects.id"), nullable=True
    )
    slot: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)
    severity_band: Mapped[str] = mapped_column(String, nullable=False)
    inherited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")

    __table_args__ = (CheckConstraint(f"status IN {GAP_STATUSES}", name="ck_context_gaps_status"),)


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
