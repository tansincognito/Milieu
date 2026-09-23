"""OpenRouter LLMClient implementation.

Uses the OpenAI-compatible REST API directly over `httpx` (no `openai` SDK dependency).
Structured output is requested via `response_format: json_schema` (strict); if the model/
provider rejects that, we fall back once to `json_object` with the schema embedded in the
system prompt. Output is always Pydantic-validated; one retry with the validation error
appended to the prompt; after that the caller sees an exception and the job fails per
§13.2 (JobQueue retry/backoff/poison).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMRateLimitedError, LLMValidationError, SchemaT

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterLLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        app_name: str = "Milieu",
        site_url: str = "",
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 180.0,
    ) -> None:
        self._model = model
        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Title": app_name,
        }
        if site_url:
            headers["HTTP-Referer"] = site_url
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
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": user_content})

            raw = self._chat(messages, json_schema, schema.__name__)
            try:
                return schema.model_validate_json(raw)
            except ValidationError as e:
                last_error = str(e)
                if attempt == 1:
                    raise LLMValidationError(last_error) from e
        raise LLMValidationError(last_error or "unknown extraction failure")

    def _chat(self, messages: list[dict[str, str]], json_schema: dict, schema_name: str) -> str:
        body = {
            "model": self._model,
            "temperature": 0,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
            },
        }
        resp = self._post(body)
        if resp.status_code >= 400:
            # Fall back once to a plain JSON-object response with the schema embedded in the
            # prompt, in case this model/provider combination rejects json_schema mode.
            fallback_messages = [
                {
                    "role": "system",
                    "content": (
                        (messages[0]["content"] + "\n\n" if messages[0]["role"] == "system" else "")
                        + "Respond with ONLY a single JSON object matching this JSON Schema, "
                        "no prose, no markdown fences:\n" + json.dumps(json_schema)
                    ),
                },
                *[m for m in messages if m["role"] != "system"],
            ]
            fallback_body = {
                "model": self._model,
                "temperature": 0,
                "messages": fallback_messages,
                "response_format": {"type": "json_object"},
            }
            resp = self._post(fallback_body)
        resp.raise_for_status()
        data = resp.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError) as exc:
            # A 200 with no choices means the provider reported the failure in the body.
            raise RuntimeError(
                f"OpenRouter returned no completion: {data.get('error') or data}"
            ) from exc

    def _post(self, body: dict) -> httpx.Response:
        resp = self._client.post("/chat/completions", json=body)
        if resp.status_code == 429:
            retry_after_hdr = resp.headers.get("Retry-After")
            retry_after = float(retry_after_hdr) if retry_after_hdr else None
            raise LLMRateLimitedError("OpenRouter rate limit (429)", retry_after=retry_after)
        return resp
