"""Extraction job pipeline (§13.1): extract -> validate -> evidence-span check -> resolve
entity -> assign authority/confidence -> embed -> persist context_objects + a
context_versions row -> dedup/lifecycle/supersession/conflict (§7.2, §7.3) -> lineage (§8).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from psycopg.types.range import Range
from redis import Redis
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.directory.resolve import (
    PeopleDirectory,
    SqlAlchemyPeopleDirectory,
    actor_role_from_speaker_label,
    resolve_person,
)
from app.embedding.base import EmbeddingClient
from app.llm.base import LLMClient
from app.models.orm import CapabilityVocab, ContextObjects, ContextVersions, Entities, Sources
from app.pipeline.authority import assign_authority
from app.pipeline.entity_resolution import resolve_entity
from app.pipeline.evidence import EvidenceSpanError, compute_evidence_span
from app.pipeline.extraction_cache import (
    extraction_cache_key,
    get_cached_extraction,
    set_cached_extraction,
)
from app.pipeline.lifecycle import resolve_object_state
from app.pipeline.lineage import create_lineage_links
from app.pipeline.prompts import PROMPT_VERSION, build_extraction_prompt
from app.schemas.extraction import ContextAttributes, ExtractedContext, ExtractionResult

logger = logging.getLogger(__name__)


class SourceNotFoundError(RuntimeError):
    pass


def _resolve_actor(
    source: Sources, item: ExtractedContext, directory: PeopleDirectory, tenant_id: uuid.UUID
) -> tuple[str, uuid.UUID | None]:
    """Returns (actor_role, actor_person_id). `actor_person_id` (§7.3 R3 "same author") is
    only resolvable when the source carries a real identity — email/Slack authors resolve
    through the directory; call transcripts label roles, not individuals, and drive's
    DriveProvenance carries no author identity at all (documented gap, see below)."""
    if source.kind == "call":
        return actor_role_from_speaker_label(item.actor_label), None
    if source.kind == "email":
        from_email = source.provenance.get("from")
        resolution = resolve_person(directory, tenant_id, email=from_email)
        return resolution.actor_role, resolution.person_id
    if source.kind == "slack":
        author_id = source.provenance.get("author_id")
        resolution = resolve_person(directory, tenant_id, slack_user_id=author_id)
        return resolution.actor_role, resolution.person_id
    # drive: §6.2's DriveProvenance carries no author identity, so per-item actor_role
    # can't be resolved through the directory. Fall back to the source's own stage as a
    # proxy for "owning function" — documented gap, flagged in the dispatch report.
    return source.stage or "other", None


def _build_subject_key(entity_slug: str, capability: str, attributes: ContextAttributes) -> str:
    incident_id = attributes.extra.get("incident_id") if attributes.extra else None
    if capability == "incident" and incident_id:
        return f"{entity_slug}:incident:{incident_id}"
    return f"{entity_slug}:{capability}"


def _determine_status(confidence: float, is_new_capability: bool) -> str:
    """§5 review routing (partial for Day 1 — the `authority <= 1 and critical contract
    field` rule needs contracts, which are Day 3 scope)."""
    if confidence < 0.6:
        return "candidate"
    if is_new_capability:
        return "candidate"
    return "active"


def _sibling_subject_key(db: Session, tenant_id: uuid.UUID, source: Sources) -> str | None:
    """The `subject_key` (§4.2) an earlier-processed section of the *same* parent document
    already established, if any.

    Drive documents are split on markdown headings (§6.2) and each section is extracted as
    its own source record/job, so a later section only ever sees its own paragraph. A
    paragraph like "## Remediation" reads as generic work in isolation and can misroute to
    a different capability than the rest of the document (e.g. `uptime_sla` instead of
    `incident`) even though §11's incident-vs-uptime_sla rule is spelled out, because the
    paragraph itself doesn't repeat the outage language that made earlier sections resolve
    correctly. Once a sibling section of the same document has already been persisted under
    a subject_key, later sections are told to bind to that same subject instead of
    re-deriving capability from a weaker, standalone paragraph.

    Only **instance** subjects (`{entity_slug}:incident:{incident_id}`, §4.2) bind this way.
    A postmortem genuinely is all one incident, so every section belongs to that subject. A
    capability-level subject must NOT bind: an ordinary document routinely covers several
    capabilities — `product/acme-prd.md` has a "Requirements" section about SSO and a
    "Provisioning" section about SCIM — and binding there would collapse `acme:scim` into
    whichever subject happened to be persisted first.
    """
    document_id = (source.provenance or {}).get("document_id")
    if source.kind != "drive" or not document_id:
        return None
    row = (
        db.query(ContextObjects.subject_key)
        .join(Sources, ContextObjects.source_id == Sources.id)
        .filter(
            Sources.tenant_id == tenant_id,
            Sources.kind == "drive",
            Sources.provenance["document_id"].astext == document_id,
            Sources.id != source.id,
            ContextObjects.subject_key.like("%:incident:%"),
        )
        .order_by(ContextObjects.created_at.asc())
        .first()
    )
    return row[0] if row else None


def _established_subject_hint(subject_key: str) -> str:
    """Turn a persisted `subject_key` into the phrasing the prompt's binding rule expects
    (§4.2: `{entity_slug}:{capability}` or `{entity_slug}:incident:{incident_id}`)."""
    parts = subject_key.split(":", 2)
    if len(parts) == 3 and parts[1] == "incident":
        return (
            f'subject_capability="incident", the same incident '
            f'(attributes.extra.incident_id="{parts[2]}")'
        )
    if len(parts) >= 2:
        return f'subject_capability="{parts[1]}"'
    return subject_key


def _document_context(source: Sources, established_subject_key: str | None = None) -> str | None:
    """A one-line description of the parent artifact a source record came from.

    Sections and single messages are extracted in isolation, so without this the model
    cannot infer the capability or entity a fragment belongs to. `established_subject_key`
    (see `_sibling_subject_key`) additionally anchors later sections of a split document to
    the subject a sibling section already established, rather than letting each section
    re-derive capability from its own, possibly weaker, text.
    """
    prov = source.provenance or {}
    if source.kind == "drive":
        path = prov.get("path")
        heading = prov.get("section_heading")
        if path and heading:
            base = f'the document "{path}", section "{heading}"'
        elif path:
            base = f'the document "{path}"'
        else:
            return None
        if established_subject_key:
            base += (
                f". Another section of this same document has already been extracted and "
                f"established {_established_subject_hint(established_subject_key)} — use "
                f"that same subject for every item in this section too, even if this "
                f"section's own text read alone would suggest something else."
            )
        return base
    if source.kind == "email":
        subject = prov.get("subject")
        return f'the email thread "{subject}"' if subject else None
    if source.kind == "call":
        call_id = prov.get("call_id")
        return f"the call transcript {call_id}" if call_id else None
    return None


def source_cache_key(
    settings: Settings, source: Sources, sibling_subject_key: str | None = None
) -> str:
    """The §13.3 extraction-cache key for one source under the current prompt and model."""
    return extraction_cache_key(
        source.content_hash,
        PROMPT_VERSION,
        settings.llm_model,
        settings.schema_version,
        sibling_subject_key or "",
    )


def extract_source(
    redis_client: Redis,
    llm: LLMClient,
    settings: Settings,
    source: Sources,
    sibling_subject_key: str | None = None,
) -> ExtractionResult:
    """The LLM half of the pipeline for one source: cache lookup, prompt, extract, store.

    Split out of `process_extraction_job` because it is the only slow step and the only one
    that is **order-independent**. Everything after it — dedup, R1-R4, lineage (§7.2, §7.3,
    §8) — compares a new object against whatever is currently `active`, so it is strictly
    order-dependent and must stay sequential. Callers that need throughput can therefore run
    this concurrently over many sources to populate the cache, then replay
    `process_extraction_job` in the required order, where every call is a cache hit. It
    touches no `Session`, so it is safe to call from a worker thread.
    """
    cache_key = source_cache_key(settings, source, sibling_subject_key)
    cached = get_cached_extraction(redis_client, cache_key)
    if cached is not None:
        return cached

    system = build_extraction_prompt(
        source.kind,
        source.stage,
        source.source_ts,
        _document_context(source, sibling_subject_key),
    )
    result = llm.extract(ExtractionResult, system, source.text)
    set_cached_extraction(redis_client, cache_key, result)
    return result


def process_extraction_job(
    db: Session,
    redis_client: Redis,
    llm: LLMClient,
    embedder: EmbeddingClient,
    settings: Settings,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
) -> int:
    source = db.get(Sources, source_id)
    if source is None:
        raise SourceNotFoundError(str(source_id))

    # The sibling hint has to be resolved *before* the cache key, because it is part of the
    # prompt and therefore part of what the cached result represents.
    sibling_subject_key = _sibling_subject_key(db, tenant_id, source)
    cache_key = source_cache_key(settings, source, sibling_subject_key)
    result = extract_source(
        redis_client, llm, settings, source, sibling_subject_key=sibling_subject_key
    )

    directory = SqlAlchemyPeopleDirectory(db)

    accepted: list[
        tuple[ExtractedContext, tuple[int, int], str, uuid.UUID | None, uuid.UUID, int, bool]
    ] = []
    for item in result.items:
        try:
            span = compute_evidence_span(source.text, item.evidence_quote)
        except EvidenceSpanError:
            logger.warning(
                "rejecting extracted object: evidence_quote not found in source %s", source_id
            )
            continue

        actor_role, actor_person_id = _resolve_actor(source, item, directory, tenant_id)
        entity_id = resolve_entity(db, tenant_id, item.entity_hint)
        authority = assign_authority(
            type_=item.type, actor_role=actor_role, stage=source.stage, speculative=item.speculative
        )

        vocab_row = db.get(CapabilityVocab, item.subject_capability)
        is_new_capability = vocab_row is None
        if is_new_capability:
            db.add(CapabilityVocab(slug=item.subject_capability, parent_slug=None, synonyms=[]))
            db.flush()

        accepted.append(
            (item, span, actor_role, actor_person_id, entity_id, authority, is_new_capability)
        )

    if not accepted:
        db.commit()
        return 0

    embeddings = embedder.embed([item.content for item, *_ in accepted])

    created = 0
    now = datetime.now(UTC)
    for (
        item,
        span,
        actor_role,
        actor_person_id,
        entity_id,
        authority,
        is_new_capability,
    ), embedding in zip(accepted, embeddings, strict=True):
        entity = db.get(Entities, entity_id)
        assert entity is not None
        subject_key = _build_subject_key(entity.slug, item.subject_capability, item.attributes)
        status = _determine_status(item.confidence, is_new_capability)
        attributes_json = item.attributes.model_dump(mode="json", exclude_none=True)
        # §7.3: "corrects"/"corrects_hint" ride along in attributes.extra — they're not a
        # §4.2 slot, but R3's explicit-correction check needs them on the persisted object.
        if item.corrects or item.corrects_hint:
            extra = attributes_json.setdefault("extra", {})
            extra["corrects"] = item.corrects
            if item.corrects_hint:
                extra["corrects_hint"] = item.corrects_hint

        obj = ContextObjects(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            entity_id=entity_id,
            type=item.type,
            subject_key=subject_key,
            content=item.content,
            attributes=attributes_json,
            actor_label=item.actor_label,
            actor_role=actor_role,
            actor_person_id=actor_person_id,
            stage=source.stage,
            authority=authority,
            confidence=item.confidence,
            status=status,
            valid_from=source.source_ts,
            source_id=source.id,
            evidence_quote=item.evidence_quote,
            evidence_span=Range(span[0], span[1]),
            embedding=embedding,
            extraction_key=cache_key,
            version=1,
            created_at=now,
            updated_at=now,
        )
        db.add(obj)
        # Flush so `obj` has an id and is visible to the sibling queries in
        # resolve_object_state/create_lineage_links below (and to the next accepted item
        # in this same batch, if it shares a subject_key).
        db.flush()

        resolve_object_state(db, tenant_id, obj)
        create_lineage_links(db, tenant_id, obj)

        # obj.status may have been overridden by lifecycle rules (R1/R2/R4 -> conflicting)
        # above; the version=1 row records the object's *final* state as first written.
        db.add(
            ContextVersions(
                id=uuid.uuid4(),
                context_id=obj.id,
                version=1,
                status=obj.status,
                attributes=attributes_json,
                content=item.content,
                changed_by=None,
                reason="initial extraction",
                created_at=now,
            )
        )
        created += 1

    db.commit()
    return created
