"""Slack signature verification (§12.4): valid signature, bad signature, stale timestamp,
missing headers."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from app.slack.verify import SlackSignatureError, verify_slack_signature

SECRET = "test-signing-secret"


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
    return "v0=" + hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()


def test_valid_signature_passes() -> None:
    body = b'{"type":"url_verification","challenge":"abc"}'
    ts = str(int(time.time()))
    sig = _sign(SECRET, ts, body)

    verify_slack_signature(SECRET, ts, sig, body)  # does not raise


def test_bad_signature_is_rejected() -> None:
    body = b'{"type":"url_verification"}'
    ts = str(int(time.time()))

    with pytest.raises(SlackSignatureError, match="mismatch"):
        verify_slack_signature(SECRET, ts, "v0=" + "0" * 64, body)


def test_stale_timestamp_is_rejected() -> None:
    body = b'{"type":"url_verification"}'
    ts = str(int(time.time()) - 600)  # 10 minutes old, beyond the 5-minute replay window
    sig = _sign(SECRET, ts, body)

    with pytest.raises(SlackSignatureError, match="stale"):
        verify_slack_signature(SECRET, ts, sig, body)


def test_missing_headers_are_rejected() -> None:
    body = b'{"type":"url_verification"}'

    with pytest.raises(SlackSignatureError, match="missing"):
        verify_slack_signature(SECRET, None, None, body)

    with pytest.raises(SlackSignatureError, match="missing"):
        verify_slack_signature(SECRET, str(int(time.time())), None, body)
