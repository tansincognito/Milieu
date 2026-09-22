"""Markdown section splitting for the Drive connector (§6.2: "Drive documents are split on
markdown headings. Each section is one source record.").

Splits on `## ` (h2) headings. Anything before the first h2 (typically the `# ` title plus
metadata lines) becomes its own leading section, titled after the `# ` heading if present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_H1_RE = re.compile(r"^#\s+(.*)$", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class MarkdownSection:
    heading: str
    index: int
    text: str  # exact substring of the original document


def split_markdown_sections(document_text: str) -> list[MarkdownSection]:
    h2_matches = list(_H2_RE.finditer(document_text))

    if not h2_matches:
        h1_match = _H1_RE.search(document_text)
        heading = h1_match.group(1).strip() if h1_match else "Document"
        stripped = document_text.strip()
        if not stripped:
            return []
        return [MarkdownSection(heading=heading, index=0, text=document_text)]

    sections: list[MarkdownSection] = []
    index = 0

    preamble = document_text[: h2_matches[0].start()]
    if preamble.strip():
        h1_match = _H1_RE.search(preamble)
        heading = h1_match.group(1).strip() if h1_match else "Overview"
        sections.append(MarkdownSection(heading=heading, index=index, text=preamble))
        index += 1

    for i, match in enumerate(h2_matches):
        start = match.start()
        end = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(document_text)
        heading = match.group(1).strip()
        sections.append(MarkdownSection(heading=heading, index=index, text=document_text[start:end]))
        index += 1

    return sections
