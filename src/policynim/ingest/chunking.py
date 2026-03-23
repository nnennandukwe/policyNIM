"""Deterministic Markdown section chunking for PolicyNIM ingest.

This module is intentionally self-contained. It extracts heading-aware sections
from ``ParsedDocument.body`` without importing the unfinished parser stack, and it
assembles those sections into stable ``PolicyChunk`` values.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from policynim.errors import InvalidPolicyDocumentError
from policynim.types import DocumentSection, ParsedDocument, PolicyChunk

HEADING_PATH_SEPARATOR = " > "
SECTION_KEY_SEPARATOR = "__"
CHUNK_ID_SEPARATOR = ":"
CHUNK_DUPLICATE_SUFFIX_SEPARATOR = "-"

_ATX_HEADING_RE = re.compile(r"^(?P<indent> {0,3})(?P<marks>#{1,6})(?P<rest>.*)$")
_FENCE_OPEN_RE = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_NORMALIZE_IDENTIFIER_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class _HeadingEvent:
    line: int
    level: int
    title: str


@dataclass(frozen=True, slots=True)
class _FenceState:
    marker: str
    length: int


def chunk_policy_documents(
    documents: Sequence[ParsedDocument],
    *,
    parser: object | None = None,
) -> list[PolicyChunk]:
    """Chunk a list of normalized documents."""
    chunks: list[PolicyChunk] = []
    for document in documents:
        chunks.extend(chunk_policy_document(document, parser=parser))
    return chunks


def chunk_policy_document(
    document: ParsedDocument,
    *,
    parser: object | None = None,
) -> list[PolicyChunk]:
    """Chunk one normalized document into retrievable sections."""
    sections = _extract_sections(document, parser=parser)
    if not sections:
        raise InvalidPolicyDocumentError(
            f"Policy document {document.source_path} did not yield any sections."
        )

    counts: defaultdict[str, int] = defaultdict(int)
    chunks: list[PolicyChunk] = []

    for section in sections:
        section_path = section.heading_path or [document.metadata.title]
        section_key = _section_key(section_path)
        base_chunk_id = f"{document.metadata.policy_id}{CHUNK_ID_SEPARATOR}{section_key}"
        counts[base_chunk_id] += 1
        occurrence = counts[base_chunk_id]
        chunk_id = (
            base_chunk_id
            if occurrence == 1
            else f"{base_chunk_id}{CHUNK_DUPLICATE_SUFFIX_SEPARATOR}{occurrence}"
        )

        chunks.append(
            PolicyChunk(
                chunk_id=chunk_id,
                path=document.source_path,
                section=HEADING_PATH_SEPARATOR.join(section_path),
                lines=_format_line_span(section.start_line, section.end_line),
                text=section.content,
                policy=document.metadata,
            )
        )

    return chunks


def _extract_sections(
    document: ParsedDocument,
    *,
    parser: object | None = None,
) -> list[DocumentSection]:
    """Return heading-aware sections for one parsed document."""
    if parser is not None:
        extract_sections = getattr(parser, "extract_sections", None)
        if callable(extract_sections):
            sections = extract_sections(document)
            if sections:
                return sections

    lines = document.body.splitlines()
    if not lines:
        raise InvalidPolicyDocumentError(
            f"Policy document {document.source_path} does not contain any lines to chunk."
        )
    if not document.body.strip():
        raise InvalidPolicyDocumentError(
            f"Policy document {document.source_path} does not contain usable Markdown content."
        )

    heading_events = _find_heading_events(lines, body_start_line=document.body_start_line)
    if not heading_events:
        return [
            DocumentSection(
                heading_path=[document.metadata.title],
                content=document.body.strip(),
                start_line=document.body_start_line,
                end_line=document.body_start_line + len(lines) - 1,
            )
        ]

    sections: list[DocumentSection] = []
    stack: list[str] = []

    for index, heading in enumerate(heading_events):
        level = heading.level
        title = heading.title or document.metadata.title
        start_line = heading.line
        next_start = (
            heading_events[index + 1].line - 1
            if index + 1 < len(heading_events)
            else document.body_start_line + len(lines) - 1
        )
        if next_start < start_line:
            next_start = start_line

        stack = stack[: max(level - 1, 0)]
        stack.append(title)

        relative_start = _relative_line_index(document.body_start_line, start_line)
        relative_end = _relative_line_index(document.body_start_line, next_start)
        content = "\n".join(lines[relative_start : relative_end + 1]).strip()
        if not content:
            continue

        sections.append(
            DocumentSection(
                heading_path=list(stack),
                content=content,
                start_line=document.body_start_line + relative_start,
                end_line=document.body_start_line + relative_end,
            )
        )

    if not sections:
        raise InvalidPolicyDocumentError(
            f"Policy document {document.source_path} did not yield any non-empty sections."
        )

    return sections


def _find_heading_events(
    lines: Sequence[str],
    *,
    body_start_line: int,
) -> list[_HeadingEvent]:
    heading_events: list[_HeadingEvent] = []
    fence_state: _FenceState | None = None

    for index, line in enumerate(lines, start=1):
        absolute_line = body_start_line + index - 1

        if fence_state is not None:
            if _is_fence_closer(line, fence_state):
                fence_state = None
            continue

        fence_open = _match_fence_opener(line)
        if fence_open is not None:
            fence_state = fence_open
            continue

        heading = _match_atx_heading(line)
        if heading is not None:
            heading_events.append(
                _HeadingEvent(
                    line=absolute_line,
                    level=heading[0],
                    title=heading[1],
                )
            )

    return heading_events


def _match_fence_opener(line: str) -> _FenceState | None:
    match = _FENCE_OPEN_RE.match(line)
    if match is None:
        return None

    marker = match.group("marker")
    return _FenceState(marker=marker[0], length=len(marker))


def _is_fence_closer(line: str, fence_state: _FenceState) -> bool:
    match = re.match(
        rf"^[ ]{{0,3}}(?P<marker>{re.escape(fence_state.marker)}{{{fence_state.length},}})[ \t]*$",
        line,
    )
    return match is not None


def _match_atx_heading(line: str) -> tuple[int, str] | None:
    match = _ATX_HEADING_RE.match(line)
    if match is None:
        return None

    rest = match.group("rest")
    if rest and not rest[0].isspace():
        return None

    level = len(match.group("marks"))
    title = _normalize_heading_text(rest)
    return level, title


def _normalize_heading_text(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""

    # Remove a trailing ATX closing marker sequence while preserving literal hashes
    # that are part of the heading text itself.
    text = re.sub(r"[ \t]+#+[ \t]*$", "", text)
    return " ".join(text.split())


def _relative_line_index(body_start_line: int, absolute_line: int) -> int:
    return absolute_line - body_start_line


def _format_line_span(start_line: int, end_line: int) -> str:
    if end_line < start_line:
        raise InvalidPolicyDocumentError(
            f"Invalid line span requested: start={start_line}, end={end_line}."
        )
    return f"{start_line}-{end_line}"


def _section_key(heading_path: Sequence[str]) -> str:
    parts = [_slugify(part) for part in heading_path if _slugify(part)]
    return SECTION_KEY_SEPARATOR.join(parts) or "document"


def _slugify(value: str) -> str:
    slug = _NORMALIZE_IDENTIFIER_RE.sub("-", value.lower()).strip("-")
    return slug or "section"


__all__ = [
    "chunk_policy_document",
    "chunk_policy_documents",
]
