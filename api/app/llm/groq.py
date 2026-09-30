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

NOT live-tested: no GROQ_API_KEY has been provided yet. The extraction/judge call shape is
identical to the already-verified OpenRouter path, but that is not the same as having run
a real request against Groq's API — say so plainly until it has.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMRateLimitedError, LLMValidationError, SchemaT

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


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

    def _post(self, body: dict) -> httpx.Response:
        resp = self._client.post("/chat/completions", json=body)
        if resp.status_code == 429:
            retry_after_hdr = resp.headers.get("Retry-After")
            retry_after = float(retry_after_hdr) if retry_after_hdr else None
            raise LLMRateLimitedError("Groq rate limit (429)", retry_after=retry_after)
        return resp
