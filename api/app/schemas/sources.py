"""Pydantic models for normalized source records (§6.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

SourceKind = Literal["slack", "email", "drive", "call", "api"]
Stage = Literal["sales", "product", "engineering", "customer_success"]


class SlackProvenance(BaseModel):
    kind: Literal["slack"] = "slack"
    workspace_id: str
    channel_id: str
    message_ts: str
    thread_ts: str | None = None
    author_id: str


class EmailProvenance(BaseModel):
    kind: Literal["email"] = "email"
    thread_id: str
    message_id: str
    from_: str = Field(alias="from")
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str

    model_config = {"populate_by_name": True}


class DriveProvenance(BaseModel):
    kind: Literal["drive"] = "drive"
    document_id: str
    path: str
    section_heading: str
    section_index: int
    modified_ts: datetime


class CallConsent(BaseModel):
    given: bool
    by: str
    at: datetime


class CallProvenance(BaseModel):
    """§6.2 provenance for a call source.

    The MVP ingests one source per *transcript* (dispatch scope decision, not per
    utterance), so the extractor sees full conversational context and speaker labels
    inline in `NormalizedSource.text`. `speaker`/`utterance_index` are kept for schema
    parity with §6.2 but are `None`/`0` at the whole-transcript grain; a future
    per-utterance connector would populate them per source.
    """

    kind: Literal["call"] = "call"
    call_id: str
    speaker: str | None = None
    utterance_index: int = 0
    transcript_path: str
    consent: CallConsent


Provenance = Annotated[
    SlackProvenance | EmailProvenance | DriveProvenance | CallProvenance,
    Field(discriminator="kind"),
]


class NormalizedSource(BaseModel):
    kind: SourceKind
    external_id: str
    version: int = 1
    content_hash: str
    text: str
    source_ts: datetime
    stage: Stage | None
    acl: list[str] = Field(default_factory=lambda: ["*"])
    provenance: Provenance
