"""Tenants, connections, and simulation-seed-sources.

Foundation for two things asked for together: an org setup screen with real source
checkboxes, and a simulation/production mode toggle where simulation mode reads seed data
from Postgres rather than flat files, "mimicking real APIs" so a real connector can drop in
later without changing the shape anything downstream expects.

Purely additive -- no existing table changes, nothing here is read by any connector or
pipeline code yet (see `Connections`/`SimulationSeedSources` docstrings in orm.py), so this
cannot regress anything already passing.

Seeds one `tenants` row matching the UUID `Settings.tenant_id` has always hardcoded
(`00000000-0000-0000-0000-000000000001`) plus one for the eval harness's dedicated tenant
(`eeeeeeee-...`), both `mode='simulation'` -- every tenant this codebase has ever used
already only runs against seed data, so this makes that fact explicit instead of implicit.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TENANT_MODES = "'simulation', 'production'"
CONNECTION_KINDS = "'slack', 'email', 'drive', 'call', 'directory'"
CONNECTION_PROVIDERS = "'simulation', 'google', 'microsoft', 'okta', 'slack_api'"
CONNECTION_STATUSES = "'disconnected', 'pending', 'connected', 'error'"

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
EVAL_TENANT_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("mode", sa.String, nullable=False, server_default="simulation"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(f"mode IN ({TENANT_MODES})", name="ck_tenants_mode"),
    )

    op.create_table(
        "connections",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", pg.UUID(as_uuid=True), nullable=False, index=True
        ),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("provider", sa.String, nullable=False, server_default="simulation"),
        sa.Column("status", sa.String, nullable=False, server_default="disconnected"),
        sa.Column("external_account", sa.String, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "kind", name="uq_connections_tenant_kind"),
        sa.CheckConstraint(f"kind IN ({CONNECTION_KINDS})", name="ck_connections_kind"),
        sa.CheckConstraint(
            f"provider IN ({CONNECTION_PROVIDERS})", name="ck_connections_provider"
        ),
        sa.CheckConstraint(f"status IN ({CONNECTION_STATUSES})", name="ck_connections_status"),
    )

    op.create_table(
        "simulation_seed_sources",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", pg.UUID(as_uuid=True), nullable=False, index=True
        ),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("external_id", sa.String, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("source_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "kind", "external_id", name="uq_simulation_seed_tenant_kind_external"
        ),
        sa.CheckConstraint(f"kind IN ({CONNECTION_KINDS})", name="ck_simulation_seed_kind"),
    )

    conn = op.get_bind()
    for tenant_id, name in ((DEFAULT_TENANT_ID, "Default"), (EVAL_TENANT_ID, "Eval")):
        conn.execute(
            sa.text(
                "INSERT INTO tenants (id, name, mode) VALUES (:id, :name, 'simulation') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": tenant_id, "name": name},
        )


def downgrade() -> None:
    op.drop_table("simulation_seed_sources")
    op.drop_table("connections")
    op.drop_table("tenants")
