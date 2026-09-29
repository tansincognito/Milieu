"""Concurrent extraction pre-warm (§13.1, §13.3).

The pipeline has one slow step (the LLM extraction) and one strictly ordered step
(dedup/lifecycle/lineage, §7.2/§7.3/§8, where each new object is compared against whatever
is currently `active`). Running the whole thing sequentially to protect the ordered half
also serialises the slow half, so a 50-source load costs 50 round trips end to end.

This module runs only the order-independent half, concurrently, and writes the results to
the shared extraction cache. The caller then replays the sources through
`process_extraction_job` in the exact order it needs, where every call is a cache hit and
does DB work only. Ordering guarantees are unchanged — this changes when the network calls
happen, not the sequence the lifecycle rules see.

Sources whose prompt depends on DB state (a later section of an incident document, which
binds to the subject a sibling already established — see `process._sibling_subject_key`)
are deliberately *not* pre-warmed: that hint is part of the cache key, and at pre-warm time
no sibling has been persisted yet, so the hint cannot be known. Those few sources fall
through to a live call during the sequential replay, which is correct and cheap.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from redis import Redis

from app.core.config import Settings
from app.llm.base import LLMClient
from app.models.orm import Sources
from app.pipeline.process import extract_source

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 8


def _detached_copy(source: Sources) -> Sources:
    """A transient `Sources` carrying only the fields `extract_source` reads.

    Built on the calling thread while the real row is still attached. Worker threads must
    never touch a `Session`-bound instance: a rollback anywhere expires it, and the next
    attribute read would either emit a query on a Session shared across threads or raise
    `ObjectDeletedError`.
    """
    return Sources(
        id=source.id,
        kind=source.kind,
        stage=source.stage,
        text=source.text,
        content_hash=source.content_hash,
        source_ts=source.source_ts,
        provenance=source.provenance,
    )


def prewarm_extractions(
    redis_client: Redis,
    llm: LLMClient,
    settings: Settings,
    sources: list[Sources],
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> tuple[int, int]:
    """Extract `sources` concurrently into the cache. Returns (succeeded, failed).

    Failures are swallowed on purpose: a pre-warm miss is not an error, it just means the
    sequential pass makes that call itself. Never let a warm-up failure abort the run.
    """
    if not sources:
        return (0, 0)

    detached = [_detached_copy(s) for s in sources]
    succeeded = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(extract_source, redis_client, llm, settings, snapshot): snapshot
            for snapshot in detached
        }
        for future in as_completed(futures):
            snapshot = futures[future]
            try:
                future.result()
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - see docstring
                failed += 1
                logger.warning("prewarm failed for source %s: %s", snapshot.id, exc)

    return (succeeded, failed)
