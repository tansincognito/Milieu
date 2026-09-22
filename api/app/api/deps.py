"""FastAPI dependency providers."""

from __future__ import annotations

from collections.abc import Generator

from redis import Redis

from app.core.config import Settings, get_settings
from app.core.db import SessionLocal
from app.core.factories import build_redis_client
from app.queue.base import JobQueue
from app.queue.postgres import PostgresJobQueue


def get_queue() -> JobQueue:
    return PostgresJobQueue(SessionLocal)


def get_redis() -> Generator[Redis]:
    settings = get_settings()
    client = build_redis_client(settings)
    try:
        yield client
    finally:
        client.close()


def get_app_settings() -> Settings:
    return get_settings()
