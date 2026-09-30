"""First-class `incidents` table, replacing the subject_key-pattern heuristic.

Two gaps this closes, both already named as needing a real schema change:

1. I7 (Golden 3, §19.1): "the incident links to BOTH Acme and Globex... Globex shows it in
   the Explorer even though no Globex email was sent." `context_objects.entity_id` is a
   single FK -- one object cannot belong to two customers. `incident_entities` is a proper
   many-to-many, so one incident can span customers the way a real outage does.
2. Cross-source correlation. Before this, "everything about INC-2311" was reconstructed at
   read time from a subject_key pattern (`%:incident%`) plus a 72h time-window guess around
   the one object that happened to carry a structured `incident_id` --
   `api/app/api/incidents.py`'s own module docstring called this out as unreliable beyond a
   single-incident demo. `incident_context_objects` makes the link a real, durable row
   instead of a heuristic recomputed on every request -- `linked=true` for a confirmed tie,
   `linked=false` for a candidate the old time-window heuristic would have suggested,
   preserving the same linked/unconfirmed distinction the pack already showed, but now
   reviewable and queryable rather than runtime-only.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

SEVERITIES = "'P0', 'P1', 'P2'"
STATUSES = "'open', 'resolved'"


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("incident_id", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("severity", sa.String, nullable=False, server_default="P1"),
        sa.Column("status", sa.String, nullable=False, server_default="open"),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "declared_by", pg.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "incident_id", name="uq_incidents_tenant_incident_id"),
        sa.CheckConstraint(f"severity IN ({SEVERITIES})", name="ck_incidents_severity"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_incidents_status"),
    )

    op.create_table(
        "incident_entities",
        sa.Column(
            "incident_id", pg.UUID(as_uuid=True), sa.ForeignKey("incidents.id"), primary_key=True
        ),
        sa.Column(
            "entity_id", pg.UUID(as_uuid=True), sa.ForeignKey("entities.id"), primary_key=True
        ),
    )

    op.create_table(
        "incident_context_objects",
        sa.Column(
            "incident_id", pg.UUID(as_uuid=True), sa.ForeignKey("incidents.id"), primary_key=True
        ),
        sa.Column(
            "context_object_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("context_objects.id"),
            primary_key=True,
        ),
        # true: a confirmed tie (the object carries the real incident_id, or a human/
        # reviewer confirmed it). false: a candidate the correlation heuristic suggested --
        # same capability, inside the incident's time window -- not yet confirmed. Mirrors
        # entity_aliases.status's confirmed/candidate pattern (§7.1).
        sa.Column("linked", sa.Boolean, nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_table("incident_context_objects")
    op.drop_table("incident_entities")
    op.drop_table("incidents")
