"""Check and render nonempty notes for the actual selected project version."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token
from release_evidence_io import read_bytes, require_separate_output, write_document


def _has_html_comment(token: Token) -> bool:
    """Refuse hidden comment syntax in parsed HTML while preserving quoted code tokens."""
    return (token.type in {"html_block", "html_inline"} and "<!--" in token.content) or any(
        _has_html_comment(child) for child in token.children or []
    )


def version_notes(root: Path) -> tuple[str, str]:
    """Reject missing, duplicate, empty or placeholder notes before any output replacement."""
    project = tomllib.loads(read_bytes(root / "pyproject.toml").decode("utf-8"))
    version = project["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}", version
    ):
        raise ValueError("Invalid project version.")
    changelog = read_bytes(root / "CHANGELOG.md").decode("utf-8")
    parser = MarkdownIt("commonmark")
    tokens = parser.parse(changelog)
    headers: list[tuple[str | None, int, int]] = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag in {"h1", "h2"} and token.level == 0:
            assert token.map is not None
            title = tokens[index + 1].content if token.tag == "h2" else ""
            label, closing, _ = title[1:].partition("]") if title.startswith("[") else ("", "", "")
            headers.append((label if closing else None, token.map[0], token.map[1]))
    current = [index for index, header in enumerate(headers) if header[0] == version]
    if len(current) != 1 or sum(header[0] == "Unreleased" for header in headers) != 1:
        raise ValueError("Missing or duplicate version/Unreleased section.")
    index = current[0]
    # CommonMark counts CR/LF endings; other Unicode separators remain source content.
    lines = re.split(r"(?<=\n)|(?<=\r)(?!\n)", changelog)
    start = headers[index][2]
    end = headers[index + 1][1] if index + 1 < len(headers) else len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    body = "".join(lines[start:end]).rstrip("\r\n")
    body_tokens = parser.parse(body)
    if any(_has_html_comment(token) for token in body_tokens):
        raise ValueError("Current version contains an HTML comment; finish its notes first.")
    content: list[str] = []
    for index, token in enumerate(body_tokens):
        if token.type in {"fence", "code_block"}:
            content.append(token.content)
        elif (
            token.type == "inline" and index > 0 and body_tokens[index - 1].type == "paragraph_open"
        ):
            content.append(
                "".join(
                    child.content if child.type in {"text", "code_inline", "image"} else " "
                    for child in token.children or []
                    if child.type in {"text", "code_inline", "image", "softbreak", "hardbreak"}
                )
            )
    plain = [" ".join(value.strip("#*- \t\r\n.!:").split()).casefold() for value in content]
    if not any(
        value and re.match(r"^(?:todo|tbd|placeholder|coming soon)\b", value) is None
        for value in plain
    ):
        raise ValueError("Current version needs nonempty release notes.")
    return version, body


def main() -> int:
    """Return an explicit failed gate while preserving any earlier notes on validation failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        version, body = version_notes(args.repo_root)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "gate": "release_notes",
                    "reason": "invalid_version_notes",
                    "error_class": type(exc).__name__,
                }
            )
        )
        return 1
    if args.output is not None:
        try:
            require_separate_output(
                args.output, [args.repo_root / "CHANGELOG.md", args.repo_root / "pyproject.toml"]
            )
            write_document(args.output, f"# PolicyNIM {version}\n\n{body}\n".encode())
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "gate": "release_notes",
                        "version": version,
                        "reason": "notes_write_failed",
                        "error_class": type(exc).__name__,
                        "recovery": (
                            "Choose an output separate from project inputs and inspect its parent."
                        ),
                    }
                )
            )
            return 2
    print(
        json.dumps(
            {
                "status": "passed",
                "gate": "release_notes",
                "version": version,
                "rendered": args.output is not None,
                "notes_sha256": hashlib.sha256(
                    f"# PolicyNIM {version}\n\n{body}\n".encode()
                ).hexdigest(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
