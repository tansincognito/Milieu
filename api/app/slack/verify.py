"""Slack request signature verification (§12.4).

`v0` HMAC-SHA256 over `v0:{timestamp}:{raw_body}`, constant-time compared against the
`X-Slack-Signature` header. Timestamps older than 5 minutes are rejected (replay guard).
Must run against the RAW request body — the caller reads it before any JSON/form parsing,
since re-serializing would not byte-for-byte match what Slack signed.
"""

from __future__ import annotations

import hashlib
import hmac
import time

REPLAY_WINDOW_SECONDS = 5 * 60


class SlackSignatureError(ValueError):
    """Raised when a Slack request fails signature verification."""


def verify_slack_signature(
    signing_secret: str,
    timestamp: str | None,
    signature: str | None,
    raw_body: bytes,
    *,
    now: float | None = None,
) -> None:
    """Raises `SlackSignatureError` if the request doesn't verify. Returns None on success."""
    if not timestamp or not signature:
        raise SlackSignatureError("missing Slack signature headers")

    try:
        request_ts = int(timestamp)
    except ValueError as exc:
        raise SlackSignatureError("invalid timestamp header") from exc

    current = now if now is not None else time.time()
    if abs(current - request_ts) > REPLAY_WINDOW_SECONDS:
        raise SlackSignatureError("stale timestamp (possible replay)")

    basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}".encode()
    computed = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed, signature):
        raise SlackSignatureError("signature mismatch")
