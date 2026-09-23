"""Extraction job pipeline: extract -> validate -> evidence-span check -> resolve entity
(minimal) -> assign authority/confidence -> embed -> persist context_objects + a
context_versions row (§13.1, up to persist — dedup/supersession/conflict/lineage are Day 2).
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
from app.pipeline.prompts import PROMPT_VERSION, build_extraction_prompt
from app.schemas.extraction import ContextAttributes, ExtractedContext, ExtractionResult

logger = logging.getLogger(__name__)


class SourceNotFoundError(RuntimeError):
    pass


def _resolve_actor_role(
    source: Sources, item: ExtractedContext, directory: PeopleDirectory, tenant_id: uuid.UUID
) -> str:
    if source.kind == "call":
        return actor_role_from_speaker_label(item.actor_label)
    if source.kind == "email":
        from_email = source.provenance.get("from")
        return resolve_person(directory, tenant_id, email=from_email).actor_role
    if source.kind == "slack":
        author_id = source.provenance.get("author_id")
        return resolve_person(directory, tenant_id, slack_user_id=author_id).actor_role
    # drive: §6.2's DriveProvenance carries no author identity, so per-item actor_role
    # can't be resolved through the directory. Fall back to the source's own stage as a
    # proxy for "owning function" — documented gap, flagged in the dispatch report.
    return source.stage or "other"


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


def _document_context(source: Sources) -> str | None:
    """A one-line description of the parent artifact a source record came from.

    Sections and single messages are extracted in isolation, so without this the model
    cannot infer the capability or entity a fragment belongs to.
    """
    prov = source.provenance or {}
    if source.kind == "drive":
        path = prov.get("path")
        heading = prov.get("section_heading")
        if path and heading:
            return f'the document "{path}", section "{heading}"'
        return f'the document "{path}"' if path else None
    if source.kind == "email":
        subject = prov.get("subject")
        return f'the email thread "{subject}"' if subject else None
    if source.kind == "call":
        call_id = prov.get("call_id")
        return f"the call transcript {call_id}" if call_id else None
    return None


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

    cache_key = extraction_cache_key(
        source.content_hash, PROMPT_VERSION, settings.llm_model, settings.schema_version
    )
    cached = get_cached_extraction(redis_client, cache_key)
    if cached is not None:
        result = cached
    else:
        system = build_extraction_prompt(
            source.kind, source.stage, source.source_ts, _document_context(source)
        )
        result = llm.extract(ExtractionResult, system, source.text)
        set_cached_extraction(redis_client, cache_key, result)

    directory = SqlAlchemyPeopleDirectory(db)

    accepted: list[tuple[ExtractedContext, tuple[int, int], str, uuid.UUID, int, bool]] = []
    for item in result.items:
        try:
            span = compute_evidence_span(source.text, item.evidence_quote)
        except EvidenceSpanError:
            logger.warning(
                "rejecting extracted object: evidence_quote not found in source %s", source_id
            )
            continue

        actor_role = _resolve_actor_role(source, item, directory, tenant_id)
        entity_id = resolve_entity(db, tenant_id, item.entity_hint)
        authority = assign_authority(
            type_=item.type, actor_role=actor_role, stage=source.stage, speculative=item.speculative
        )

        vocab_row = db.get(CapabilityVocab, item.subject_capability)
        is_new_capability = vocab_row is None
        if is_new_capability:
            db.add(CapabilityVocab(slug=item.subject_capability, parent_slug=None, synonyms=[]))
            db.flush()

        accepted.append((item, span, actor_role, entity_id, authority, is_new_capability))

    if not accepted:
        db.commit()
        return 0

    embeddings = embedder.embed([item.content for item, *_ in accepted])

    created = 0
    now = datetime.now(UTC)
    for (item, span, actor_role, entity_id, authority, is_new_capability), embedding in zip(
        accepted, embeddings, strict=True
    ):
        entity = db.get(Entities, entity_id)
        assert entity is not None
        subject_key = _build_subject_key(entity.slug, item.subject_capability, item.attributes)
        status = _determine_status(item.confidence, is_new_capability)
        attributes_json = item.attributes.model_dump(mode="json", exclude_none=True)

        obj_id = uuid.uuid4()
        db.add(
            ContextObjects(
                id=obj_id,
                tenant_id=tenant_id,
                entity_id=entity_id,
                type=item.type,
                subject_key=subject_key,
                content=item.content,
                attributes=attributes_json,
                actor_label=item.actor_label,
                actor_role=actor_role,
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
        )
        db.add(
            ContextVersions(
                id=uuid.uuid4(),
                context_id=obj_id,
                version=1,
                status=status,
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
