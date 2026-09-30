"""`sync_runs` — one row per simulated sync attempt on a connection.

Architecture v2 §5 named this table; migration 0006 shipped `connections` and
`simulation_seed_sources` but not this one. Needed now to make "authentication states,
failures, rate limits" an observable, queryable mechanism (POST /connections/{kind}/sync)
instead of just static fields on `connections`.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SYNC_RUN_STATUSES = "'ok', 'partial', 'rate_limited', 'auth_expired', 'failed'"


def upgrade() -> None:
    op.create_table(
        "sync_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("connections.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("items_seen", sa.Integer, nullable=False, server_default="0"),
        sa.Column("items_ingested", sa.Integer, nullable=False, server_default="0"),
        sa.Column("items_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.String, nullable=True),
        sa.CheckConstraint(f"status IN ({SYNC_RUN_STATUSES})", name="ck_sync_runs_status"),
    )


def downgrade() -> None:
    op.drop_table("sync_runs")
