"""Check and render nonempty notes for the actual selected project version."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path

from release_evidence_io import read_bytes, require_separate_output, write_document


def version_notes(root: Path) -> tuple[str, str]:
    """Reject missing, duplicate, empty or placeholder notes before any output replacement."""
    project = tomllib.loads(read_bytes(root / "pyproject.toml").decode("utf-8"))
    version = project["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}", version
    ):
        raise ValueError("Invalid project version.")
    changelog = read_bytes(root / "CHANGELOG.md").decode("utf-8")
    changelog = re.sub(
        r"(?P<code>(?<!`)(?P<ticks>`+)(?!`)[\s\S]*?(?<!`)(?P=ticks)(?!`))"
        r"|<!--[\s\S]*?(?:-->|$)",
        lambda match: match[0] if match["code"] is not None else "",
        changelog,
    )
    headers = list(re.finditer(r"^## \[([^\]\n]+)\][^\n]*$", changelog, re.MULTILINE))
    current = [index for index, match in enumerate(headers) if match[1] == version]
    if len(current) != 1 or sum(match[1] == "Unreleased" for match in headers) != 1:
        raise ValueError("Missing or duplicate version/Unreleased section.")
    index = current[0]
    end = headers[index + 1].start() if index + 1 < len(headers) else len(changelog)
    body = changelog[headers[index].end() : end].strip()
    content = re.sub(r"(?m)^[ \t]{0,3}#{1,6}(?:[ \t]+|$)[^\n]*(?:\n|$)", "", body)
    plain = content.strip("#*- \t\r\n.!:").casefold()
    if not plain or plain in {"todo", "tbd", "placeholder", "coming soon"}:
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
