"""Outbound Slack Web API calls (§12.1/§12.2), behind a small interface so the API
routes and tests never depend on a real Slack workspace (none is available for this
dispatch — see the `HttpSlackClient` docstring). Tests use an in-memory fake.

`fetch_message` is not in the dispatch brief's four-method list (`chat.postEphemeral`,
`chat.postMessage`, `conversations.members`, `chat.getPermalink`) but is required to
implement `@context mark` literally: "marks the thread parent ... as context" needs the
parent message's text, which only a Slack API call can supply. Documented as an addition
in the dispatch report.
"""

from __future__ import annotations

from typing import Protocol

import httpx

SLACK_API_BASE_URL = "https://slack.com/api"


class SlackClient(Protocol):
    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> None: ...

    def post_ephemeral(self, *, channel: str, user: str, text: str) -> None: ...

    def conversations_members(self, channel: str) -> list[str]: ...

    def get_permalink(self, *, channel: str, message_ts: str) -> str | None: ...

    def fetch_message(self, *, channel: str, ts: str) -> str | None: ...


class HttpSlackClient:
    """Real Slack Web API client (bot-token auth). The MVP needs exactly one
    implementation of this adapter — no other Slack backend is in scope."""

    def __init__(
        self,
        bot_token: str,
        base_url: str = SLACK_API_BASE_URL,
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {bot_token}"},
            timeout=timeout,
        )

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> None:
        body: dict[str, str] = {"channel": channel, "text": text}
        if thread_ts:
            body["thread_ts"] = thread_ts
        self._client.post("/chat.postMessage", json=body)

    def post_ephemeral(self, *, channel: str, user: str, text: str) -> None:
        self._client.post(
            "/chat.postEphemeral", json={"channel": channel, "user": user, "text": text}
        )

    def conversations_members(self, channel: str) -> list[str]:
        members: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, str] = {"channel": channel}
            if cursor:
                params["cursor"] = cursor
            resp = self._client.get("/conversations.members", params=params)
            data = resp.json()
            members.extend(data.get("members", []))
            cursor = data.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                break
        return members

    def get_permalink(self, *, channel: str, message_ts: str) -> str | None:
        resp = self._client.get(
            "/chat.getPermalink", params={"channel": channel, "message_ts": message_ts}
        )
        data = resp.json()
        return data.get("permalink") if data.get("ok") else None

    def fetch_message(self, *, channel: str, ts: str) -> str | None:
        resp = self._client.get(
            "/conversations.history",
            params={"channel": channel, "latest": ts, "inclusive": "true", "limit": "1"},
        )
        data = resp.json()
        messages = data.get("messages") or []
        text = messages[0].get("text") if messages else None
        return str(text) if text is not None else None
