"""Provider selection by env var (§20: "The LLM provider, embedding provider, queue
implementation... are all selected by env var.")."""

from __future__ import annotations

from redis import Redis

from app.core.config import Settings
from app.embedding.base import EmbeddingClient
from app.embedding.fastembed_client import FastEmbedClient
from app.llm.base import LLMClient
from app.llm.openrouter import OpenRouterLLMClient
from app.slack.client import HttpSlackClient, SlackClient


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider != "openrouter":
        raise ValueError(f"unsupported LLM_PROVIDER: {settings.llm_provider!r}")
    return OpenRouterLLMClient(
        api_key=settings.openrouter_api_key,
        model=settings.llm_model,
        app_name=settings.openrouter_app_name,
        site_url=settings.openrouter_site_url,
    )


def build_embedding_client(settings: Settings) -> EmbeddingClient:
    if settings.embedding_provider != "fastembed":
        raise ValueError(f"unsupported EMBEDDING_PROVIDER: {settings.embedding_provider!r}")
    return FastEmbedClient(model_name=settings.embedding_model)


def build_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url)


def build_slack_client(settings: Settings) -> SlackClient:
    return HttpSlackClient(bot_token=settings.slack_bot_token)
