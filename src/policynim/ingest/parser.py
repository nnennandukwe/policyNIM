"""Format-specific source parsing for PolicyNIM ingest."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Protocol, TypedDict, cast

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import ValidationError

from policynim.errors import InvalidPolicyDocumentError
from policynim.types import (
    RUNTIME_RULE_MATCHER_FIELDS,
    DocumentSection,
    ParsedDocument,
    ParsedRuntimeRule,
    PolicyMetadata,
    RuntimeActionKind,
    RuntimeRuleEffect,
)

_RUNTIME_RULE_ALLOWED_KEYS = {
    "action",
    "effect",
    "reason",
    *RUNTIME_RULE_MATCHER_FIELDS,
}
_NON_STRING_YAML_SCALARS = {"true", "false", "null", "~", "yes", "no", "on", "off"}
_NUMERIC_YAML_SCALAR_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


class DocumentParser(Protocol):
    """Parses source files into normalized documents and sections."""

    def parse(self, source_path: str, text: str) -> ParsedDocument:
        """Normalize one source file into a parsed document."""
        ...

    def extract_sections(self, document: ParsedDocument) -> list[DocumentSection]:
        """Extract heading-aware sections from a parsed document."""
        ...


class _HeadingToken(TypedDict):
    level: int
    title: str
    start_line: int


class MarkdownParser:
    """Markdown parser that tolerates imperfect frontmatter and heading structure."""

    def __init__(self) -> None:
        self._markdown = MarkdownIt("commonmark")

    def parse(self, source_path: str, text: str) -> ParsedDocument:
        """Normalize Markdown content and infer missing metadata."""
        normalized_text = text.lstrip("\ufeff")
        frontmatter, body, body_start_line = _split_frontmatter(normalized_text, source_path)
        if not body.strip():
            raise InvalidPolicyDocumentError(
                f"Policy document {source_path} does not contain usable Markdown content."
            )

        tokens = self._markdown.parse(body)
        headings = _collect_heading_titles(tokens)
        metadata = _normalize_metadata(source_path, frontmatter, headings)
        runtime_rules = _runtime_rules_from_frontmatter(frontmatter.get("runtime_rules"))

        return ParsedDocument(
            source_path=source_path,
            metadata=metadata,
            runtime_rules=runtime_rules,
            body=body,
            body_start_line=body_start_line,
        )

    def extract_sections(self, document: ParsedDocument) -> list[DocumentSection]:
        """Return section blocks with full heading ancestry and source line spans."""
        if not document.body.strip():
            return []

        lines = document.body.splitlines()
        tokens = self._markdown.parse(document.body)
        heading_tokens = _collect_heading_tokens(tokens)
        if not heading_tokens:
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
        body_line_count = len(lines)
        preamble = _build_preamble_section(
            lines=lines,
            title=document.metadata.title,
            body_start_line=document.body_start_line,
            first_heading_line=heading_tokens[0]["start_line"],
        )
        if preamble is not None:
            sections.append(preamble)

        for index, heading in enumerate(heading_tokens):
            level = heading["level"]
            title = heading["title"] or document.metadata.title
            start_line = heading["start_line"]
            next_start = (
                heading_tokens[index + 1]["start_line"] - 1
                if index + 1 < len(heading_tokens)
                else body_line_count
            )
            if next_start < start_line:
                next_start = start_line

            stack = stack[: max(level - 1, 0)]
            stack.append(title)
            content = "\n".join(lines[start_line - 1 : next_start]).strip()
            if not content:
                continue

            sections.append(
                DocumentSection(
                    heading_path=list(stack),
                    content=content,
                    start_line=document.body_start_line + start_line - 1,
                    end_line=document.body_start_line + next_start - 1,
                )
            )

        if not sections:
            raise InvalidPolicyDocumentError(
                f"Policy document {document.source_path} did not yield any non-empty sections."
            )

        return sections


def _split_frontmatter(text: str, source_path: str) -> tuple[dict[str, object], str, int]:
    """Split optional YAML frontmatter from the Markdown body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, 1

    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            closing_index = index
            break

    if closing_index is None:
        raise InvalidPolicyDocumentError(
            f"Policy document {source_path} starts frontmatter but never closes it."
        )

    raw_frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])
    if text.endswith("\n"):
        body = f"{body}\n" if body else ""

    try:
        parsed = _parse_frontmatter_mapping(raw_frontmatter, source_path)
    except InvalidPolicyDocumentError:
        raise
    except Exception as exc:  # pragma: no cover - defensive guard.
        raise InvalidPolicyDocumentError(
            f"Policy document {source_path} has malformed YAML frontmatter."
        ) from exc

    if not isinstance(parsed, dict):
        raise InvalidPolicyDocumentError(
            f"Policy document {source_path} frontmatter must be a mapping."
        )

    return parsed, body, closing_index + 2


def _normalize_metadata(
    source_path: str,
    frontmatter: dict[str, object],
    headings: Sequence[tuple[int, str]],
) -> PolicyMetadata:
    """Normalize metadata, inferring missing fields from the source path and headings."""
    first_h1 = next((title for level, title in headings if level == 1 and title), None)
    first_heading = next((title for _, title in headings if title), None)

    title = (
        _string_value(frontmatter.get("title"))
        or first_h1
        or first_heading
        or _humanize_stem(source_path)
    )
    policy_id = _string_value(frontmatter.get("policy_id")) or _derive_policy_id(source_path)
    domain = _string_value(frontmatter.get("domain")) or _derive_domain(source_path)
    doc_type = _string_value(frontmatter.get("doc_type")) or "guidance"

    return PolicyMetadata(
        policy_id=policy_id,
        title=title,
        doc_type=doc_type,
        domain=domain,
        tags=_string_list(frontmatter.get("tags")),
        grounded_in=_string_list(frontmatter.get("grounded_in")),
    )


def _runtime_rules_from_frontmatter(value: object) -> list[ParsedRuntimeRule]:
    """Return parsed runtime rules from frontmatter, if present."""
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, ParsedRuntimeRule) for item in value):
        return list(value)
    raise InvalidPolicyDocumentError("Policy frontmatter parser produced invalid runtime_rules.")


def _collect_heading_titles(tokens: Sequence[Token]) -> list[tuple[int, str]]:
    """Collect heading titles and levels in document order."""
    return [
        (heading["level"], heading["title"])
        for heading in _collect_heading_tokens(tokens)
        if heading["title"]
    ]


def _collect_heading_tokens(tokens: Sequence[Token]) -> list[_HeadingToken]:
    """Collect heading token metadata in document order."""
    headings: list[_HeadingToken] = []

    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue

        title = ""
        if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
            title = tokens[index + 1].content.strip()

        try:
            level = int(token.tag.removeprefix("h"))
        except ValueError as exc:
            raise InvalidPolicyDocumentError(
                "Encountered a heading with a non-numeric level."
            ) from exc

        headings.append(
            {
                "level": level,
                "title": title,
                "start_line": token.map[0] + 1,
            }
        )

    return headings


def _build_preamble_section(
    *,
    lines: Sequence[str],
    title: str,
    body_start_line: int,
    first_heading_line: int,
) -> DocumentSection | None:
    """Return a synthetic preamble section when content appears before the first heading."""
    if first_heading_line <= 1:
        return None

    content = "\n".join(lines[: first_heading_line - 1]).strip()
    if not content:
        return None

    return DocumentSection(
        heading_path=[title, "Preamble"],
        content=content,
        start_line=body_start_line,
        end_line=body_start_line + first_heading_line - 2,
    )


def _parse_frontmatter_mapping(raw_frontmatter: str, source_path: str) -> dict[str, object]:
    """Parse a narrow YAML frontmatter subset used by the corpus."""
    if not raw_frontmatter.strip():
        return {}

    lines = raw_frontmatter.splitlines()
    data: dict[str, object] = {}
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue

        if raw_line.startswith((" ", "\t")):
            raise InvalidPolicyDocumentError(
                f"Policy document {source_path} has malformed YAML frontmatter."
            )

        match = re.match(r"^([A-Za-z0-9_-]+):(.*)$", raw_line)
        if match is None:
            raise InvalidPolicyDocumentError(
                f"Policy document {source_path} has malformed YAML frontmatter."
            )

        key = match.group(1).strip()
        remainder = match.group(2).strip()
        if key == "runtime_rules":
            if remainder:
                raise _invalid_runtime_rule(
                    source_path,
                    _frontmatter_line_number(index),
                    "runtime_rules must use block-list syntax.",
                )
            runtime_rules, next_index = _parse_runtime_rules(lines, index + 1, source_path)
            if not runtime_rules:
                raise _invalid_runtime_rule(
                    source_path,
                    _frontmatter_line_number(index),
                    "runtime_rules must include at least one rule entry.",
                )
            data[key] = runtime_rules
            index = next_index
            continue

        if remainder:
            data[key] = _parse_frontmatter_scalar_or_list(remainder, source_path)
            index += 1
            continue

        list_value, next_index = _parse_frontmatter_list(lines, index + 1, source_path)
        if list_value is not None:
            data[key] = list_value
            index = next_index
            continue

        if _next_nonblank_line_is_indented(lines, index + 1):
            raise InvalidPolicyDocumentError(
                f"Policy document {source_path} has malformed YAML frontmatter."
            )

        data[key] = ""
        index += 1

    return data


def _parse_runtime_rules(
    lines: Sequence[str],
    start_index: int,
    source_path: str,
) -> tuple[list[ParsedRuntimeRule], int]:
    """Parse the narrow runtime_rules list syntax from frontmatter."""
    rules: list[ParsedRuntimeRule] = []
    index = start_index

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if raw_line.startswith("  - "):
            rule, index = _parse_runtime_rule(lines, index, source_path)
            rules.append(rule)
            continue
        if raw_line.startswith((" ", "\t")):
            raise _invalid_runtime_rule(
                source_path,
                _frontmatter_line_number(index),
                "expected a rule entry beginning with `-`.",
            )
        break

    return rules, index


def _parse_runtime_rule(
    lines: Sequence[str],
    start_index: int,
    source_path: str,
) -> tuple[ParsedRuntimeRule, int]:
    """Parse one runtime rule and preserve its line span."""
    rule_data: dict[str, object] = {}
    seen_keys: set[str] = set()

    key, value, index, last_content_index = _consume_runtime_rule_field(
        lines,
        start_index,
        source_path,
        prefix="  - ",
    )
    rule_data[key] = value
    seen_keys.add(key)

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if raw_line.startswith("  - "):
            break
        if raw_line.startswith("      - "):
            raise _invalid_runtime_rule(
                source_path,
                _frontmatter_line_number(index),
                "matcher list items must follow a matcher-family key.",
            )
        if raw_line.startswith("    "):
            key, value, index, consumed_line_index = _consume_runtime_rule_field(
                lines,
                index,
                source_path,
                prefix="    ",
            )
            if key in seen_keys:
                raise _invalid_runtime_rule(
                    source_path,
                    _frontmatter_line_number(consumed_line_index),
                    f"duplicate rule key `{key}`.",
                )
            rule_data[key] = value
            seen_keys.add(key)
            last_content_index = max(last_content_index, consumed_line_index)
            continue
        if raw_line.startswith((" ", "\t")):
            raise _invalid_runtime_rule(
                source_path,
                _frontmatter_line_number(index),
                "unexpected indentation inside runtime_rules.",
            )
        break

    try:
        rule = ParsedRuntimeRule(
            action=cast(RuntimeActionKind, _required_runtime_rule_string(rule_data.get("action"))),
            effect=cast(RuntimeRuleEffect, _required_runtime_rule_string(rule_data.get("effect"))),
            reason=_required_runtime_rule_string(rule_data.get("reason")),
            path_globs=_runtime_rule_matcher_values(
                rule_data.get("path_globs"),
                source_path=source_path,
                line_number=_frontmatter_line_number(start_index),
            ),
            command_regexes=_runtime_rule_matcher_values(
                rule_data.get("command_regexes"),
                source_path=source_path,
                line_number=_frontmatter_line_number(start_index),
            ),
            url_host_patterns=_runtime_rule_matcher_values(
                rule_data.get("url_host_patterns"),
                source_path=source_path,
                line_number=_frontmatter_line_number(start_index),
            ),
            start_line=_frontmatter_line_number(start_index),
            end_line=_frontmatter_line_number(last_content_index),
        )
    except ValidationError as exc:
        message = exc.errors()[0]["msg"]
        raise _invalid_runtime_rule(
            source_path,
            _frontmatter_line_number(start_index),
            message,
        ) from exc

    return rule, index


def _consume_runtime_rule_field(
    lines: Sequence[str],
    index: int,
    source_path: str,
    *,
    prefix: str,
) -> tuple[str, object, int, int]:
    """Consume one rule field from the supplied line index."""
    raw_line = lines[index]
    if not raw_line.startswith(prefix):
        raise _invalid_runtime_rule(
            source_path,
            _frontmatter_line_number(index),
            "expected a runtime_rules field.",
        )

    field_text = raw_line.removeprefix(prefix)
    match = re.match(r"^([A-Za-z0-9_-]+):(.*)$", field_text)
    if match is None:
        raise _invalid_runtime_rule(
            source_path,
            _frontmatter_line_number(index),
            "expected `key: value` syntax inside runtime_rules.",
        )

    key = match.group(1).strip()
    remainder = match.group(2).strip()
    if key not in _RUNTIME_RULE_ALLOWED_KEYS:
        raise _invalid_runtime_rule(
            source_path,
            _frontmatter_line_number(index),
            f"unknown runtime_rules key `{key}`.",
        )

    if remainder:
        return (
            key,
            _coerce_runtime_rule_value(
                key,
                _parse_frontmatter_scalar_or_list(remainder, source_path),
                source_path=source_path,
                line_number=_frontmatter_line_number(index),
            ),
            index + 1,
            index,
        )

    if key not in RUNTIME_RULE_MATCHER_FIELDS:
        raise _invalid_runtime_rule(
            source_path,
            _frontmatter_line_number(index),
            f"`{key}` must use single-line scalar syntax.",
        )

    values, next_index, last_item_index = _parse_runtime_rule_matcher_list(
        lines,
        index + 1,
        source_path,
    )
    return key, values, next_index, last_item_index if last_item_index is not None else index


def _parse_runtime_rule_matcher_list(
    lines: Sequence[str],
    start_index: int,
    source_path: str,
) -> tuple[list[str], int, int | None]:
    """Parse a matcher-family block list nested under one runtime rule."""
    values: list[str] = []
    index = start_index
    last_item_index: int | None = None

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if raw_line.startswith("      - "):
            raw_value = raw_line.removeprefix("      - ").strip()
            values.append(
                _parse_runtime_rule_matcher_item(
                    raw_value,
                    source_path=source_path,
                    line_number=_frontmatter_line_number(index),
                )
            )
            last_item_index = index
            index += 1
            continue
        if raw_line.startswith("    ") and not raw_line.startswith("      "):
            break
        if raw_line.startswith("  - "):
            break
        if not raw_line.startswith((" ", "\t")):
            break
        raise _invalid_runtime_rule(
            source_path,
            _frontmatter_line_number(index),
            "expected a matcher list item beginning with `-`.",
        )

    return values, index, last_item_index


def _coerce_runtime_rule_value(
    key: str,
    value: object,
    *,
    source_path: str,
    line_number: int,
) -> str | list[str]:
    """Normalize one parsed runtime-rule field into a typed value."""
    if key in RUNTIME_RULE_MATCHER_FIELDS:
        return _runtime_rule_matcher_values(
            value,
            source_path=source_path,
            line_number=line_number,
        )
    return _runtime_rule_scalar_value(
        key,
        value,
        source_path=source_path,
        line_number=line_number,
    )


def _runtime_rule_scalar_value(
    key: str,
    value: object,
    *,
    source_path: str,
    line_number: int,
) -> str:
    """Validate a required runtime-rule scalar field."""
    if not isinstance(value, str):
        raise _invalid_runtime_rule(
            source_path,
            line_number,
            f"`{key}` must be a single string value.",
        )
    normalized = _required_runtime_rule_string(value)
    if not normalized:
        raise _invalid_runtime_rule(
            source_path,
            line_number,
            f"`{key}` must not be empty.",
        )
    return normalized


def _required_runtime_rule_string(value: object) -> str:
    """Return one required runtime-rule scalar as a stripped string."""
    normalized = _string_value(value)
    return normalized or ""


def _runtime_rule_matcher_values(
    value: object,
    *,
    source_path: str,
    line_number: int,
) -> list[str]:
    """Normalize matcher values into a list of validated strings."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid_runtime_rule(
            source_path,
            line_number,
            "matcher families must use list syntax.",
        )
    return [
        _parse_runtime_rule_matcher_item(
            item,
            source_path=source_path,
            line_number=line_number,
        )
        for item in value
    ]


def _parse_runtime_rule_matcher_item(
    value: object,
    *,
    source_path: str,
    line_number: int,
) -> str:
    """Validate one matcher item as a real string, not a YAML typed scalar."""
    if not isinstance(value, str):
        raise _invalid_runtime_rule(
            source_path,
            line_number,
            "matcher list items must be strings.",
        )
    stripped = value.strip()
    if not stripped:
        raise _invalid_runtime_rule(
            source_path,
            line_number,
            "matcher list items must not be empty.",
        )
    if _looks_like_non_string_yaml_scalar(stripped):
        raise _invalid_runtime_rule(
            source_path,
            line_number,
            "matcher list items must be strings, not YAML booleans, nulls, numbers, or maps.",
        )
    return _parse_frontmatter_scalar(stripped, source_path)


def _looks_like_non_string_yaml_scalar(value: str) -> bool:
    """Return whether an unquoted matcher item looks like a non-string YAML scalar."""
    if not value:
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return False
    lowered = value.lower()
    if lowered in _NON_STRING_YAML_SCALARS:
        return True
    if _NUMERIC_YAML_SCALAR_RE.match(value):
        return True
    if (value.startswith("{") and value.endswith("}")) or (
        value.startswith("[") and value.endswith("]")
    ):
        return True
    return False


def _frontmatter_line_number(index: int) -> int:
    """Convert a raw frontmatter index into an absolute file line number."""
    return index + 2


def _invalid_runtime_rule(
    source_path: str,
    line_number: int,
    message: str,
) -> InvalidPolicyDocumentError:
    """Return a consistent runtime-rules parsing error."""
    return InvalidPolicyDocumentError(
        f"Policy document {source_path} has invalid runtime_rules at line {line_number}: {message}"
    )


def _parse_frontmatter_list(
    lines: Sequence[str],
    start_index: int,
    source_path: str,
) -> tuple[list[str] | None, int]:
    """Parse a simple YAML block list."""
    items: list[str] = []
    index = start_index
    saw_item = False

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue

        if not re.match(r"^\s*-\s+", raw_line):
            break

        saw_item = True
        item = re.sub(r"^\s*-\s+", "", raw_line, count=1)
        items.append(_parse_frontmatter_scalar(item.strip(), source_path))
        index += 1

    if not saw_item:
        return None, start_index

    return items, index


def _parse_frontmatter_scalar_or_list(value: str, source_path: str) -> str | list[str]:
    """Parse either a scalar value or a compact inline list."""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_frontmatter_scalar(item, source_path) for item in _split_inline_list(inner)]

    return _parse_frontmatter_scalar(value, source_path)


def _parse_frontmatter_scalar(value: str, source_path: str) -> str:
    """Parse one frontmatter scalar value."""
    if not value:
        return ""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return _unquote_frontmatter_string(value, source_path)

    return value


def _split_inline_list(value: str) -> list[str]:
    """Split a compact YAML inline list into raw items."""
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False

    for char in value:
        if quote is not None:
            current.append(char)
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
            continue

        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue

        if char == ",":
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue

        current.append(char)

    tail = "".join(current).strip()
    if tail:
        items.append(tail)

    if quote is not None:
        raise InvalidPolicyDocumentError("Frontmatter contains an unterminated quoted string.")

    return items


def _unquote_frontmatter_string(value: str, source_path: str) -> str:
    """Remove matching quote marks from a frontmatter scalar."""
    if len(value) < 2:
        raise InvalidPolicyDocumentError(
            f"Policy document {source_path} has malformed YAML frontmatter."
        )

    body = value[1:-1]
    return body.replace(r"\'", "'").replace(r"\"", '"')


def _next_nonblank_line_is_indented(lines: Sequence[str], start_index: int) -> bool:
    """Detect an indented continuation line after a mapping key."""
    for index in range(start_index, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        return lines[index].startswith((" ", "\t"))
    return False


def _derive_policy_id(source_path: str) -> str:
    """Build a stable policy identifier from the repo-relative path."""
    path = PurePosixPath(source_path).with_suffix("")
    parts = list(path.parts)
    if parts and parts[0] == "policies":
        parts = parts[1:]

    normalized = [_slugify(part).upper() for part in parts if _slugify(part)]
    return "-".join(normalized) or "POLICY-DOCUMENT"


def _derive_domain(source_path: str) -> str:
    """Infer the policy domain from the first directory under policies/."""
    path = PurePosixPath(source_path)
    parts = list(path.parts)
    if len(parts) >= 2 and parts[0] == "policies":
        return parts[1]
    return "general"


def _humanize_stem(source_path: str) -> str:
    """Convert a filename stem into a readable title."""
    stem = PurePosixPath(source_path).stem.replace("-", " ").replace("_", " ").strip()
    return stem.title() or "Untitled Policy"


def _string_value(value: object) -> str | None:
    """Normalize a scalar frontmatter field into a string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value).strip() or None


def _string_list(value: object) -> list[str]:
    """Normalize list-like frontmatter values into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = []
        for item in value:
            cleaned = _string_value(item)
            if cleaned:
                values.append(cleaned)
        return values
    cleaned = _string_value(value)
    return [cleaned] if cleaned else []


def _slugify(value: str) -> str:
    """Return a filesystem- and chunk-safe slug."""
    characters = []
    previous_dash = False

    for char in value.lower():
        if char.isalnum():
            characters.append(char)
            previous_dash = False
            continue

        if not previous_dash:
            characters.append("-")
            previous_dash = True

    return "".join(characters).strip("-")
