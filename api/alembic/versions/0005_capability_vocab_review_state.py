"""Capability vocabulary review state + baseline seed.

Two problems this fixes, found while investigating subject_key instability (the same
document's isolated sections sometimes classifying under different capabilities across
otherwise-identical runs):

1. `capability_vocab` was never seeded. `process.py` only ever inserted a row reactively,
   the first time it saw a given `subject_capability` string — which meant EVERY object's
   capability was "new" on a cold tenant, even the ones in `prompts.py`'s own
   `CAPABILITY_VOCAB` hint list, silently routing nearly everything to `candidate` review on
   first ingest regardless of how well the model actually classified it. This migration
   seeds that list as `confirmed` rows, so the review queue reflects genuinely novel
   capabilities, not "first time we happened to see this word".
2. There was no way to tell a reviewed, trustworthy slug apart from an unreviewed guess —
   `capability_resolution.py`'s canonicalizer needs that distinction to know which rows it
   is allowed to match new proposals against. `status` supplies it (same two-state pattern
   as `entity_aliases.status`, §7.1). `suggested_parent_slug` carries a near-miss match the
   resolver found but was not confident enough to apply automatically, for a reviewer to
   accept or reject.

Every row this migration finds already in the table — on any DB that ran before it, not
just a fresh one — defaults to `candidate`, not `confirmed`. Checked live against this
repo's own dev database before writing it this way: it already held rows like
`requirements_aggregation`, `internal_coordination`, and even a literal `null`/empty-string
slug — exactly the kind of unreviewed noise this migration exists to stop treating as
trustworthy. Blanket-confirming everything already present would launder that noise into
the vocabulary the resolver trusts, the opposite of the point. Only the explicit
`BASELINE_CAPABILITIES` list below is confirmed; if a slug in that list already existed as a
stray row, it is promoted; everything else present keeps `candidate` and is left for a
reviewer, same as it should have been extracted as in the first place.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# The hint list `prompts.py` has shown the model since Day 1. Seeding these as `confirmed`
# is what stops "sso" itself from being treated as a novel capability on a cold tenant.
BASELINE_CAPABILITIES = (
    "sso",
    "scim",
    "audit_logs",
    "data_residency",
    "rbac",
    "api_access",
    "uptime_sla",
    "pricing",
    "integration",
    "onboarding",
    "seats",
    "incident",
)

def upgrade() -> None:
    op.add_column(
        "capability_vocab",
        sa.Column("status", sa.String, nullable=False, server_default="candidate"),
    )
    op.add_column(
        "capability_vocab",
        sa.Column(
            "suggested_parent_slug",
            sa.String,
            sa.ForeignKey("capability_vocab.slug"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_capability_vocab_status", "capability_vocab", "status IN ('confirmed', 'candidate')"
    )

    # `server_default='candidate'` on the ADD COLUMN above already backfilled every
    # pre-existing row to 'candidate' -- correct, since none of them were ever reviewed.
    # Only the named baseline gets promoted, and only that exact list: ON CONFLICT ...
    # DO UPDATE so a slug that already existed as a stray candidate row (e.g. "sso" from an
    # earlier reactive insert) is confirmed, while every other pre-existing row (the actual
    # noise) is left as 'candidate' for a human to clean up.
    conn = op.get_bind()
    for slug in BASELINE_CAPABILITIES:
        conn.execute(
            sa.text(
                """
                INSERT INTO capability_vocab (slug, parent_slug, synonyms, status, suggested_parent_slug)
                VALUES (:slug, NULL, '{}', 'confirmed', NULL)
                ON CONFLICT (slug) DO UPDATE SET status = 'confirmed'
                """
            ),
            {"slug": slug},
        )


def downgrade() -> None:
    op.drop_constraint("ck_capability_vocab_status", "capability_vocab", type_="check")
    op.drop_column("capability_vocab", "suggested_parent_slug")
    op.drop_column("capability_vocab", "status")
