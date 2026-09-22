"""Extraction cache (§13.3): key = sha256(content_hash, prompt_version, model_id,
schema_version), stored in Redis."""

from __future__ import annotations

import hashlib

from redis import Redis

from app.schemas.extraction import ExtractionResult


def extraction_cache_key(
    content_hash: str, prompt_version: str, model_id: str, schema_version: str
) -> str:
    raw = f"{content_hash}:{prompt_version}:{model_id}:{schema_version}"
    return "extract:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_extraction(redis_client: Redis, key: str) -> ExtractionResult | None:
    raw = redis_client.get(key)
    if raw is None:
        return None
    return ExtractionResult.model_validate_json(raw)


def set_cached_extraction(redis_client: Redis, key: str, result: ExtractionResult) -> None:
    redis_client.set(key, result.model_dump_json())
