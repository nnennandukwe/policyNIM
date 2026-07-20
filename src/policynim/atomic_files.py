"""Helpers for staging text artifacts before atomic replacement."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile


def stage_text_artifact(
    destination: Path,
    *,
    content: str,
    ensure_trailing_newline: bool = False,
) -> Path:
    """Write text to a sibling temp file and return the staged path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            if ensure_trailing_newline and not content.endswith("\n"):
                handle.write("\n")
            staged_path = Path(handle.name)
    except OSError:
        if staged_path is not None:
            with suppress(OSError):
                staged_path.unlink(missing_ok=True)
        raise
    return staged_path
