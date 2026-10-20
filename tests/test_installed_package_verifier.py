"""Regression checks for failures in the fresh-install evidence collector."""

from __future__ import annotations

import importlib.util
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_installed_package.py"
spec = importlib.util.spec_from_file_location("installed_package_verifier", SCRIPT)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def test_console_command_failure_reports_cause_without_credentials(
    monkeypatch, tmp_path: Path
) -> None:
    """An installed entry-point failure must retain actionable, sanitized diagnostics."""
    calls = []

    def failed_command(arguments, **kwargs):
        """Simulate an installer-generated command that fails before module execution."""
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments, 2, stdout="", stderr="ImportError missing_module secret-token"
        )

    monkeypatch.setattr(verifier, "installed_command", lambda: "/installed/bin/policynim")
    monkeypatch.setattr(verifier.subprocess, "run", failed_command)
    with pytest.raises(RuntimeError, match="ImportError missing_module") as error:
        verifier.run_cli(
            ["inventory", "--format", "json"], env={"POLICYNIM_TOKEN": "secret-token"}, cwd=tmp_path
        )
    assert "secret-token" not in str(error.value)
    assert "<redacted>" in str(error.value)
    assert calls[0][0] == "/installed/bin/policynim"


def test_missing_console_command_cannot_pass_verification(monkeypatch, tmp_path: Path) -> None:
    """Module imports can't substitute for a missing user-facing command."""
    monkeypatch.setattr(verifier.sysconfig, "get_path", lambda name: str(tmp_path))
    with pytest.raises(RuntimeError, match="console command is missing"):
        verifier.installed_command()


def test_altered_entry_point_metadata_fails_wheel_comparison(monkeypatch, tmp_path: Path) -> None:
    """A broken installed entry point can't hide behind matching package source bytes."""
    package = tmp_path / "policynim"
    package.mkdir()
    (package / "__init__.py").write_text("")
    metadata = tmp_path / "policynim-0.1.0.dist-info"
    metadata.mkdir()
    (metadata / "entry_points.txt").write_text("[console_scripts]\npolicynim = missing:main\n")
    wheel = tmp_path / "policynim.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("policynim/__init__.py", "")
        archive.writestr(
            "policynim-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\npolicynim = policynim.interfaces.cli:main\n",
        )
    monkeypatch.setattr(verifier, "find_source_checkout_root", lambda: None)
    monkeypatch.setattr(verifier.policynim, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(
        verifier.importlib.metadata,
        "distribution",
        lambda name: SimpleNamespace(locate_file=lambda path: tmp_path / path),
    )
    with pytest.raises(RuntimeError, match="metadata differs"):
        verifier.check_wheel(wheel)
