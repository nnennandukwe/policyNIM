"""Bind standalone evidence to native-library bytes and bounded artifact ownership."""

from __future__ import annotations

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
