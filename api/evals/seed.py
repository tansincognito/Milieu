"""Deterministic seeded ingest for the eval harness (§19).

Runs the *real* pipeline (§13.1: normalize -> hash -> enqueue -> extract -> validate ->
resolve entity -> authority -> embed -> dedupe -> lifecycle/supersession/conflict ->
lineage -> persist) against every file under `/mock-data`, using the real `LLMClient`
(OpenRouter) and the real `EmbeddingClient` (fastembed). No hand-stubbed objects.

Deterministic and reproducible:
  - A fixed eval tenant (`EVAL_TENANT_ID`) is wiped and rebuilt from scratch on every run,
    so lifecycle/dedup/lineage rules (order- and state-dependent) always see the same
    sequence of arrivals.
  - Sources are ingested and then processed one at a time in strict connector/file order
    (the same order `app.pipeline.load_mock_data.load_mock_data` iterates), not via
    concurrent worker polling — needed because §7.3's R1-R4 rules compare a new object
    against whatever is *currently* `active`, so processing order changes outcomes.
  - The extraction cache (Redis, keyed by `sha256(content_hash, prompt_version, model_id,
    schema_version)`, §13.3) is left on and is *not* tenant-scoped, so a second `make eval`
    run reuses cached LLM output instead of re-hitting OpenRouter for unchanged mock data.

This intentionally duplicates (rather than imports and reuses) the connector-iteration loop
in `app.pipeline.load_mock_data`: that function ingests but doesn't return ingestion order,
and it is out of scope for this dispatch to modify pipeline modules other agents are
actively editing in parallel.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.connectors.base import SourceConnector
from app.connectors.mock_call import ConsentNotGivenError, MockCallConnector
from app.connectors.mock_drive import MockDriveConnector
from app.connectors.mock_email import MockEmailConnector
from app.connectors.mock_slack_seed import MockSlackSeedConnector
from app.core.config import Settings, get_settings
from app.core.db import engine
from app.core.factories import build_embedding_client, build_llm_client, build_redis_client
from app.directory.resolve import SqlAlchemyPeopleDirectory
from app.llm.base import LLMRateLimitedError, LLMValidationError
from app.models.orm import Sources
from app.pipeline.ingest import ingest_source
from app.pipeline.process import process_extraction_job
from app.pipeline.seed_directory import load_directory, load_slack_channel_stage_map
from app.queue.base import JobQueue
from app.queue.postgres import PostgresJobQueue

# Fixed, dedicated tenant for eval runs -- never the dev default tenant
# (00000000-0000-0000-0000-000000000001), so `make eval` never collides with whatever a
# developer has loaded through `POST /sources/mock/load` in their own manual testing.
EVAL_TENANT_ID = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")

MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 4.0
BACKOFF_MAX_SECONDS = 45.0

_INFRA_EXCEPTIONS = (
    LLMRateLimitedError,
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
)


@dataclass
class SourceIngestResult:
    source_id: uuid.UUID
    kind: str
    external_id: str
    status: str  # "done" | "infra_failed" | "extraction_failed"
    attempts: int
    objects_created: int
    error: str | None = None


@dataclass
class IngestReport:
    tenant_id: uuid.UUID
    load_counts: dict[str, int] = field(default_factory=dict)
    sources: list[SourceIngestResult] = field(default_factory=list)

    @property
    def infra_failures(self) -> list[SourceIngestResult]:
        return [s for s in self.sources if s.status == "infra_failed"]

    @property
    def extraction_failures(self) -> list[SourceIngestResult]:
        return [s for s in self.sources if s.status == "extraction_failed"]

    @property
    def total_objects(self) -> int:
        return sum(s.objects_created for s in self.sources)


def _wipe_tenant(db: Session, tenant_id: uuid.UUID) -> None:
    """Full teardown of every row scoped to `tenant_id`, so each `make eval` run starts
    from a clean slate regardless of what a previous (possibly interrupted) run left
    behind."""
    t = {"t": tenant_id}
    db.execute(
        text(
            "DELETE FROM context_relations WHERE from_id IN "
            "(SELECT id FROM context_objects WHERE tenant_id = :t)"
        ),
        t,
    )
    db.execute(
        text(
            "DELETE FROM context_versions WHERE context_id IN "
            "(SELECT id FROM context_objects WHERE tenant_id = :t)"
        ),
        t,
    )
    db.execute(text("DELETE FROM context_objects WHERE tenant_id = :t"), t)
    db.execute(
        text("DELETE FROM processing_jobs WHERE payload ->> 'tenant_id' = :t"),
        {"t": str(tenant_id)},
    )
    db.execute(text("DELETE FROM sources WHERE tenant_id = :t"), t)
    db.execute(text("DELETE FROM entity_aliases WHERE tenant_id = :t"), t)
    db.execute(text("DELETE FROM entities WHERE tenant_id = :t"), t)
    db.execute(text("DELETE FROM org_domains WHERE tenant_id = :t"), t)
    db.execute(text("DELETE FROM people WHERE tenant_id = :t"), t)
    db.commit()


def _ordered_ingest(
    db: Session, queue: JobQueue, tenant_id: uuid.UUID, mock_data_dir: Path
) -> tuple[dict[str, int], list[uuid.UUID]]:
    """Mirrors `load_mock_data`'s connector order exactly, but also returns the created
    source ids in strict ingestion order (drive -> call -> email -> slack; within each,
    the connector's own natural order), which `load_mock_data` itself doesn't expose."""
    directory_path = mock_data_dir / "directory.json"
    load_directory(db, tenant_id, directory_path)

    directory = SqlAlchemyPeopleDirectory(db)
    channel_stage_map = load_slack_channel_stage_map(directory_path)

    connectors: list[SourceConnector] = [
        MockDriveConnector(mock_data_dir / "drive"),
        MockCallConnector(mock_data_dir / "calls"),
        MockEmailConnector(mock_data_dir / "email", directory, tenant_id),
        MockSlackSeedConnector(mock_data_dir / "slack" / "seed.json", channel_stage_map),
    ]

    counts = {
        "sources_created": 0,
        "sources_skipped": 0,
        "jobs_enqueued": 0,
        "consent_rejected": 0,
    }
    ordered_ids: list[uuid.UUID] = []
    for connector in connectors:
        for raw in connector.fetch(None):
            try:
                normalized = connector.normalize(raw)
            except ConsentNotGivenError:
                counts["consent_rejected"] += 1
                continue
            source_id, was_new = ingest_source(db, queue, tenant_id, normalized)
            ordered_ids.append(source_id)
            if was_new:
                counts["sources_created"] += 1
                counts["jobs_enqueued"] += 1
            else:
                counts["sources_skipped"] += 1

    return counts, ordered_ids


def _process_with_retry(
    db: Session, redis_client, llm, embedder, settings: Settings, tenant_id: uuid.UUID, source: Sources
) -> SourceIngestResult:
    last_error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            created = process_extraction_job(
                db, redis_client, llm, embedder, settings, tenant_id, source.id
            )
        except LLMRateLimitedError as exc:
            db.rollback()
            last_error = f"rate_limited (attempt {attempt}): {exc}"
            sleep_s = exc.retry_after or min(
                BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS
            )
            time.sleep(sleep_s)
            continue
        except _INFRA_EXCEPTIONS as exc:
            db.rollback()
            last_error = f"infra ({type(exc).__name__}, attempt {attempt}): {exc}"
            time.sleep(min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS))
            continue
        except (LLMValidationError, Exception) as exc:  # noqa: BLE001 - classify below
            db.rollback()
            last_error = f"extraction ({type(exc).__name__}, attempt {attempt}): {exc}"
            if attempt >= 2:
                # A schema-validation or pipeline failure that repeats on retry is a real
                # (reproducible) failure, not free-tier flakiness -- stop retrying it.
                return SourceIngestResult(
                    source.id, source.kind, source.external_id, "extraction_failed", attempt, 0, last_error
                )
            time.sleep(BACKOFF_BASE_SECONDS)
            continue
        else:
            return SourceIngestResult(
                source.id, source.kind, source.external_id, "done", attempt, created
            )

    return SourceIngestResult(
        source.id, source.kind, source.external_id, "infra_failed", MAX_ATTEMPTS, 0, last_error
    )


def run_seeded_ingest(
    *, mock_data_dir: Path | None = None, settings: Settings | None = None, verbose: bool = True
) -> IngestReport:
    """Wipes the eval tenant, ingests every mock-data file, and drains every resulting
    extraction job synchronously (real LLM + real embedder), in deterministic order.
    Returns a report of what happened per source, so the runner can tell an infra failure
    (OpenRouter 429/timeout) apart from a real pipeline failure."""
    settings = settings or get_settings()
    mock_data_dir = mock_data_dir or Path(settings.mock_data_dir)

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = session_factory()
    try:
        _wipe_tenant(db, EVAL_TENANT_ID)

        queue: JobQueue = PostgresJobQueue(session_factory)
        load_counts, ordered_source_ids = _ordered_ingest(
            db, queue, EVAL_TENANT_ID, mock_data_dir
        )

        llm = build_llm_client(settings)
        embedder = build_embedding_client(settings)
        redis_client = build_redis_client(settings)

        report = IngestReport(tenant_id=EVAL_TENANT_ID, load_counts=load_counts)
        for source_id in ordered_source_ids:
            source = db.get(Sources, source_id)
            assert source is not None
            result = _process_with_retry(
                db, redis_client, llm, embedder, settings, EVAL_TENANT_ID, source
            )
            report.sources.append(result)
            if verbose:
                marker = {"done": "OK", "infra_failed": "INFRA-FAIL", "extraction_failed": "FAIL"}[
                    result.status
                ]
                print(
                    f"  [{marker:10}] {source.kind:6} {source.external_id:55} "
                    f"-> {result.objects_created} objects (attempts={result.attempts})"
                )
                if result.error:
                    print(f"               {result.error}")
        return report
    finally:
        db.close()
