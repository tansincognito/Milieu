"""Day 2a (§7.1, §7.3): author identity for the R3 "same author" check, and a review-status
column on entity_aliases so entity resolution's 0.6-0.85 candidate band (§7.1 step 4) has
somewhere durable to live without inventing a whole new table for one nullable flag.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # §7.3 R3: "same author" means the same people.id, resolved through the directory.
    # Nullable because drive sources currently carry no author identity (§6.2 documented
    # gap — see process.py's `_resolve_actor` docstring) and call transcripts label roles,
    # not individuals.
    op.add_column(
        "context_objects",
        sa.Column(
            "actor_person_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
    )

    # §7.1 step 4: a 0.6-0.85 trgm match creates a "candidate link... routed to review"
    # instead of an auto-confirmed alias. `status` distinguishes the two; `similarity`
    # records the score that triggered a candidate row, for the reviewer's benefit.
    op.add_column(
        "entity_aliases",
        sa.Column("status", sa.String, nullable=False, server_default="confirmed"),
    )
    op.add_column(
        "entity_aliases", sa.Column("similarity", sa.Numeric(4, 3), nullable=True)
    )
    op.create_check_constraint(
        "ck_entity_aliases_status", "entity_aliases", "status IN ('confirmed', 'candidate')"
    )

    # A candidate row and a later-confirmed row may legitimately share one
    # (tenant_id, alias_normalized) pair (the candidate suggests a merge with an existing
    # entity; the hint itself still gets its own confirmed alias on a new entity so the same
    # hint resolves instantly next time — see entity_resolution.py). Uniqueness only needs
    # to hold among confirmed aliases.
    op.drop_constraint("uq_alias_tenant_normalized", "entity_aliases", type_="unique")
    op.create_index(
        "uq_alias_tenant_normalized_confirmed",
        "entity_aliases",
        ["tenant_id", "alias_normalized"],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed'"),
    )


def downgrade() -> None:
    op.drop_index("uq_alias_tenant_normalized_confirmed", table_name="entity_aliases")
    op.create_unique_constraint(
        "uq_alias_tenant_normalized", "entity_aliases", ["tenant_id", "alias_normalized"]
    )
    op.drop_constraint("ck_entity_aliases_status", "entity_aliases", type_="check")
    op.drop_column("entity_aliases", "similarity")
    op.drop_column("entity_aliases", "status")
    op.drop_column("context_objects", "actor_person_id")
