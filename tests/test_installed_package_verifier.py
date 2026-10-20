"""Regression checks for failures in the fresh-install evidence collector."""

from __future__ import annotations

import asyncio
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
        assert kwargs["encoding"] == "utf-8"
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


def test_dependency_lock_digest_cant_be_substituted(tmp_path: Path) -> None:
    """A report can't attach a claimed digest to different lockfile bytes."""
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\npackage = []\n")
    with pytest.raises(RuntimeError, match="digest differs"):
        verifier.check_dependencies(lock, "sha256:" + "0" * 64, tmp_path / "requirements.txt")


def test_unreviewed_installed_version_fails_lock_verification(monkeypatch, tmp_path: Path) -> None:
    """Fresh-install evidence rejects dependency drift even if the wheel version matches."""
    import hashlib

    lock = tmp_path / "uv.lock"
    lock.write_text('version = 1\n[[package]]\nname = "runtime-dep"\nversion = "1.0"\n')
    monkeypatch.setattr(
        verifier.importlib.metadata,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "runtime_dep"}, version="2.0")],
    )
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("runtime-dep==1.0\n")
    digest = "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="runtime-dep==2.0"):
        verifier.check_dependencies(lock, digest, requirements)


@pytest.mark.parametrize("selected", [False, True])
def test_bootstrap_pip_uses_runtime_selection(monkeypatch, tmp_path: Path, selected: bool) -> None:
    """Optional lock entries can't constrain an unselected bundled installer."""
    import hashlib

    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\n[[package]]\nname = "pip"\nversion = "26.0.1"\n'
        '[[package]]\nname = "policynim"\nversion = "0.1.0"\n'
    )
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pip==26.0.1\n" if selected else "# base runtime\n")
    monkeypatch.setattr(
        verifier.importlib.metadata,
        "distributions",
        lambda: [
            SimpleNamespace(metadata={"Name": "pip"}, version="24.0"),
            SimpleNamespace(metadata={"Name": "policynim"}, version="0.1.0"),
        ],
    )
    digest = "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest()
    if selected:
        with pytest.raises(RuntimeError, match="pip==24.0"):
            verifier.check_dependencies(lock, digest, requirements)
    else:
        report = verifier.check_dependencies(lock, digest, requirements)
        assert report["bootstrap_versions"] == {"pip": "24.0"}


def test_installed_cli_reads_utf8_output_independently_of_locale(
    monkeypatch, tmp_path: Path
) -> None:
    """The installed child uses UTF-8 even when the verifier's locale differs."""

    def unicode_command(arguments, **kwargs):
        """Model the UTF-8 output that Windows-1252 couldn't decode in CI."""
        assert kwargs["encoding"] == "utf-8"
        return subprocess.CompletedProcess(
            arguments, 0, stdout="Usage: policynim " + chr(0x2192) + " policy", stderr=""
        )

    monkeypatch.setattr(verifier, "installed_command", lambda: "/installed/bin/policynim")
    monkeypatch.setattr(verifier.subprocess, "run", unicode_command)
    assert chr(0x2192) in verifier.run_command(["--help"], env={"PYTHONUTF8": "1"}, cwd=tmp_path)


def test_exported_requirement_cant_select_another_locked_version(
    monkeypatch, tmp_path: Path
) -> None:
    """A multi-version lock doesn't authorize ignoring the exported environment pin."""
    import hashlib

    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\n[[package]]\nname = "runtime-dep"\nversion = "1.0"\n'
        '[[package]]\nname = "runtime-dep"\nversion = "2.0"\n'
    )
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("runtime-dep==2.0\n")
    monkeypatch.setattr(
        verifier.importlib.metadata,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "runtime_dep"}, version="1.0")],
    )
    digest = "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="differs from exported requirements"):
        verifier.check_dependencies(lock, digest, requirements)


@pytest.mark.parametrize(
    "message",
    [
        "NVIDIA_API_KEY is missing.",
        "Generation is unavailable.",
        "top_k must be between 1 and 50.",
        "top_k\n Input should be greater than or equal to 1 "
        "[type=greater_than_equal, input_value=0]",
    ],
)
def test_client_requires_argument_validation_instead_of_any_tool_error(message: str) -> None:
    """A provider or index error can't stand in for input-validation evidence."""

    async def list_tools():
        """Expose the expected schemas independently of the rejected tool request."""
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name=name, input_schema={"type": "object"}, output_schema={"type": "object"}
                )
                for name in [
                    "policy_search",
                    "policy_preflight",
                    "policy_compile",
                    "policy_preflight_evidence",
                ]
            ]
        )

    async def call_tool(name, arguments):
        """Return an unrelated or correctly classified SDK error envelope."""
        return SimpleNamespace(is_error=True, content=[SimpleNamespace(text=message)])

    client = SimpleNamespace(
        protocol_version="2026-07-28", list_tools=list_tools, call_tool=call_tool
    )
    if message.startswith("top_k"):
        assert asyncio.run(verifier.check_client(client, "auto"))["invalid_argument_rejected"]
    else:
        with pytest.raises(RuntimeError, match="unrelated failure"):
            asyncio.run(verifier.check_client(client, "auto"))
