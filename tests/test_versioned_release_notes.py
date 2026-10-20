"""Versioned release notes use the actual selected project and preserve failed outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "scripts/check_release_notes.py"


@pytest.mark.parametrize(
    "body",
    ["", "  \n", "<!-- pending -->", "<!-- pending", "<!-- pending\nsecond line", "TODO", "- TBD"],
)
def test_empty_or_placeholder_notes_fail_before_render(tmp_path: Path, body: str) -> None:
    """A version heading alone can't authorize rendered release notes."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.2.0]\n" + body)
    output = tmp_path / "notes.md"
    output.write_text("prior reviewed notes")
    process = subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(tmp_path), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 1
    assert json.loads(process.stdout)["status"] == "failed"
    assert output.read_text() == "prior reviewed notes"


def test_actual_version_body_is_rendered_without_invented_artifact_claims(tmp_path: Path) -> None:
    """Render the current version only and don't promote candidate notes to deployment facts."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [0.2.0] - Candidate\nBeta keys remain supported.\n"
        "\n## [0.1.0]\nEarlier notes.\n"
    )
    output = tmp_path / "notes.md"
    process = subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(tmp_path), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 0
    assert json.loads(process.stdout)["version"] == "0.2.0"
    assert output.read_text() == "# PolicyNIM 0.2.0\n\nBeta keys remain supported.\n"


def test_wrong_version_is_nonzero_and_keeps_prior_output(tmp_path: Path) -> None:
    """An earlier version's notes can't satisfy the selected project version."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.1.0]\nEarlier notes.\n")
    output = tmp_path / "notes.md"
    output.write_text("prior")
    process = subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(tmp_path), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 1 and output.read_text() == "prior"
