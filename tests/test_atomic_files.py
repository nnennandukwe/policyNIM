"""Tests for shared atomic text-artifact staging."""

from __future__ import annotations

from pathlib import Path

from policynim.atomic_files import stage_text_artifact


def test_stage_text_artifact_writes_sibling_temp_file_with_optional_newline(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "reports" / "session-1.json"

    staged_path = stage_text_artifact(
        destination,
        content='{"session_id":"session-1"}',
        ensure_trailing_newline=True,
    )

    assert staged_path.parent == destination.parent
    assert staged_path.name.startswith(f".{destination.name}.")
    assert staged_path.suffix == ".tmp"
    assert staged_path.read_text(encoding="utf-8") == '{"session_id":"session-1"}\n'
