"""Capability canonicalization — the fix for subject_key instability.

`subject_key` (§4.2) is `{entity_slug}:{capability}`, and until now `capability` was
whatever free-text slug the model happened to propose in `ExtractedContext.subject_capability`
(`api/app/schemas/extraction.py`), taken as-is. `prompts.py`'s `CAPABILITY_VOCAB` is only a
hint shown in the prompt text — nothing enforced it, so the same underlying topic could (and
in live probing, did) come back as `incident`, `uptime_sla`, and `api_access` across
otherwise-identical runs of an isolated document section. Each distinct string produces a
distinct `subject_key`, so instability here doesn't just mislabel an object — it splits one
topic's history across several ledgers, which is the ledger's entire reliability guarantee
failing silently.

This module does for capabilities what `entity_resolution.py` already does for entity names:
the model's raw text is a *hint*, not the identity. A hint is matched against confirmed
vocabulary (`capability_vocab.status = 'confirmed'`) — by exact string, then by `pg_trgm`
similarity against both the slug and its confirmed synonyms — and only a confirmed match's
`slug` is ever used to build a `subject_key`.

Unlike entity resolution, a near-miss (the candidate band) does NOT auto-create a new
canonical row the way a new entity does. An entity mismatch is cheap to recover from: a
duplicate entity row still resolves every fact to itself and just waits for a human merge.
A wrong capability is not cheap: it IS the subject_key, so two objects about the same real
topic under different slugs will never dedupe, conflict-check, or lineage-link against each
other until someone notices and migrates the data. So below the auto-link threshold this
resolver is conservative: it proposes a new `candidate` vocab row (routed to review, same as
before) and, if there was a near-miss, attaches it as an unconfirmed `suggested_parent_slug`
for a human to accept or reject — it never guesses on the write path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.orm import CapabilityVocab

AUTO_LINK_THRESHOLD = 0.85
CANDIDATE_THRESHOLD = 0.6


@dataclass(frozen=True)
class CapabilityResolution:
    slug: str
    is_new: bool
    # True when this proposal matched an existing confirmed slug/synonym above the
    # auto-link threshold and was snapped to it, rather than being an exact hit already.
    snapped: bool = False
    # An unconfirmed near-match a reviewer should look at (candidate band only).
    suggested_match: str | None = None


def normalize_capability(raw: str) -> str:
    """lowercase, non-alphanumeric runs -> single underscore, trimmed.

    "Audit Log Retention" and "audit-log-retention" and "audit_log_retention " all normalize
    identically, so even two *new* proposals for the same real topic land on one candidate
    row instead of three, without needing a trgm match to get there.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    return slug or "capability"


def _exact_match(db: Session, normalized: str) -> str | None:
    """A confirmed row whose slug or synonym literally equals `normalized`."""
    row = db.execute(
        text(
            """
            SELECT slug FROM capability_vocab
            WHERE status = 'confirmed' AND (slug = :n OR :n = ANY(synonyms))
            LIMIT 1
            """
        ),
        {"n": normalized},
    ).first()
    return row.slug if row else None


def _best_trgm_match(db: Session, normalized: str) -> tuple[str, float] | None:
    """Best confirmed slug by trigram similarity against either the slug itself or any of
    its synonyms, or None if nothing scores at or above `CANDIDATE_THRESHOLD`.

    `similarity()` is called directly (not the `%` operator) so the threshold isn't at the
    mercy of the session's `pg_trgm.similarity_threshold` GUC — same reasoning as
    `entity_resolution._best_trgm_match`.
    """
    row = db.execute(
        text(
            """
            SELECT slug, GREATEST(
                similarity(slug, :n),
                COALESCE((SELECT MAX(similarity(syn, :n)) FROM unnest(synonyms) AS syn), 0)
            ) AS sim
            FROM capability_vocab
            WHERE status = 'confirmed'
            ORDER BY sim DESC
            LIMIT 1
            """
        ),
        {"n": normalized},
    ).first()
    if row is None or row.sim < CANDIDATE_THRESHOLD:
        return None
    return row.slug, float(row.sim)


def resolve_capability(db: Session, proposed: str) -> CapabilityResolution:
    """Turn a model-proposed capability string into the slug a `subject_key` may use.

    Always call this instead of using `ExtractedContext.subject_capability` directly to
    build a `subject_key` — see module docstring for why the raw string is unsafe.
    """
    normalized = normalize_capability(proposed)

    exact = _exact_match(db, normalized)
    if exact is not None:
        return CapabilityResolution(slug=exact, is_new=False)

    match = _best_trgm_match(db, normalized)

    if match is not None and match[1] >= AUTO_LINK_THRESHOLD:
        canonical_slug, _sim = match
        _add_synonym(db, canonical_slug, normalized)
        return CapabilityResolution(slug=canonical_slug, is_new=False, snapped=True)

    # Candidate band or no match: genuinely new to this resolver. The row is created as
    # 'candidate' — unreviewed — with the near-miss (if any) recorded for a human, never
    # auto-applied. `is_new=True` is what routes the object itself to review in process.py.
    suggested = match[0] if match is not None else None
    _create_candidate(db, normalized, suggested_parent_slug=suggested)
    return CapabilityResolution(slug=normalized, is_new=True, suggested_match=suggested)


def _add_synonym(db: Session, canonical_slug: str, new_synonym: str) -> None:
    """Record `new_synonym` on the matched row (if not already present) so the *next*
    identical proposal hits the exact-match path instead of paying for a trgm scan again —
    the vocabulary tightens itself with use instead of drifting wider."""
    row = db.get(CapabilityVocab, canonical_slug)
    if row is not None and new_synonym not in row.synonyms and new_synonym != canonical_slug:
        row.synonyms = [*row.synonyms, new_synonym]
        db.add(row)
        db.flush()


def _create_candidate(db: Session, slug: str, suggested_parent_slug: str | None) -> None:
    existing = db.get(CapabilityVocab, slug)
    if existing is not None:
        # Someone else in this same batch already proposed this exact normalized slug —
        # leave its existing row (and whatever suggestion it already carries) alone.
        return
    db.add(
        CapabilityVocab(
            slug=slug,
            parent_slug=None,
            synonyms=[],
            status="candidate",
            suggested_parent_slug=suggested_parent_slug,
        )
    )
    db.flush()
