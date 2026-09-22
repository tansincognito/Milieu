"""Authority assignment (§5). Confidence and authority are never combined into one score."""

from __future__ import annotations


def assign_authority(
    *,
    type_: str,
    actor_role: str,
    stage: str | None,
    speculative: bool,
) -> int:
    """§5 table. `speculative` covers "SALES hedged proposals" (authority 1) — the
    extractor is the only stage that reads the hedging language (see
    `ExtractedContext.speculative` docstring)."""
    if actor_role == "customer":
        return 4
    if actor_role == "product" and type_ == "decision":
        # "Every team member is accountable, so there is no approver list."
        return 4
    if speculative:
        return 1
    if actor_role == "sales":
        # Sales owns `commitment`s (its own promises); everything else is sales
        # restating what the customer/product said, one level down.
        return 3 if type_ == "commitment" else 2
    if actor_role in ("product", "engineering"):
        return 3
    if actor_role == "other" and stage is not None:
        # actor_role has no `customer_success` value (§4.3 gap, see
        # directory/resolve.py) — treat a same-stage internal statement as the
        # "owning function inside its own stage" case.
        return 3
    return 0
