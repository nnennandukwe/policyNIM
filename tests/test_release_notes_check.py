"""Release notes contract tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_release_notes.py"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_release_notes", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_notes_check_passes_for_current_changelog() -> None:
    """Require public release notes for the currently packaged version."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "json"],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "passed"
    assert payload["version"] == "0.1.1"
    assert checks["changelog_exists"]["status"] == "passed"
    assert checks["unreleased_section"]["status"] == "passed"
    assert checks["current_version_section"]["status"] == "passed"
    assert "CHANGELOG.md" in checks["current_version_section"]["evidence"]


def test_release_notes_check_fails_when_current_version_is_missing(tmp_path: Path) -> None:
    """Hold release packaging when the changelog does not cover the package version."""
    module = _load_script_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "policynim"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- Draft notes.\n\n## [0.1.0]\n\n- Old notes.\n",
        encoding="utf-8",
    )

    payload = module.run_release_notes_check(repo_root=repo)
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "failed"
    assert payload["required_passed"] is False
    assert checks["current_version_section"]["status"] == "failed"
    assert "9.9.9" in checks["current_version_section"]["next_step"]


def test_release_notes_check_writes_github_release_notes_from_current_version(
    tmp_path: Path,
) -> None:
    """Publish the same versioned changelog notes that the local release gate verifies."""
    repo = tmp_path / "repo"
    repo.mkdir()
    notes_path = tmp_path / "release-notes.md"
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "policynim"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (repo / "CHANGELOG.md").write_text(
        "\n".join(
            [
                "# Changelog",
                "",
                "## [Unreleased]",
                "",
                "- Future private note.",
                "",
                "## [1.2.3]",
                "",
                "- Added release-backed MCP config evidence.",
                "- Hardened CLI first-run diagnostics.",
                "",
                "## [1.2.2]",
                "",
                "- Older note.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(repo),
            "--write-github-release-notes",
            str(notes_path),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )
    release_notes = notes_path.read_text(encoding="utf-8")

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Wrote GitHub release notes" in result.stdout
    assert release_notes.startswith("# PolicyNIM 1.2.3\n")
    assert "- Added release-backed MCP config evidence." in release_notes
    assert "- Hardened CLI first-run diagnostics." in release_notes
    assert "Release Artifacts" in release_notes
    assert "RELEASE_MANIFEST.json" in release_notes
    assert "SHA256SUMS" in release_notes
    assert "artifact attestations" in release_notes
    assert "gh attestation verify" in release_notes
    assert "Future private note" not in release_notes
    assert "Older note" not in release_notes
