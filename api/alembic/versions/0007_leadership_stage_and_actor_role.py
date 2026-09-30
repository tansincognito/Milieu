"""Add `leadership` as a stage and actor_role.

Needed to author the CEO/CPO/CRO workflow content (pasted workflow table: "CPO -> Product ->
Sales -> Customer", "CRO -> Sales -> Customer", "CEO -> Leadership -> Product/Engineering").
In every one of those, the C-suite role is the ORIGIN of a handoff into an existing
functional team, not a synonym for that team -- a CPO's directive is leadership->product,
not already "product". Without a `leadership` value, every message from Morgan Ellis
(CEO), Jordan Vega (CPO) or Riley Chen (CRO) would resolve through
`app/directory/resolve.py`'s TEAM_TO_STAGE/TEAM_TO_ACTOR_ROLE with no match, landing on
`stage=None, actor_role="other"` -- invisible to handoff validation, which defeats the
entire point of authoring content to test these flows.

Same small, additive pattern as migration 0003 (which added `customer_success` for the
identical reason). This is NOT the configurable-departments overhaul in
ARCHITECTURE-v2.md §7.1 -- that replaces the fixed CHECK constraint with a per-tenant table;
this just adds one more fixed value to it, same as 0003 did.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-01
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

OLD_STAGES = "'sales', 'product', 'engineering', 'customer_success'"
NEW_STAGES = "'sales', 'product', 'engineering', 'customer_success', 'leadership'"

OLD_ACTOR_ROLES = "'customer', 'sales', 'product', 'engineering', 'customer_success', 'other', 'system'"
NEW_ACTOR_ROLES = (
    "'customer', 'sales', 'product', 'engineering', 'customer_success', 'leadership', "
    "'other', 'system'"
)


def upgrade() -> None:
    op.drop_constraint("ck_sources_stage", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_stage", "sources", f"stage IS NULL OR stage IN ({NEW_STAGES})"
    )

    op.drop_constraint("ck_context_objects_stage", "context_objects", type_="check")
    op.create_check_constraint(
        "ck_context_objects_stage", "context_objects", f"stage IS NULL OR stage IN ({NEW_STAGES})"
    )

    op.drop_constraint("ck_context_objects_actor_role", "context_objects", type_="check")
    op.create_check_constraint(
        "ck_context_objects_actor_role", "context_objects", f"actor_role IN ({NEW_ACTOR_ROLES})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_context_objects_actor_role", "context_objects", type_="check")
    op.create_check_constraint(
        "ck_context_objects_actor_role", "context_objects", f"actor_role IN ({OLD_ACTOR_ROLES})"
    )

    op.drop_constraint("ck_context_objects_stage", "context_objects", type_="check")
    op.create_check_constraint(
        "ck_context_objects_stage", "context_objects", f"stage IS NULL OR stage IN ({OLD_STAGES})"
    )

    op.drop_constraint("ck_sources_stage", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_stage", "sources", f"stage IS NULL OR stage IN ({OLD_STAGES})"
    )
