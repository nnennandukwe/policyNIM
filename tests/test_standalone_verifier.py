"""Bind standalone evidence to native-library bytes and bounded artifact ownership."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import zipfile
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable mode contract.")
def test_executable_permission_change_invalidates_artifact_identity(tmp_path: Path) -> None:
    """An archive can't lose its executable launcher while retaining the same receipt hash."""
    command = tmp_path / "policynim"
    command.write_bytes(b"launcher")
    command.chmod(0o755)
    original = verifier.artifact_identity(command)
    command.chmod(0o644)
    assert verifier.artifact_identity(command)["tree_sha256"] != original["tree_sha256"]


@pytest.mark.skipif(os.name == "nt", reason="The failing child uses a POSIX executable script.")
def test_stdio_failure_retains_redacted_child_diagnostics(tmp_path: Path) -> None:
    """Actual child startup failure keeps its bounded stderr and removes known credentials."""
    command = tmp_path / "broken-command"
    command.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "print('startup-diagnostic-marker secret-test-token',file=sys.stderr)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    with pytest.raises(Exception) as failure:
        asyncio.run(
            verifier.checks.check_stdio(
                {**os.environ, "TEST_SECRET": "secret-test-token"}, tmp_path, command=str(command)
            )
        )
    assert "startup-diagnostic-marker" in str(failure.value)
    assert "secret-test-token" not in str(failure.value)


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


@pytest.mark.parametrize("fault", ["traversal", "link", "duplicate", "oversized", "case"])
def test_standalone_archive_rejects_unsafe_payload_before_execution(
    tmp_path: Path, fault: str, monkeypatch
):
    """Archive paths and expansion limits can't write outside the owned extraction root."""
    archive_path = tmp_path / "payload.tar.gz"
    if fault == "case":
        monkeypatch.setattr(verifier.platform, "system", lambda: "Darwin")
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("policynim/policynim")
        info.mode = 0o755
        payload = b"launcher"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
        bad = tarfile.TarInfo(
            {
                "traversal": "../outside",
                "link": "policynim/link",
                "duplicate": "policynim/./policynim",
                "oversized": "policynim/large",
                "case": "policynim/POLICYNIM",
            }[fault]
        )
        if fault == "link":
            bad.type = tarfile.SYMTYPE
            bad.linkname = "../../outside"
        elif fault == "oversized":
            bad.size = 8 * 1024**3 + 1
        archive.addfile(bad)
    destination = tmp_path / "extracted"
    destination.mkdir()
    with pytest.raises((RuntimeError, tarfile.TarError)):
        verifier.unpack_standalone(archive_path, destination)
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize(
    "name", ["../outside", "C:/outside", "policynim.exe", "POLICYNIM.EXE", "CON", "policynim.exe."]
)
def test_standalone_zip_rejects_paths_and_case_collisions(tmp_path: Path, name: str):
    """The Windows archive layout has no traversal or ambiguous case-folded members."""
    archive_path = tmp_path / "payload.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("policynim.exe", b"launcher")
        if name == "policynim.exe":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr(name, b"different")
        else:
            archive.writestr(name, b"different")
    destination = tmp_path / "extracted"
    destination.mkdir()
    with pytest.raises(RuntimeError):
        verifier.unpack_standalone(archive_path, destination)
    assert not (tmp_path / "outside").exists()


@pytest.mark.skipif(os.name == "nt", reason="Requires an executable POSIX launcher.")
def test_archive_cli_executes_archived_entrypoint(tmp_path: Path):
    """The archive-only CLI must execute and reject its actual stale-version payload."""
    archive_path = tmp_path / "payload.tar.gz"
    payload = (
        b'#!/bin/sh\nif [ "$1" = "--help" ]; then echo "Usage: policynim"; else echo 0.0.1; fi\n'
    )
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("policynim/policynim")
        info.size, info.mode = len(payload), 0o755
        archive.addfile(info, io.BytesIO(payload))
    lockfile = tmp_path / "uv.lock"
    lockfile.write_bytes(b"locked")
    output = tmp_path / "receipt.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify_standalone.py"),
            "--archive",
            str(archive_path),
            "--bundle",
            str(tmp_path / "unused.zip"),
            "--bundle-digest",
            "sha256:" + "b" * 64,
            "--lockfile",
            str(lockfile),
            "--lock-digest",
            "sha256:" + hashlib.sha256(b"locked").hexdigest(),
            "--revision",
            "a" * 40,
            "--expected-version",
            "0.1.0",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "failed"
    assert receipt["source_revision"] == "a" * 40
    assert receipt["expected_version"] == "0.1.0"
    assert receipt["public_bundle_digest"] == "sha256:" + "b" * 64
    assert receipt["expected_dependency_lock"] == "sha256:" + hashlib.sha256(b"locked").hexdigest()
    assert receipt["platform"] == verifier.platform.system()
    assert "execution didn't complete" in receipt["context_scope"]
    assert "Standalone version differs" in receipt["message"]
    assert receipt.get("archive_verified") is not True


def test_archive_changed_after_payload_execution_cant_receive_binding(tmp_path: Path, monkeypatch):
    """A private snapshot binds executed bytes; concurrent source replacement is rejected."""
    archive_path = tmp_path / "payload.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("policynim.exe", b"launcher")

    def change_after_execution(args):
        """Change the public archive only after the private extracted payload is selected."""
        assert args.command.read_bytes() == b"launcher"
        archive_path.write_bytes(b"replaced")
        return {"status": "passed"}

    monkeypatch.setattr(verifier, "_verify_command", change_after_execution)
    with pytest.raises(RuntimeError, match="archive changed"):
        verifier.verify(argparse.Namespace(archive=archive_path))


@pytest.mark.parametrize("case_count", [0, 5, 7])
def test_partial_offline_fixture_cant_pass_before_transports(
    tmp_path: Path, monkeypatch, case_count
):
    """A reduced or unexpected synthetic suite can't inherit the six-case release claim."""
    command = tmp_path / "policynim"
    command.write_bytes(b"launcher")
    lockfile = tmp_path / "uv.lock"
    lockfile.write_bytes(b"locked")
    digest = "sha256:" + "b" * 64

    def run_command(arguments, **kwargs):
        """Provide only the checked command's help and version boundary."""
        return "Usage: policynim" if arguments == ["--help"] else "0.1.0"

    def run_cli(arguments, **kwargs):
        """Expose an otherwise successful run whose fixture count differs."""
        if arguments[:2] == ["policies", "validate"]:
            return {"catalog_digest": digest, "effective_count": 15}
        if arguments[:2] == ["generation", "import"]:
            return {"generation_id": "a" * 64}
        if arguments[:2] == ["generation", "inspect"]:
            return {
                "generation_id": "a" * 64,
                "catalog": {"digest": digest},
                "manifest": {"chunk_count": 111},
            }
        return {"runs": [{"metrics": {"overall_pass_rate": 1.0, "case_count": case_count}}]}

    def forbid_transport(*args, **kwargs):
        """Keep incomplete offline evidence from advancing to transport acceptance."""
        pytest.fail("Incomplete fixture advanced to transport verification.")

    monkeypatch.setattr(verifier.checks, "run_command", run_command)
    monkeypatch.setattr(verifier.checks, "run_cli", run_cli)
    monkeypatch.setattr(verifier.checks, "check_stdio", forbid_transport)
    args = argparse.Namespace(
        command=command,
        lockfile=lockfile,
        lock_digest="sha256:" + hashlib.sha256(b"locked").hexdigest(),
        revision="a" * 40,
        bundle=tmp_path / "unused.zip",
        bundle_digest=digest,
        expected_version="0.1.0",
    )
    with pytest.raises(RuntimeError, match="requires exactly 6 cases"):
        verifier.verify(args)


@pytest.mark.skipif(os.name == "nt", reason="The failing child uses a POSIX executable script.")
def test_failed_command_receipt_keeps_requested_identity(tmp_path: Path) -> None:
    """Actual failed command verification retains requested context without a passed claim."""
    command = tmp_path / "policynim"
    command.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "print('Usage: policynim' if '--help' in sys.argv else '0.0.1')\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    lockfile = tmp_path / "uv.lock"
    lockfile.write_bytes(b"locked")
    output = tmp_path / "failure.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify_standalone.py"),
            "--command",
            str(command),
            "--bundle",
            str(tmp_path / "unused.zip"),
            "--bundle-digest",
            "sha256:" + "b" * 64,
            "--lockfile",
            str(lockfile),
            "--lock-digest",
            "sha256:" + hashlib.sha256(b"locked").hexdigest(),
            "--revision",
            "a" * 40,
            "--expected-version",
            "0.1.0",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "failed"
    assert receipt["source_revision"] == "a" * 40
    assert receipt["expected_version"] == "0.1.0"
    assert receipt["public_bundle_digest"] == "sha256:" + "b" * 64
    assert receipt["expected_dependency_lock"] == "sha256:" + hashlib.sha256(b"locked").hexdigest()
    assert receipt["platform"] == verifier.platform.system()
    assert "execution didn't complete" in receipt["context_scope"]
    assert "Standalone version differs" in receipt["message"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permission contract.")
@pytest.mark.parametrize("root_directory", [False, True])
def test_artifact_identity_includes_directory_permissions(tmp_path: Path, root_directory: bool):
    """Directory traversal changes must invalidate the artifact identity too."""
    artifact = tmp_path / "artifact"
    internal = artifact / "_internal"
    internal.mkdir(parents=True)
    command = artifact / "policynim"
    command.write_bytes(b"launcher")
    (internal / "native.so").write_bytes(b"library")
    target = artifact if root_directory else internal
    target.chmod(0o755)
    original = verifier.artifact_identity(command)
    target.chmod(0o700)
    assert verifier.artifact_identity(command)["tree_sha256"] != original["tree_sha256"]


@pytest.mark.skipif(os.name == "nt", reason="The failing child uses a POSIX executable script.")
@pytest.mark.parametrize("secret_size", [14000, 20000])
def test_stdio_failure_cant_leak_credential_suffix_across_capture_boundary(
    tmp_path: Path, secret_size: int
):
    """A real failing child can't leak a known secret after bounded capture cuts its start."""
    secret = "x" * secret_size + "private-credential-suffix-marker"
    command = tmp_path / "broken-command"
    command.write_text(
        f"#!{sys.executable}\nimport os,sys\n"
        "print(os.environ['TEST_SECRET'],file=sys.stderr)\n"
        "print('padding-' * 400,file=sys.stderr)\n"
        "print('safe-diagnostic-end-marker',file=sys.stderr)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    with pytest.raises(Exception) as failure:
        asyncio.run(
            verifier.checks.check_stdio(
                {**os.environ, "TEST_SECRET": secret}, tmp_path, command=str(command)
            )
        )
    assert "private-credential-suffix-marker" not in str(failure.value)
    if secret_size <= 16000:
        assert "safe-diagnostic-end-marker" in str(failure.value)
    else:
        assert "omitted" in str(failure.value)


@pytest.mark.parametrize("artifact_kind", ["standalone", "wheel"])
def test_verification_children_use_owned_home_without_changing_parent(
    tmp_path, monkeypatch, artifact_kind
):
    """Installed verification can't merge a real user profile into its packaged-source check."""
    command = tmp_path / "policynim"
    command.write_bytes(b"launcher")
    lockfile = tmp_path / "uv.lock"
    lockfile.write_bytes(b"locked")
    parent_home = os.environ.get("HOME")
    parent_profile = os.environ.get("USERPROFILE")

    class ReachedProbe(RuntimeError):
        """Stop before any fixture provider or transport work."""

    def inspect_child(arguments, *, env, cwd, **kwargs):
        """Inspect the actual environment handed to the first child command."""
        child_home = Path(env["HOME"])
        assert child_home.is_relative_to(cwd) and child_home.is_dir()
        assert env["USERPROFILE"] == str(child_home)
        raise ReachedProbe("owned child environment observed")

    monkeypatch.setattr(verifier.checks, "run_command", inspect_child)
    monkeypatch.setattr(verifier.checks, "check_wheel", lambda *args: {})
    monkeypatch.setattr(verifier.checks, "check_dependencies", lambda *args: {})
    args = argparse.Namespace(
        command=command,
        wheel=tmp_path / "unused.whl",
        requirements=tmp_path / "unused.txt",
        lockfile=lockfile,
        lock_digest="sha256:" + hashlib.sha256(b"locked").hexdigest(),
        revision="a" * 40,
        bundle_digest="sha256:" + "b" * 64,
        expected_version="0.1.0",
    )
    with pytest.raises(ReachedProbe):
        (verifier.verify if artifact_kind == "standalone" else verifier.checks.verify)(args)
    assert os.environ.get("HOME") == parent_home
    assert os.environ.get("USERPROFILE") == parent_profile


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "mkfifo"), reason="POSIX special-file contract."
)
def test_artifact_identity_rejects_real_fifo_without_reading_it(tmp_path: Path):
    """A real FIFO can't disappear from the claimed complete artifact identity."""
    command = tmp_path / "policynim"
    command.write_bytes(b"launcher")
    os.mkfifo(tmp_path / "special-entry")
    with pytest.raises(RuntimeError, match="special file"):
        verifier.artifact_identity(command)
