#!/usr/bin/env python3
"""One-off (re-runnable) correction for `contradicts` relations created before the
`slots_compatible` fix (2026-10-01, see `app.pipeline.dedup`): `due_date` at matching
"quarter"/"month" precision compared by literal value instead of period, and free-text
slots (`rationale`, `root_cause`, `remediation`) compared by literal value at all. Both
could flag two restatements of the *same* fact as an incompatible slot, routing them into
R4 ("conflicting") instead of dedup -- the dashboard's "Contradictions" widget then showed
what looked like duplicated noise next to genuine contradictions.

For every existing `system:lifecycle:*` `contradicts` relation whose pair is now
slots-compatible under the fixed logic:
  - cosine >= REVIEW_DUPLICATE_THRESHOLD: really a duplicate pair. Runs the same
    `apply_dedup` the pipeline would have run, and reverts both objects' status back to
    `active` via the normal `_transition` mechanism (versioned, audited -- nothing is
    skipped or deleted).
  - otherwise: two genuinely distinct, non-conflicting facts. Reverts both back to `active`
    with no new relation (that's what two independently-active siblings looks like).

Either way the original `contradicts` relation is kept (nothing is ever deleted, §7.3) but
its `resolved_at` is stamped to mark it superseded by this correction, instead of sitting
forever as an unresolved relation pending human review it was never a real candidate for.

Usage: uv run python ../scripts/reconcile_false_contradictions.py [--tenant-id UUID] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.models.orm import ContextObjects, ContextRelations  # noqa: E402
from app.pipeline.dedup import (  # noqa: E402
    REVIEW_DUPLICATE_THRESHOLD,
    apply_dedup,
    cosine_similarity,
    slots_compatible,
)
from app.pipeline.lifecycle import _transition  # noqa: E402

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    tenant_id = uuid.UUID(args.tenant_id)

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = session_factory()
    now = datetime.now(UTC)

    relations = (
        db.query(ContextRelations)
        .filter(
            ContextRelations.relation == "contradicts",
            ContextRelations.created_by.like("system:lifecycle:%"),
            ContextRelations.resolved_at.is_(None),
        )
        .all()
    )

    fixed_duplicates = 0
    fixed_independent = 0
    for rel in relations:
        a = db.get(ContextObjects, rel.from_id)
        b = db.get(ContextObjects, rel.to_id)
        if a is None or b is None or a.tenant_id != tenant_id:
            continue
        if not slots_compatible(a.attributes or {}, b.attributes or {}):
            continue  # a genuine conflict under the fixed logic too -- leave it alone

        cosine = cosine_similarity(a.embedding, b.embedding)
        print(f"reconciling {a.id} <-> {b.id} (cosine={cosine:.3f})")
        print(f"  a: {a.content}")
        print(f"  b: {b.content}")

        if args.dry_run:
            continue

        if cosine >= REVIEW_DUPLICATE_THRESHOLD:
            apply_dedup(db, a, b, cosine)
            fixed_duplicates += 1
        else:
            fixed_independent += 1

        if a.status == "conflicting":
            _transition(db, a, "active", "reconcile_false_contradictions: slots_compatible fix", now)
        if b.status == "conflicting":
            _transition(db, b, "active", "reconcile_false_contradictions: slots_compatible fix", now)
        rel.resolved_at = now

    if args.dry_run:
        print(f"\n(dry run) would fix {fixed_duplicates + fixed_independent} relation(s)")
    else:
        db.commit()
        print(
            f"\nfixed {fixed_duplicates} duplicate pair(s), "
            f"{fixed_independent} independent-fact pair(s)"
        )
    db.close()


if __name__ == "__main__":
    main()
