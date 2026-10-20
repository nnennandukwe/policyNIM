"""Bind standalone evidence to native-library bytes and bounded artifact ownership."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location(
    "standalone_verifier", SCRIPTS / "verify_standalone.py"
)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SCRIPTS))
try:
    spec.loader.exec_module(verifier)
finally:
    sys.path.remove(str(SCRIPTS))


def test_native_library_change_invalidates_artifact_identity(tmp_path: Path) -> None:
    """A matching launcher can't hide a missing or substituted vector extension."""
    command = tmp_path / "policynim"
    command.write_bytes(b"launcher")
    native = tmp_path / "_internal/sqlite_vec/vec0.so"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"reviewed native extension")
    original = verifier.artifact_identity(command)
    assert original["file_count"] == 2
    native.write_bytes(b"substituted native extension")
    assert verifier.artifact_identity(command)["tree_sha256"] != original["tree_sha256"]
    native.unlink()
    assert verifier.artifact_identity(command)["tree_sha256"] != original["tree_sha256"]


def test_artifact_identity_rejects_external_symlink(tmp_path: Path) -> None:
    """The receipt can't omit bytes reached outside its recorded artifact root."""
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    command = artifact / "policynim"
    command.write_bytes(b"launcher")
    external = tmp_path / "unowned.so"
    external.write_bytes(b"outside")
    try:
        (artifact / "native.so").symlink_to(external)
    except OSError:
        pytest.skip("Symlink creation isn't permitted on this runner.")
    with pytest.raises(RuntimeError, match="symlink escapes"):
        verifier.artifact_identity(command)


def test_artifact_identity_doesnt_confuse_newlines_with_record_boundaries(tmp_path: Path) -> None:
    """Two owned symlink layouts can't serialize as one newline-delimited record stream."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in [first, second]:
        root.mkdir()
        (root / "policynim").write_bytes(b"launcher")
    try:
        (first / "a").symlink_to("policynim\nlink:b:policynim")
        (second / "a").symlink_to("policynim")
        (second / "b").symlink_to("policynim")
    except OSError:
        pytest.skip("Symlink creation isn't permitted on this runner.")
    observed_first = verifier.artifact_identity(first / "policynim")
    observed_second = verifier.artifact_identity(second / "policynim")
    assert observed_first["file_count"] == observed_second["file_count"] == 1
    assert observed_first["tree_sha256"] != observed_second["tree_sha256"]


def test_wrong_release_version_fails_before_generation_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A functional command from another release can't earn a passed receipt."""
    command = tmp_path / "policynim"
    command.write_bytes(b"launcher")
    lockfile = tmp_path / "uv.lock"
    lockfile.write_bytes(b"locked")
    calls: list[list[str]] = []

    def respond(arguments: list[str], **kwargs: object) -> str:
        """Expose a wrong release before any generation or provider work."""
        calls.append(arguments)
        return "Usage: policynim" if arguments == ["--help"] else "0.0.1"

    monkeypatch.setattr(verifier.checks, "run_command", respond)
    args = argparse.Namespace(
        command=command,
        lockfile=lockfile,
        lock_digest="sha256:" + hashlib.sha256(b"locked").hexdigest(),
        revision="a" * 40,
        bundle_digest="sha256:" + "b" * 64,
        expected_version="0.1.0",
    )
    with pytest.raises(RuntimeError, match="Standalone version differs"):
        verifier.verify(args)
    assert calls == [["--help"], ["--version"]]
