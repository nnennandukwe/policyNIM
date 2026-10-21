"""Versioned release notes use the actual selected project and preserve failed outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "scripts/check_release_notes.py"


def test_backticks_in_separate_paragraphs_cant_expose_hidden_notes(tmp_path: Path) -> None:
    """The public renderer refuses actual hidden comments despite unrelated unmatched ticks."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [0.2.0]\nShow an unmatched ` tick.\n\n"
        "<!-- private-hidden-notes-sentinel -->\n\nAnother unmatched ` tick.\n"
    )
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
    assert "private-hidden-notes-sentinel" not in process.stdout + process.stderr
    assert output.read_text() == "prior reviewed notes"


def test_fenced_version_heading_cant_truncate_selected_notes(tmp_path: Path) -> None:
    """A literal Markdown example stays inside the current version's rendered section."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    body = (
        "Example:\n\n```markdown\n## [0.1.0]\nLiteral earlier heading.\n```\n\nRetain later entry."
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [0.2.0]\n" + body + "\n\n## [0.1.0]\nEarlier notes.\n"
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
    assert output.read_text() == f"# PolicyNIM 0.2.0\n\n{body}\n"


@pytest.mark.parametrize(
    "body",
    [
        "",
        "  \n",
        "<!-- pending -->",
        "<!-- pending",
        "<!-- pending\nsecond line",
        "TODO",
        "- TBD",
        "### Added",
        "### Added\n\n### Fixed",
        "### Added\n\n**TODO**\n\n### Fixed\n\n- TBD",
        "[TBD](https://example.invalid)",
        "coming\nsoon",
        "---",
    ],
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


@pytest.mark.parametrize("literal", ["`<!--`", "`<!-- quoted -->`", "``<!-- `quoted` -->``"])
def test_literal_comment_in_code_keeps_later_release_entries(tmp_path: Path, literal: str) -> None:
    """The real renderer preserves quoted syntax without treating it as hidden drafting text."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    body = f"- Show {literal}.\n\n- Retain later entry."
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.2.0]\n" + body)
    output = tmp_path / "notes.md"
    process = subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(tmp_path), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 0
    assert output.read_text() == f"# PolicyNIM 0.2.0\n\n{body}\n"


@pytest.mark.parametrize(
    "body",
    [
        "Valid entry.\n\n<!-- hidden drafting detail -->\nLater entry.",
        "Valid entry <!-- hidden drafting detail --> after.",
        "Valid entry.\n\n<!-- unfinished drafting detail",
        "<div>\nValid entry.\n<!-- hidden drafting detail -->\n</div>",
    ],
)
def test_hidden_comments_refuse_rendering_and_preserve_prior_notes(
    tmp_path: Path, body: str
) -> None:
    """Actual HTML comment tokens fail closed instead of silently changing authored notes."""
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
    assert process.returncode == 1 and output.read_text() == "prior reviewed notes"
    assert json.loads(process.stdout)["reason"] == "invalid_version_notes"
    assert "drafting detail" not in process.stdout + process.stderr


@pytest.mark.parametrize(
    "body",
    [
        "```markdown\n<!-- literal -->\n## [Unreleased]\n## [0.2.0]\n```",
        "~~~html\n<!-- literal -->\n~~~",
        "    <!-- literal -->",
    ],
)
def test_fenced_and_indented_comment_examples_are_retained(tmp_path: Path, body: str) -> None:
    """Real code tokens preserve literal comments and version-heading examples byte for byte."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.2.0]\n\n" + body + "\n")
    output = tmp_path / "notes.md"
    process = subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(tmp_path), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 0
    assert output.read_text() == f"# PolicyNIM 0.2.0\n\n{body}\n"


def test_comments_elsewhere_cant_supply_version_headings(tmp_path: Path) -> None:
    """Hidden headings outside the selected body don't add duplicate or missing sections."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "<!--\n## [Unreleased]\n## [0.2.0]\nHidden earlier draft.\n-->\n\n"
        "## [Unreleased]\n\n## [0.2.0]\nReviewed visible entry.\n"
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
    assert output.read_text() == "# PolicyNIM 0.2.0\n\nReviewed visible entry.\n"


def test_setext_version_heading_uses_its_complete_line_map(tmp_path: Path) -> None:
    """A parsed two-line h2 heading ends before the selected note body starts."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "[Unreleased]\n------------\n\n[0.2.0]\n-------\n\nReviewed visible entry.\n"
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
    assert output.read_text() == "# PolicyNIM 0.2.0\n\nReviewed visible entry.\n"


def test_an_unrelated_h2_cant_supply_empty_versions_notes(tmp_path: Path) -> None:
    """Content in the next top-level section can't fill an empty selected release."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [0.2.0]\n### Added\n\n## Contributor guide\nUnrelated text.\n"
    )
    output = tmp_path / "notes.md"
    output.write_text("prior reviewed notes")
    process = subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(tmp_path), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 1 and output.read_text() == "prior reviewed notes"


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


@pytest.mark.parametrize("filename", ["CHANGELOG.md", "pyproject.toml"])
def test_notes_output_cant_replace_the_version_or_authored_source(tmp_path: Path, filename) -> None:
    """An output typo can't replace the source that justified the rendered notes."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [0.2.0]\nBeta keys remain supported.\n"
    )
    output = tmp_path / filename
    before = output.read_bytes()
    process = subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(tmp_path), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 2 and output.read_bytes() == before


def test_commented_version_heading_isnt_a_release_section(tmp_path: Path) -> None:
    """A hidden planning section can't supply the current version's release notes."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n<!--\n## [0.2.0]\nHidden planning notes.\n-->\n"
    )
    output = tmp_path / "notes.md"
    output.write_text("prior notes")
    process = subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(tmp_path), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 1 and output.read_text() == "prior notes"
