"""POST /sources/slack/events, POST /sources/slack/interactions (§12, §15).

Signature verification (§12.4) runs against the RAW request body, read before any
JSON/form parsing. Message-capture events only normalize/hash/dedupe/enqueue (§13.1) —
the worker does extraction, never this handler. `@context <entity> [topic]` retrieval
(§12.2) is the one synchronous reply: it's a permission-filtered DB read with no LLM
summary, so it doesn't need the async job path.

Slack event dedup (§13.1 "Deduplicate redeliveries by Slack event_id"): the outer event
envelope's `event_id` is used as `external_id`, which lands in the same
`(tenant, kind, external_id, content_hash)` unique constraint `ingest_source` already
enforces — a redelivery is a no-op, no new source or job.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_queue, get_slack_client
from app.core.config import Settings
from app.core.db import get_db
from app.models.orm import ContextObjects, EntityAliases, Sources
from app.pipeline.ingest import ingest_source
from app.pipeline.seed_directory import load_slack_channel_id_stage_map, normalize_alias
from app.queue.base import JobQueue
from app.schemas.sources import NormalizedSource, SlackProvenance, Stage
from app.slack.client import SlackClient
from app.slack.verify import SlackSignatureError, verify_slack_signature

router = APIRouter()

# A plain user message never has this key; every automated/edited/joined variant does.
_MENTION_PREFIX_RE = re.compile(r"^\s*<@[^>]+>[:,]?\s*")
_MARK_RE = re.compile(r"(?i)^mark\b\s*(.*)$")


def _verify_request(request: Request, raw_body: bytes, settings: Settings) -> None:
    try:
        verify_slack_signature(
            settings.slack_signing_secret,
            request.headers.get("X-Slack-Request-Timestamp"),
            request.headers.get("X-Slack-Signature"),
            raw_body,
        )
    except SlackSignatureError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _channel_id_stage_map(settings: Settings) -> dict[str, Stage | None]:
    return load_slack_channel_id_stage_map(Path(settings.mock_data_dir) / "directory.json")


def _acl_for_event(event: dict, slack_client: SlackClient) -> list[str]:
    """§14: private channel -> member list at ingest time; public -> ["*"]."""
    if event.get("channel_type") == "group":
        return slack_client.conversations_members(event["channel"])
    return ["*"]


@router.post("/sources/slack/events")
async def slack_events(
    request: Request,
    db: Session = Depends(get_db),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_app_settings),
    slack_client: SlackClient = Depends(get_slack_client),
) -> dict:
    raw_body = await request.body()
    _verify_request(request, raw_body, settings)
    body = json.loads(raw_body)

    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    if body.get("type") != "event_callback":
        return {"ok": True}

    event = body.get("event", {})
    event_type = event.get("type")
    channel_id_stage_map = _channel_id_stage_map(settings)

    # Ignore bot messages and any non-plain-text subtype (edits, deletes, joins,
    # bot_message, etc. all set `subtype`; a plain user message never does) — avoids
    # extraction loops on our own or Slack's own automated messages (§12.4/§13.1).
    if event_type == "app_mention":
        _handle_app_mention(db, queue, settings, slack_client, body, event, channel_id_stage_map)
    elif event_type == "message" and not event.get("bot_id") and not event.get("subtype"):
        _ingest_message(db, queue, settings, slack_client, body, event, channel_id_stage_map)

    return {"ok": True}


@router.post("/sources/slack/interactions")
async def slack_interactions(
    request: Request,
    db: Session = Depends(get_db),
    queue: JobQueue = Depends(get_queue),
    settings: Settings = Depends(get_app_settings),
    slack_client: SlackClient = Depends(get_slack_client),
) -> dict:
    raw_body = await request.body()
    _verify_request(request, raw_body, settings)

    form = parse_qs(raw_body.decode("utf-8"))
    payload_values = form.get("payload")
    if not payload_values:
        raise HTTPException(status_code=400, detail="missing payload field")
    payload = json.loads(payload_values[0])
    payload_type = payload.get("type")

    if payload_type == "message_action" and payload.get("callback_id") == "save_as_context":
        _handle_save_as_context(db, queue, settings, slack_client, payload)
    elif payload_type == "block_actions":
        _handle_block_actions(slack_client, payload)

    return {"ok": True}


# ---- event_callback: message capture -----------------------------------------------


def _ingest_message(
    db: Session,
    queue: JobQueue,
    settings: Settings,
    slack_client: SlackClient,
    body: dict,
    event: dict,
    channel_id_stage_map: dict[str, Stage | None],
) -> None:
    text = (event.get("text") or "").strip()
    # A message that itself starts with a bot mention is command syntax handled by the
    # app_mention branch, not organic content worth capturing.
    if not text or _MENTION_PREFIX_RE.match(text):
        return

    tenant_id = uuid.UUID(settings.tenant_id)
    channel_id = event["channel"]
    ts = event["ts"]
    normalized = NormalizedSource(
        kind="slack",
        external_id=body.get("event_id", f"{channel_id}:{ts}"),
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        source_ts=_ts_to_datetime(ts),
        stage=channel_id_stage_map.get(channel_id),
        acl=_acl_for_event(event, slack_client),
        provenance=SlackProvenance(
            workspace_id=body.get("team_id", "unknown"),
            channel_id=channel_id,
            message_ts=ts,
            thread_ts=event.get("thread_ts"),
            author_id=event.get("user", "unknown"),
        ),
    )
    ingest_source(db, queue, tenant_id, normalized)


# ---- app_mention: `mark [text]` and `<entity> [topic]` (§12.1/§12.2) ----------------


def _handle_app_mention(
    db: Session,
    queue: JobQueue,
    settings: Settings,
    slack_client: SlackClient,
    body: dict,
    event: dict,
    channel_id_stage_map: dict[str, Stage | None],
) -> None:
    text = event.get("text") or ""
    stripped = _MENTION_PREFIX_RE.sub("", text).strip()
    channel_id = event["channel"]
    ts = event["ts"]
    reply_thread = event.get("thread_ts") or ts

    mark_match = _MARK_RE.match(stripped)
    if mark_match:
        _handle_mark(
            db, queue, settings, slack_client, body, event, channel_id_stage_map,
            mark_match.group(1).strip(),
        )
        return

    if not stripped:
        slack_client.post_message(
            channel=channel_id,
            text="Usage: `@context <entity> [topic]` or `@context mark [text]`.",
            thread_ts=reply_thread,
        )
        return

    parts = stripped.split(maxsplit=1)
    entity_hint = parts[0]
    topic = parts[1] if len(parts) > 1 else None
    tenant_id = uuid.UUID(settings.tenant_id)
    reply_text = render_context_reply(
        db, tenant_id, entity_hint, topic, event.get("user", "unknown"), slack_client
    )
    slack_client.post_message(channel=channel_id, text=reply_text, thread_ts=reply_thread)


def _handle_mark(
    db: Session,
    queue: JobQueue,
    settings: Settings,
    slack_client: SlackClient,
    body: dict,
    event: dict,
    channel_id_stage_map: dict[str, Stage | None],
    extra_text: str,
) -> None:
    tenant_id = uuid.UUID(settings.tenant_id)
    channel_id = event["channel"]
    ts = event["ts"]
    thread_ts = event.get("thread_ts")
    reply_thread = thread_ts or ts

    parent_text = None
    if thread_ts and thread_ts != ts:
        parent_text = slack_client.fetch_message(channel=channel_id, ts=thread_ts)
    combined = "\n\n".join(part for part in (parent_text, extra_text) if part)

    if not combined:
        slack_client.post_message(
            channel=channel_id,
            text="Nothing to mark — add text after `mark` or reply inside a thread.",
            thread_ts=reply_thread,
        )
        return

    normalized = NormalizedSource(
        kind="slack",
        external_id=f"mark:{body.get('event_id', f'{channel_id}:{ts}')}",
        content_hash=hashlib.sha256(combined.encode("utf-8")).hexdigest(),
        text=combined,
        source_ts=_ts_to_datetime(ts),
        stage=channel_id_stage_map.get(channel_id),
        acl=_acl_for_event(event, slack_client),
        provenance=SlackProvenance(
            workspace_id=body.get("team_id", "unknown"),
            channel_id=channel_id,
            message_ts=ts,
            thread_ts=thread_ts,
            author_id=event.get("user", "unknown"),
        ),
    )
    source_id, _ = ingest_source(db, queue, tenant_id, normalized)
    slack_client.post_message(
        channel=channel_id,
        text=(
            f"Marked as context — source `{source_id}`. Extraction is queued; ask "
            "`@context <entity>` again once it's processed to see the extracted objects."
        ),
        thread_ts=reply_thread,
    )


def render_context_reply(
    db: Session,
    tenant_id: uuid.UUID,
    entity_hint: str,
    topic: str | None,
    caller_principal: str,
    slack_client: SlackClient,
) -> str:
    """§12.2 `@context <entity> [topic]`. Permission filter (§14) runs BEFORE composing
    the reply. No LLM summary — a plain list, ordered by authority then recency."""
    normalized = normalize_alias(entity_hint)
    alias = (
        db.query(EntityAliases)
        .filter(EntityAliases.tenant_id == tenant_id, EntityAliases.alias_normalized == normalized)
        .first()
    )
    if alias is None:
        return f"No context found for '{entity_hint}'."

    query_rows = (
        db.query(ContextObjects, Sources)
        .join(Sources, ContextObjects.source_id == Sources.id)
        .filter(
            ContextObjects.tenant_id == tenant_id,
            ContextObjects.entity_id == alias.entity_id,
            ContextObjects.status == "active",
        )
        .order_by(ContextObjects.authority.desc(), ContextObjects.created_at.desc())
        .all()
    )
    rows: list[tuple[ContextObjects, Sources]] = [(obj, src) for obj, src in query_rows]

    if topic:
        needle = topic.lower()
        rows = [
            (obj, src)
            for obj, src in rows
            if needle in obj.subject_key.lower() or needle in obj.content.lower()
        ]

    visible = [(obj, src) for obj, src in rows if "*" in src.acl or caller_principal in src.acl]
    if not visible:
        return f"No context visible to you for '{entity_hint}'."

    lines = [f"*{entity_hint}* — current context:"]
    for obj, src in visible:
        link = _source_link(src, slack_client)
        lines.append(f"• [{obj.type}] {obj.content} (authority {obj.authority}) — {link}")
    return "\n".join(lines)


def _source_link(source: Sources, slack_client: SlackClient) -> str:
    if source.kind == "slack":
        channel_id = source.provenance.get("channel_id")
        message_ts = source.provenance.get("message_ts")
        if channel_id and message_ts:
            permalink = slack_client.get_permalink(channel=channel_id, message_ts=message_ts)
            if permalink:
                return permalink
    return f"{source.kind} source {source.id}"


# ---- interactions: message shortcut + buttons (§12.1, §12.3) ------------------------


def _handle_save_as_context(
    db: Session, queue: JobQueue, settings: Settings, slack_client: SlackClient, payload: dict
) -> None:
    tenant_id = uuid.UUID(settings.tenant_id)
    message = payload.get("message", {})
    channel = payload.get("channel", {})
    user = payload.get("user", {})
    text = (message.get("text") or "").strip()
    channel_id = channel.get("id", "unknown")
    ts = message.get("ts", "0")

    acl = (
        slack_client.conversations_members(channel_id) if channel.get("is_private") else ["*"]
    )
    normalized = NormalizedSource(
        kind="slack",
        external_id=f"shortcut:{channel_id}:{ts}",
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        source_ts=_ts_to_datetime(ts),
        stage=_channel_id_stage_map(settings).get(channel_id),
        acl=acl,
        provenance=SlackProvenance(
            workspace_id=payload.get("team", {}).get("id", "unknown"),
            channel_id=channel_id,
            message_ts=ts,
            thread_ts=message.get("thread_ts"),
            author_id=message.get("user", "unknown"),
        ),
    )
    source_id, _ = ingest_source(db, queue, tenant_id, normalized)
    slack_client.post_ephemeral(
        channel=channel_id,
        user=user.get("id", ""),
        text=f"Saved as context — source `{source_id}`. Extraction queued.",
    )


def _handle_block_actions(slack_client: SlackClient, payload: dict) -> None:
    """Review/Ignore buttons on a context gap (§12.3, §17). `context_gaps` doesn't exist
    until Day 3 (the handoff validator), so this wires the dispatch path — recognizing
    both action ids and replying — without inventing gap-resolution logic against a table
    that has no rows yet."""
    user = payload.get("user", {})
    channel = payload.get("channel", {})
    for action in payload.get("actions", []):
        if action.get("action_id") in ("gap_review", "gap_ignore"):
            slack_client.post_ephemeral(
                channel=channel.get("id", ""),
                user=user.get("id", ""),
                text="Gap review isn't available yet — the handoff validator ships on Day 3.",
            )


def _ts_to_datetime(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts.split(".")[0]), tz=UTC)
