"""Pure unit tests for the document-context/subject-binding fix (prompt v7, §11):

Drive documents are split into one source record per section (§6.2), so a section like
"## Remediation" is extracted in isolation from the rest of its document. These tests lock
in `_document_context`/`_established_subject_hint` — the pure, DB-free half of the fix —
without making a live LLM call. `_sibling_subject_key` (the DB-querying half) is exercised
indirectly through the integration extraction-pipeline tests instead.
"""

from __future__ import annotations

from datetime import date

from app.models.orm import Sources
from app.pipeline.process import _document_context, _established_subject_hint
from app.pipeline.prompts import PROMPT_VERSION, build_extraction_prompt


def _drive_source(path: str, heading: str) -> Sources:
    return Sources(
        kind="drive",
        provenance={"document_id": path, "path": path, "section_heading": heading},
    )


def test_established_subject_hint_for_incident_subject() -> None:
    assert _established_subject_hint("acme:incident:INC-2311") == (
        'subject_capability="incident", the same incident '
        '(attributes.extra.incident_id="INC-2311")'
    )


def test_established_subject_hint_for_plain_capability_subject() -> None:
    assert _established_subject_hint("acme:sso") == 'subject_capability="sso"'


def test_document_context_without_established_subject_is_unchanged() -> None:
    source = _drive_source("engineering/postmortem-INC-2311.md", "Remediation")

    context = _document_context(source)

    assert context == (
        'the document "engineering/postmortem-INC-2311.md", section "Remediation"'
    )


def test_document_context_binds_later_section_to_established_incident_subject() -> None:
    source = _drive_source("engineering/postmortem-INC-2311.md", "Remediation")

    context = _document_context(source, "acme:incident:INC-2311")

    assert context is not None
    assert 'the document "engineering/postmortem-INC-2311.md", section "Remediation"' in context
    # The exact subject_capability/incident_id the model must reuse, spelled out so a weak
    # free-tier model doesn't have to infer it from a standalone paragraph.
    assert 'subject_capability="incident"' in context
    assert 'attributes.extra.incident_id="INC-2311"' in context


def test_document_context_established_subject_survives_into_the_full_prompt() -> None:
    source = _drive_source("engineering/postmortem-INC-2311.md", "Remediation")
    context = _document_context(source, "acme:incident:INC-2311")

    prompt = build_extraction_prompt("drive", "engineering", date(2026, 11, 27), context)

    assert 'subject_capability="incident"' in prompt
    assert 'attributes.extra.incident_id="INC-2311"' in prompt


def test_established_subject_is_only_applied_to_drive_sources() -> None:
    # Email/call document_context has no notion of a sibling "section" to bind to; passing
    # an established_subject_key for a non-drive source is a caller bug, not something this
    # function should silently render into the prompt.
    source = Sources(kind="email", provenance={"subject": "Acme security thread"})

    context = _document_context(source, "acme:incident:INC-2311")

    assert context == 'the email thread "Acme security thread"'


def test_prompt_version_was_bumped_for_this_fix() -> None:
    # The extraction cache key includes PROMPT_VERSION (§13.3); forgetting to bump it would
    # mean a source re-extracted after this fix could still serve a pre-fix cached result.
    assert PROMPT_VERSION == "7"


def test_prompt_scopes_subject_binding_to_incidents_only() -> None:
    """A document routinely covers several capabilities, so the binding rule must say it
    applies to incidents only. `product/acme-prd.md` has a "Requirements" section about SSO
    and a "Provisioning" section about SCIM; binding across those would collapse
    `acme:scim` into `acme:sso` and destroy the R1 cross-stage SCIM conflict (§7.3, §19.1).
    """
    prompt = build_extraction_prompt("drive", "product", date(2026, 9, 25))

    assert "binding applies to incidents only" in prompt
    assert "derive each section's capability from its own text as usual" in prompt
