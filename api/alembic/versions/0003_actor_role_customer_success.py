"""Add `customer_success` to the actor_role check constraint.

SPEC.md §4.3 and change C18 both list `customer_success` as a valid `actor_role`, but the
0001 constraint omitted it. Extraction correctly labels CS actors on the Globex onboarding
scenario, and every one of those rows died on the check constraint and poisoned its job.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

OLD = "'customer', 'sales', 'product', 'engineering', 'other', 'system'"
NEW = "'customer', 'sales', 'product', 'engineering', 'customer_success', 'other', 'system'"


def upgrade() -> None:
    op.drop_constraint("ck_context_objects_actor_role", "context_objects", type_="check")
    op.create_check_constraint(
        "ck_context_objects_actor_role", "context_objects", f"actor_role IN ({NEW})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_context_objects_actor_role", "context_objects", type_="check")
    op.create_check_constraint(
        "ck_context_objects_actor_role", "context_objects", f"actor_role IN ({OLD})"
    )
