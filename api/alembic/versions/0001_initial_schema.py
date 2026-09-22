"""initial schema: all §16 tables, extensions, seed capability_vocab

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STAGES = "'sales', 'product', 'engineering', 'customer_success'"
CONTEXT_TYPES = (
    "'requirement', 'decision', 'constraint', 'commitment', "
    "'problem', 'open_question', 'resolution', 'dependency'"
)
ACTOR_ROLES = "'customer', 'sales', 'product', 'engineering', 'other', 'system'"
CONTEXT_STATUSES = "'candidate', 'active', 'conflicting', 'superseded', 'stale', 'ignored'"
SOURCE_KINDS = "'slack', 'email', 'drive', 'call', 'api'"
JOB_STATUSES = "'queued', 'running', 'done', 'failed', 'poison'"
GAP_STATUSES = "'open', 'ignored', 'resolved'"
REVIEW_ACTIONS = "'confirm', 'edit', 'ignore', 'resolve_conflict', 'mark_stale'"
RELATION_KINDS = (
    "'derived_from', 'supported_by', 'decided_by', 'implemented_by', "
    "'supersedes', 'contradicts', 'duplicate_of'"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "entities",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("slug", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False, server_default="customer"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_entities_tenant_slug"),
    )

    op.create_table(
        "people",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("email", sa.String, nullable=True),
        sa.Column("slack_user_id", sa.String, nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("team", sa.String, nullable=True),
        sa.Column("role", sa.String, nullable=True),
        sa.Column("is_external", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "company_entity_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("entities.id"),
            nullable=True,
        ),
        sa.UniqueConstraint("tenant_id", "email", name="uq_people_tenant_email"),
        sa.UniqueConstraint("tenant_id", "slack_user_id", name="uq_people_tenant_slack_user_id"),
    )

    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("person_id", pg.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=True),
        sa.Column("role", sa.String, nullable=False, server_default="member"),
    )

    op.create_table(
        "org_domains",
        sa.Column("domain", sa.String, primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("is_internal", sa.Boolean, nullable=False),
        sa.Column("entity_id", pg.UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=True),
    )

    op.create_table(
        "entity_aliases",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_id", pg.UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("alias_normalized", sa.String, nullable=False),
        sa.UniqueConstraint("tenant_id", "alias_normalized", name="uq_alias_tenant_normalized"),
    )

    op.create_table(
        "sources",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("external_id", sa.String, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("content_hash", sa.String, nullable=False),
        sa.Column("stage", sa.String, nullable=True),
        sa.Column("acl", pg.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("provenance", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("source_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "tenant_id", "kind", "external_id", "content_hash", name="uq_source_dedupe"
        ),
        sa.CheckConstraint(f"kind IN ({SOURCE_KINDS})", name="ck_sources_kind"),
        sa.CheckConstraint(f"stage IS NULL OR stage IN ({STAGES})", name="ck_sources_stage"),
    )

    op.create_table(
        "context_objects",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("entity_id", pg.UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("type", sa.String, nullable=False),
        sa.Column("subject_key", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("attributes", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("actor_label", sa.String, nullable=False),
        sa.Column("actor_role", sa.String, nullable=False),
        sa.Column("stage", sa.String, nullable=True),
        sa.Column("authority", sa.SmallInteger, nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="candidate"),
        sa.Column("confirmed_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_id", pg.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column("evidence_quote", sa.Text, nullable=False),
        sa.Column("evidence_span", pg.INT4RANGE, nullable=False),
        sa.Column("embedding", pg.ARRAY(sa.Float), nullable=True),  # replaced below via raw SQL
        sa.Column("extraction_key", sa.String, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"type IN ({CONTEXT_TYPES})", name="ck_context_objects_type"),
        sa.CheckConstraint(f"actor_role IN ({ACTOR_ROLES})", name="ck_context_objects_actor_role"),
        sa.CheckConstraint(
            f"stage IS NULL OR stage IN ({STAGES})", name="ck_context_objects_stage"
        ),
        sa.CheckConstraint(f"status IN ({CONTEXT_STATUSES})", name="ck_context_objects_status"),
        sa.CheckConstraint("authority BETWEEN 0 AND 4", name="ck_context_objects_authority"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_context_objects_confidence"
        ),
    )
    # embedding needs the pgvector type, not a plain float array; swap the column type.
    op.drop_column("context_objects", "embedding")
    op.execute("ALTER TABLE context_objects ADD COLUMN embedding vector(384)")

    op.create_table(
        "context_versions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "context_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("context_objects.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("attributes", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("changed_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.String, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "context_relations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "from_id", pg.UUID(as_uuid=True), sa.ForeignKey("context_objects.id"), nullable=False
        ),
        sa.Column(
            "to_id", pg.UUID(as_uuid=True), sa.ForeignKey("context_objects.id"), nullable=False
        ),
        sa.Column("relation", sa.String, nullable=False),
        sa.Column("created_by", sa.String, nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"relation IN ({RELATION_KINDS})", name="ck_context_relations_relation"),
    )

    op.create_table(
        "capability_vocab",
        sa.Column("slug", sa.String, primary_key=True),
        sa.Column("parent_slug", sa.String, sa.ForeignKey("capability_vocab.slug"), nullable=True),
        sa.Column("synonyms", pg.ARRAY(sa.String), nullable=False, server_default="{}"),
    )

    op.create_table(
        "context_contracts",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("from_stage", sa.String, nullable=False),
        sa.Column("to_stage", sa.String, nullable=False),
        sa.Column("spec", pg.JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
    )

    op.create_table(
        "handoff_validations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_id", pg.UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
        sa.Column(
            "contract_id", sa.String, sa.ForeignKey("context_contracts.id"), nullable=False
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_hash", sa.String, nullable=False),
        sa.Column("summary", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "context_gaps",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "validation_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("handoff_validations.id"),
            nullable=False,
        ),
        sa.Column("contract_field", sa.String, nullable=False),
        sa.Column(
            "upstream_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("context_objects.id"),
            nullable=False,
        ),
        sa.Column(
            "downstream_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("context_objects.id"),
            nullable=True,
        ),
        sa.Column("slot", sa.String, nullable=True),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column("severity", sa.Numeric(4, 2), nullable=False),
        sa.Column("severity_band", sa.String, nullable=False),
        sa.Column("inherited", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="open"),
        sa.CheckConstraint(f"status IN ({GAP_STATUSES})", name="ck_context_gaps_status"),
    )

    op.create_table(
        "reviews",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("reviewer_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("target_kind", sa.String, nullable=False),
        sa.Column("target_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("before", pg.JSONB, nullable=True),
        sa.Column("after", pg.JSONB, nullable=True),
        sa.Column("note", sa.String, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"action IN ({REVIEW_ACTIONS})", name="ck_reviews_action"),
    )

    op.create_table(
        "processing_jobs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String, nullable=False),
        sa.Column("idempotency_key", sa.String, nullable=False, unique=True),
        sa.Column("payload", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String, nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"status IN ({JOB_STATUSES})", name="ck_jobs_status"),
    )
    op.create_index("ix_processing_jobs_claim", "processing_jobs", ["status", "run_after"])

    op.create_table(
        "eval_cases",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("input", pg.JSONB, nullable=False),
        sa.Column("expected", pg.JSONB, nullable=False),
        sa.Column("origin", sa.String, nullable=False, server_default="seed"),
        sa.CheckConstraint("origin IN ('seed', 'review')", name="ck_eval_cases_origin"),
    )

    op.create_table(
        "eval_results",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", pg.UUID(as_uuid=True), sa.ForeignKey("eval_cases.id"), nullable=False),
        sa.Column("run_id", sa.String, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("actual", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("model_id", sa.String, nullable=False),
        sa.Column("prompt_version", sa.String, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # Seed capability_vocab (§4.2). Region (EU ⊃ eu-west-1) hierarchy lives in code, not here.
    op.bulk_insert(
        sa.table(
            "capability_vocab",
            sa.column("slug", sa.String),
            sa.column("parent_slug", sa.String),
            sa.column("synonyms", pg.ARRAY(sa.String)),
        ),
        [
            {"slug": "sso", "parent_slug": None, "synonyms": ["single sign-on", "saml", "oidc", "okta"]},
            {"slug": "scim", "parent_slug": None, "synonyms": ["provisioning", "user sync"]},
            {"slug": "audit_logs", "parent_slug": None, "synonyms": ["audit log", "audit trail"]},
            {"slug": "data_residency", "parent_slug": None, "synonyms": ["region", "data location"]},
            {"slug": "rbac", "parent_slug": None, "synonyms": ["role-based access control"]},
            {"slug": "api_access", "parent_slug": None, "synonyms": ["api"]},
            {"slug": "uptime_sla", "parent_slug": None, "synonyms": ["sla", "uptime"]},
            {"slug": "pricing", "parent_slug": None, "synonyms": ["plan", "cost"]},
            {"slug": "integration", "parent_slug": None, "synonyms": ["connector"]},
            {"slug": "onboarding", "parent_slug": None, "synonyms": ["kickoff", "go-live"]},
            {"slug": "seats", "parent_slug": None, "synonyms": ["licenses", "users"]},
            {"slug": "incident", "parent_slug": None, "synonyms": ["outage", "postmortem"]},
        ],
    )


def downgrade() -> None:
    op.drop_table("eval_results")
    op.drop_table("eval_cases")
    op.drop_index("ix_processing_jobs_claim", table_name="processing_jobs")
    op.drop_table("processing_jobs")
    op.drop_table("reviews")
    op.drop_table("context_gaps")
    op.drop_table("handoff_validations")
    op.drop_table("context_contracts")
    op.drop_table("capability_vocab")
    op.drop_table("context_relations")
    op.drop_table("context_versions")
    op.drop_table("context_objects")
    op.drop_table("sources")
    op.drop_table("entity_aliases")
    op.drop_table("org_domains")
    op.drop_table("users")
    op.drop_table("people")
    op.drop_table("entities")
