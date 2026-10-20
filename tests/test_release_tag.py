"""Exercise publication tag agreement against an actual local Git remote."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_release_tag.py"


def git(root: Path, *args: str) -> str:
    """Run Git against an isolated release fixture."""
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.mark.parametrize("annotated", [False, True])
def test_existing_tag_must_resolve_to_artifact_source(tmp_path: Path, annotated: bool) -> None:
    """Both tag forms pass at their source and reject a different dispatch commit."""
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "user.name", "Release Test")
    git(tmp_path, "config", "user.email", "release@example.invalid")
    git(tmp_path, "commit", "--allow-empty", "-m", "tag source")
    tagged = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "tag", *(["-a", "v0.2.0", "-m", "release"] if annotated else ["v0.2.0"]))
    git(tmp_path, "commit", "--allow-empty", "-m", "later dispatch")
    later = git(tmp_path, "rev-parse", "HEAD")
    for revision, expected in [(tagged, 0), (later, 1)]:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--tag",
                "v0.2.0",
                "--revision",
                revision,
                "--remote",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == expected, result.stdout + result.stderr


def test_absent_tag_is_allowed_but_remote_failure_blocks(tmp_path: Path) -> None:
    """An uncreated release can target the source; unknown remote state can't pass."""
    git(tmp_path, "init", "--bare", "--quiet")
    for remote, expected in [(str(tmp_path), 0), (str(tmp_path / "missing"), 1)]:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--tag",
                "v0.2.0",
                "--revision",
                "a" * 40,
                "--remote",
                remote,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == expected, result.stdout + result.stderr
