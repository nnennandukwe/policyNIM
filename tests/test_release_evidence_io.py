"""Canonical report replacement survives pre-commit failures and interruption."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture
def evidence_io(monkeypatch: pytest.MonkeyPatch):
    """Import the actual script helper without installing another package."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("release_evidence_io")


@pytest.mark.parametrize("phase", ["create", "write", "flush", "fsync", "close", "replace"])
def test_report_failure_preserves_prior_bytes_and_cleans_only_owned_stage(
    tmp_path: Path,
    evidence_io,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    """Each effect before replacement leaves the prior canonical report readable."""
    output = tmp_path / "report.json"
    output.write_bytes(b'{"status":"prior"}')
    unrelated = tmp_path / ".policynim-evidence-unrelated.tmp"
    unrelated.write_bytes(b"manual file")
    original = evidence_io.tempfile.NamedTemporaryFile
    primary = OSError("fixture phase failure")

    class FailingStage:
        """Inject only the selected real writer boundary and close its actual file."""

        def __init__(self, stream) -> None:
            """Retain the actual staged file and its descriptor."""
            self.stream = stream
            self.name = stream.name

        def __enter__(self):
            """Expose the wrapped stage to the actual coordinator."""
            return self

        def __exit__(self, *args):
            """Always close the descriptor before surfacing a close failure."""
            self.stream.close()
            if phase == "close":
                raise primary

        def write(self, payload: bytes) -> int:
            """Fail or write through at the selected boundary."""
            if phase == "write":
                raise primary
            return self.stream.write(payload)

        def flush(self) -> None:
            """Fail or flush through at the selected boundary."""
            if phase == "flush":
                raise primary
            self.stream.flush()

        def fileno(self) -> int:
            """Return the actual descriptor for the real file-sync boundary."""
            return self.stream.fileno()

    def stage(**options):
        """Create a real owned sibling except when creation itself fails."""
        if phase == "create":
            raise primary
        return FailingStage(original(**options))

    def fail(*args: object) -> None:
        """Reject one effect with the same primary exception instance."""
        raise primary

    monkeypatch.setattr(evidence_io.tempfile, "NamedTemporaryFile", stage)
    if phase == "fsync":
        monkeypatch.setattr(evidence_io.os, "fsync", fail)
    if phase == "replace":
        monkeypatch.setattr(evidence_io.os, "replace", fail)
    with pytest.raises(OSError) as caught:
        evidence_io.write_document(output, b'{"status":"new"}')
    assert caught.value is primary
    assert output.read_bytes() == b'{"status":"prior"}'
    assert unrelated.read_bytes() == b"manual file"
    assert set(tmp_path.iterdir()) == {output, unrelated}


def test_actual_rename_failure_keeps_prior_and_retry_succeeds(
    tmp_path: Path,
    evidence_io,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real invalid rename target fails before commit, then the same writer can retry."""
    output = tmp_path / "report.json"
    output.write_bytes(b"prior")
    directory = tmp_path / "blocked-directory"
    directory.mkdir()
    replace = os.replace

    def reject(source: Path, destination: Path) -> None:
        """Exercise the actual OS rename error without touching the canonical report."""
        replace(source, directory)

    with monkeypatch.context() as fault:
        fault.setattr(evidence_io.os, "replace", reject)
        with pytest.raises(OSError):
            evidence_io.write_document(output, b"new")
    assert output.read_bytes() == b"prior"
    assert not list(tmp_path.glob(".policynim-evidence-*.tmp"))
    evidence_io.write_document(output, b"new")
    assert output.read_bytes() == b"new"


@pytest.mark.parametrize("after_commit", [False, True])
def test_interruption_preserves_complete_prior_or_new_document(
    tmp_path: Path,
    evidence_io,
    monkeypatch: pytest.MonkeyPatch,
    after_commit: bool,
) -> None:
    """Interruption doesn't imply rollback once the replacement has committed."""
    output = tmp_path / "report.json"
    output.write_bytes(b'{"status":"prior"}')
    replace = os.replace

    def interrupt(source: Path, destination: Path) -> None:
        """Interrupt immediately before or after the exact commit operation."""
        if after_commit:
            replace(source, destination)
        raise KeyboardInterrupt()

    monkeypatch.setattr(evidence_io.os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        evidence_io.write_document(output, b'{"status":"new"}')
    assert output.read_bytes() == (b'{"status":"new"}' if after_commit else b'{"status":"prior"}')
    assert not list(tmp_path.glob(".policynim-evidence-*.tmp"))


def test_cleanup_failure_retains_primary_cause_and_owned_recovery_file(
    tmp_path: Path,
    evidence_io,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlink failure can't hide the report failure or remove another invocation's files."""
    output = tmp_path / "report.json"
    output.write_bytes(b"prior")
    primary = OSError("fixture replace failure")
    unlink = Path.unlink

    def reject_replace(*args: object) -> None:
        """Fail the commit operation before canonical state changes."""
        raise primary

    def reject_unlink(path: Path, *args, **kwargs) -> None:
        """Keep the owned stage for recovery while the coordinator propagates its primary cause."""
        if path.name.startswith(".policynim-evidence-"):
            raise PermissionError("private fixture cleanup message")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(evidence_io.os, "replace", reject_replace)
    monkeypatch.setattr(Path, "unlink", reject_unlink)
    with pytest.raises(OSError) as caught:
        evidence_io.write_document(output, b"new")
    assert caught.value is primary and output.read_bytes() == b"prior"
    assert "PermissionError" in primary.__notes__[0]
    assert "private fixture" not in primary.__notes__[0]
    assert len(list(tmp_path.glob(".policynim-evidence-*.tmp"))) == 1


def test_links_special_files_and_oversized_documents_are_rejected(
    tmp_path: Path,
    evidence_io,
) -> None:
    """Bounded regular-file contracts reject unsafe inputs before reading or replacing."""
    target = tmp_path / "target"
    target.write_bytes(b"manual")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating test symlinks isn't supported here.")
    with pytest.raises(ValueError):
        evidence_io.read_bytes(link)
    with pytest.raises(ValueError):
        evidence_io.write_document(link, b"new")
    with pytest.raises(ValueError):
        evidence_io.write_document(target, b"x" * (evidence_io.MAX_DOCUMENT_BYTES + 1))
    assert target.read_bytes() == b"manual"
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        with pytest.raises(ValueError):
            evidence_io.read_bytes(fifo)


def test_late_output_link_is_preserved_instead_of_followed(
    tmp_path: Path,
    evidence_io,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A type change observed after staging aborts before replacing another file."""
    output = tmp_path / "report.json"
    output.write_bytes(b"prior")
    manual = tmp_path / "manual.json"
    manual.write_bytes(b"manual")
    sync = os.fsync

    def change_output(descriptor: int) -> None:
        """Insert a linked output at the real file-sync boundary."""
        sync(descriptor)
        output.unlink()
        try:
            output.symlink_to(manual)
        except OSError:
            pytest.skip("Creating test symlinks isn't supported here.")

    monkeypatch.setattr(evidence_io.os, "fsync", change_output)
    with pytest.raises(ValueError):
        evidence_io.write_document(output, b"new")
    assert output.is_symlink() and manual.read_bytes() == b"manual"
    assert not list(tmp_path.glob(".policynim-evidence-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="This witness renames an open staged file.")
def test_replaced_staging_file_is_never_committed_or_deleted(
    tmp_path: Path,
    evidence_io,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed staged inode remains unowned and the prior canonical file stays intact."""
    output = tmp_path / "report.json"
    output.write_bytes(b"prior")
    sync = os.fsync

    def replace_stage(descriptor: int) -> None:
        """Replace the owned name after its bytes are synced, before the coordinator commits."""
        sync(descriptor)
        (stage,) = tmp_path.glob(".policynim-evidence-*.tmp")
        stage.rename(tmp_path / "displaced-owned-stage")
        stage.write_bytes(b"unowned replacement")

    monkeypatch.setattr(evidence_io.os, "fsync", replace_stage)
    with pytest.raises(ValueError) as caught:
        evidence_io.write_document(output, b"new")
    assert output.read_bytes() == b"prior"
    (stage,) = tmp_path.glob(".policynim-evidence-*.tmp")
    assert stage.read_bytes() == b"unowned replacement"
    assert "ValueError" in caught.value.__notes__[0]
