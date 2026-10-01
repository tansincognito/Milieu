"""Groq LLMClient implementation.

Built as the fallback provider (architecture decision, 2026-10-01): OpenRouter's free
models all share OpenRouter's own pooled free capacity across every user hitting that
route, which is the actual cause of the sustained 429s this project has hit all session —
switching models within OpenRouter just moves to a different shared queue, not off the
underlying problem. Groq's free tier runs on Groq's own hardware with its own per-account
rate limits, not a multi-tenant marketplace pool, so it doesn't inherit that contention.

Groq's API is OpenAI-compatible (same `/chat/completions` shape OpenRouter uses), so this
mirrors `openrouter.py` closely on purpose — same retry/validation/fallback behavior, same
`LLMClient` contract, different base URL and auth header. `response_format:
{"type": "json_schema", ...}` support varies by Groq model, so this goes straight to the
`json_object` + schema-in-prompt path OpenRouter only falls back to, rather than assuming
strict schema mode works.

Live-tested 2026-10-01 against a real account. Confirmed constraint: Groq's free tier caps
on a **tokens-per-minute** budget (`x-ratelimit-limit-tokens`, 8000 TPM for every usable
text model tried), not request count (`x-ratelimit-limit-requests: 1000`, barely touched).
`openai/gpt-oss-*` are reasoning models — they burn hidden `completion_tokens_details.
reasoning_tokens` before emitting `content` (measured: 487 of 941 total tokens on a small
extraction call), so a `max_tokens` cap can truncate before any content is produced. Default
model is `qwen/qwen3.8-27b` instead: not a reasoning model, ~2.2x more token-efficient on
the same call shape (422 vs 941 total tokens, same prompt), same extraction quality.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMRateLimitedError, LLMValidationError, SchemaT

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_RESET_RE = re.compile(r"(?:(\d+)m)?([\d.]+)s")


def _parse_reset_seconds(value: str) -> float:
    """Parse Groq's `x-ratelimit-reset-*` header, e.g. "2.639s" or "1m26.4s"."""
    m = _RESET_RE.match(value)
    if not m:
        return 0.0
    minutes = float(m.group(1)) if m.group(1) else 0.0
    seconds = float(m.group(2))
    return minutes * 60 + seconds


class GroqLLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = GROQ_BASE_URL,
        timeout: float = 180.0,
    ) -> None:
        self._model = model
        headers = {"Authorization": f"Bearer {api_key}"}
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
        # Updated from every response's `x-ratelimit-*` headers (success or 429). None until
        # the first request completes. Read by `_post` itself (see `_wait_for_budget`) so
        # pacing covers every outbound request uniformly -- including the validation-retry
        # inside `_call`, which fires a second request with no gap otherwise. Found live
        # (2026-10-01): pacing only between worker job claims looked right in isolation but
        # still 429'd every cycle, because the real back-to-back pair was a job's own retry,
        # not two different jobs -- external, job-level pacing could never see that gap.
        self.remaining_tokens: int | None = None
        self.reset_tokens_seconds: float | None = None
        # Conservative margin (half the known 8000 TPM limit), not a measured per-call cost
        # -- real content size varies per request, so this is size-agnostic headroom rather
        # than a guess at any one call's actual need.
        self._min_tokens_margin = 4000

    def extract(self, schema: type[SchemaT], system: str, content: str) -> SchemaT:
        return self._call(schema, system, content)

    def judge(self, schema: type[SchemaT], prompt: str) -> SchemaT:
        return self._call(schema, system="", content=prompt)

    def _call(self, schema: type[BaseModel], system: str, content: str) -> Any:
        json_schema = schema.model_json_schema()
        last_error: str | None = None
        for attempt in range(2):  # initial attempt + one retry on validation error
            user_content = content
            if last_error:
                user_content = (
                    f"{content}\n\nYour previous response was invalid JSON for the required "
                    f"schema: {last_error}\nFix it and respond again, matching the schema "
                    "exactly."
                )
            system_content = (
                (system + "\n\n" if system else "")
                + "Respond with ONLY a single JSON object matching this JSON Schema, "
                "no prose, no markdown fences:\n" + json.dumps(json_schema)
            )
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ]

            raw = self._chat(messages)
            try:
                return schema.model_validate_json(raw)
            except ValidationError as e:
                last_error = str(e)
                if attempt == 1:
                    raise LLMValidationError(last_error) from e
        raise LLMValidationError(last_error or "unknown extraction failure")

    def _chat(self, messages: list[dict[str, str]]) -> str:
        body = {
            "model": self._model,
            "temperature": 0,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        resp = self._post(body)
        resp.raise_for_status()
        data = resp.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Groq returned no completion: {data.get('error') or data}") from exc

    def _wait_for_budget(self) -> None:
        if self.remaining_tokens is None or self.reset_tokens_seconds is None:
            return
        if self.remaining_tokens < self._min_tokens_margin:
            time.sleep(self.reset_tokens_seconds)

    def _post(self, body: dict) -> httpx.Response:
        self._wait_for_budget()
        resp = self._client.post("/chat/completions", json=body)

        remaining_hdr = resp.headers.get("x-ratelimit-remaining-tokens")
        reset_hdr = resp.headers.get("x-ratelimit-reset-tokens")
        if remaining_hdr is not None:
            self.remaining_tokens = int(remaining_hdr)
        if reset_hdr is not None:
            self.reset_tokens_seconds = _parse_reset_seconds(reset_hdr)

        if resp.status_code == 429:
            retry_after_hdr = resp.headers.get("Retry-After")
            retry_after = float(retry_after_hdr) if retry_after_hdr else None
            raise LLMRateLimitedError("Groq rate limit (429)", retry_after=retry_after)
        return resp
