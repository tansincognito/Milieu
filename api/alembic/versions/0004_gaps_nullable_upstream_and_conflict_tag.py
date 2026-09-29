"""Day 3 handoff validator (§10) support columns on `context_gaps`.

- `upstream_id` becomes nullable: `present`-check gaps (§9's `has_derived_from` /
  `has_customer_contact` / acceptance-criteria rules) have no natural upstream object to
  compare against — the violation is entirely about the downstream object itself. The
  `propagate`-check path (the majority of gaps) still always sets `upstream_id`.
- `upstream_conflict` (bool): §10.1 step 1 — "When a subject group has an unresolved
  conflict, the highest-authority object is the reference. The gap is tagged
  `upstream_conflict` so the report shows that the error started inside the upstream
  stage." No column existed to carry this tag.
- an `outcome` check constraint: the 0001 schema left `outcome` as a free string; §10.1
  defines exactly seven values, so enforce them the same way every other enum in this
  schema is enforced (CHECK, not a native Postgres ENUM, per the ORM module docstring).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

OUTCOMES = (
    "'preserved', 'equivalent', 'generalized', 'missing', 'contradicted', "
    "'object_missing', 'stale_reference'"
)


def upgrade() -> None:
    op.alter_column("context_gaps", "upstream_id", nullable=True)
    op.add_column(
        "context_gaps",
        sa.Column("upstream_conflict", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_check_constraint(
        "ck_context_gaps_outcome", "context_gaps", f"outcome IN ({OUTCOMES})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_context_gaps_outcome", "context_gaps", type_="check")
    op.drop_column("context_gaps", "upstream_conflict")
    op.alter_column("context_gaps", "upstream_id", nullable=False)
