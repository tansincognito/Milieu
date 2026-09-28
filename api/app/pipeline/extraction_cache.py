"""Extraction cache (§13.3): key = sha256(content_hash, prompt_version, model_id,
schema_version, context_key), stored in Redis.

`context_key` carries whatever *document-level* context was folded into the prompt but is
not part of the source text itself — currently the subject a sibling section already
established (see `process._sibling_subject_key`). Without it the same section text keys to
the same entry whether or not the binding instruction was present, so a result extracted
before any sibling existed would be served back to a later run that should have been told
to bind, silently defeating the binding rule.
"""

from __future__ import annotations

import hashlib

from redis import Redis

from app.schemas.extraction import ExtractionResult


def extraction_cache_key(
    content_hash: str,
    prompt_version: str,
    model_id: str,
    schema_version: str,
    context_key: str = "",
) -> str:
    raw = f"{content_hash}:{prompt_version}:{model_id}:{schema_version}:{context_key}"
    return "extract:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_extraction(redis_client: Redis, key: str) -> ExtractionResult | None:
    raw = redis_client.get(key)
    if raw is None:
        return None
    return ExtractionResult.model_validate_json(raw)


def set_cached_extraction(redis_client: Redis, key: str, result: ExtractionResult) -> None:
    redis_client.set(key, result.model_dump_json())
