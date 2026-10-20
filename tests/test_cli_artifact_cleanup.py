"""Real filesystem failure proof for the CLI artifact writer's staged-file cleanup."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from policynim.errors import PolicyNIMError
from policynim.interfaces.cli import _write_cli_artifact_text


@pytest.mark.skipif(
    os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="Requires POSIX directory permission enforcement for an unprivileged user.",
)
def test_real_rename_and_unlink_failure_preserves_artifact_and_reports_retained_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed replace and cleanup retain the old artifact and original cause."""
    directory = tmp_path / "reports"
    directory.mkdir()
    target = directory / "report.json"
    target.write_text("original artifact")
    replace = os.replace
    staged: list[Path] = []

    def deny_replace(source: str, destination: Path) -> None:
        """Remove directory write permission immediately before the actual rename syscall."""
        staged.append(Path(source))
        directory.chmod(0o500)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", deny_replace)
    try:
        with pytest.raises(PolicyNIMError) as caught:
            _write_cli_artifact_text(str(target), "new artifact")
        assert isinstance(caught.value.__cause__, PermissionError)
        assert caught.value.__cause__.errno == errno.EACCES
        assert target.read_text() == "original artifact"
        assert staged[0].read_text() == "new artifact\n"
        message = str(caught.value)
        assert "Staged report cleanup also failed (PermissionError, errno=13)" in message
        assert "retained .tmp file" in message
    finally:
        directory.chmod(0o700)
        for path in staged:
            path.unlink(missing_ok=True)


def test_cleanup_failure_diagnostic_doesnt_expose_cleanup_exception_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep cleanup diagnostics bounded while preserving the original write exception as cause."""
    failure = OSError(errno.ENOSPC, "original write failure")
    original_unlink = Path.unlink
    staged: list[Path] = []

    def fail_replace(source: str, target: Path) -> None:
        """Model an unavailable destination after the staged file has been written."""
        staged.append(Path(source))
        raise failure

    def fail_unlink(path: Path, *, missing_ok: bool = False) -> None:
        """Inject private cleanup exception content that mustn't reach the diagnostic."""
        if path in staged:
            raise PermissionError(
                errno.EACCES, "private-token-and-policy-content", "/private/source"
            )
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    try:
        with pytest.raises(PolicyNIMError) as caught:
            _write_cli_artifact_text(str(tmp_path / "report.json"), "new artifact")
        assert caught.value.__cause__ is failure
        assert "original write failure" in str(caught.value)
        assert "private-token-and-policy-content" not in str(caught.value)
        assert "/private/source" not in str(caught.value)
        assert "PermissionError, errno=13" in str(caught.value)
    finally:
        for path in staged:
            original_unlink(path, missing_ok=True)
