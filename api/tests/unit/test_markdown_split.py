from app.connectors.markdown import split_markdown_sections

DOC = """# Acme Corp — Security & Access Requirements

Owner: Jordan Lee (Account Executive)
Last updated: 2026-10-03

## Requirements

Acme's security team requires SAML-based single sign-on through Okta.

## Priority

This is a P0 blocker for the Acme renewal.
"""


def test_splits_into_preamble_plus_h2_sections() -> None:
    sections = split_markdown_sections(DOC)

    assert [s.heading for s in sections] == [
        "Acme Corp — Security & Access Requirements",
        "Requirements",
        "Priority",
    ]
    assert [s.index for s in sections] == [0, 1, 2]


def test_section_text_is_exact_substring_of_original_document() -> None:
    sections = split_markdown_sections(DOC)

    for section in sections:
        assert section.text in DOC


def test_requirements_section_contains_expected_facts() -> None:
    sections = split_markdown_sections(DOC)
    requirements = next(s for s in sections if s.heading == "Requirements")

    assert "SAML" in requirements.text
    assert "Okta" in requirements.text


def test_document_with_no_h2_headings_is_one_section() -> None:
    doc = "# Just A Title\n\nSome body text with no subheadings.\n"

    sections = split_markdown_sections(doc)

    assert len(sections) == 1
    assert sections[0].heading == "Just A Title"
    assert sections[0].text == doc


def test_empty_document_yields_no_sections() -> None:
    assert split_markdown_sections("") == []
    assert split_markdown_sections("   \n  ") == []
