"""Portable diagnostics must handle native Windows path syntax on every host."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policynim.interfaces import cli


@pytest.mark.parametrize(
    "path",
    [
        r"C:\private\customer\index.sqlite3",
        "C:/private/customer/index.sqlite3",
        r"C:\private folder\customer\index.sqlite3",
        "C:/private folder/customer/index.sqlite3",
    ],
)
def test_public_support_bundle_redacts_windows_prefixes(path: str) -> None:
    """Redact private directories without leaving drive prefixes or embedded command paths."""
    public, markers = cli._redact_support_bundle_paths(
        {"path": path, "command": f"inspect --path '{path}'"}
    )
    encoded = json.dumps(public)
    assert "private" not in encoded
    assert "customer" not in encoded
    assert "C:" not in encoded
    assert "index.sqlite3" in encoded
    assert "<local-path>" in markers


def test_public_support_bundle_preserves_remote_urls() -> None:
    """Path redaction must leave public endpoint diagnostics usable."""
    url = "https://policy.example/mcp"
    public, _ = cli._redact_support_bundle_paths({"endpoint": url})
    assert public["endpoint"] == url


def test_unknown_runtime_path_under_home_redacts_private_subdirectories() -> None:
    """The generic home marker must not leave private project names in public output."""
    path = Path.home() / "private customer" / "runtime" / "index.sqlite3"
    public, _ = cli._redact_support_bundle_paths({"path": path.as_posix()})
    assert public["path"] == "<local-path>/index.sqlite3"


@pytest.mark.parametrize("separator", ["\\", "/"])
@pytest.mark.parametrize(
    "directory",
    [
        "Work (tenantname)",
        "O'Reilly tenantname",
        "Work, tenantname",
        "Work; tenantname",
        "Work`tenantname",
    ],
)
def test_support_bundle_cli_redacts_embedded_windows_name_punctuation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, separator: str, directory: str
) -> None:
    """Public diagnostics must redact real directory punctuation inside a path."""
    from test_cli import configure_standalone_cli_environment
    from typer.testing import CliRunner

    configure_standalone_cli_environment(monkeypatch, tmp_path)
    path = separator.join(["D:", directory, "customer", "index.sqlite3"])
    monkeypatch.setattr(
        cli,
        "_build_doctor_report",
        lambda: {
            "status": "action_required",
            "message": f"Couldn't read configured index {path}.",
        },
    )
    result = CliRunner().invoke(cli.app, ["support-bundle"])
    assert result.exit_code == 0, result.output
    assert "tenantname" not in result.stdout
    assert "customer" not in result.stdout
    assert "D:" not in result.stdout
    assert "index.sqlite3" in result.stdout
    assert "<local-path>" in result.stdout


def test_support_bundle_cli_redacts_known_prefix_with_mixed_windows_separators(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A normalized discovery match must also match during actual string redaction."""
    from test_cli import configure_standalone_cli_environment
    from typer.testing import CliRunner

    configure_standalone_cli_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "_build_doctor_report",
        lambda: {
            "status": "action_required",
            "path": "D:/tenantname/customer/index.sqlite3",
            "message": r"Couldn't read configured index D:\tenantname/customer\index.sqlite3.",
        },
    )
    result = CliRunner().invoke(cli.app, ["support-bundle"])
    assert result.exit_code == 0, result.output
    assert "tenantname" not in result.stdout
    assert "customer" not in result.stdout
    assert "D:" not in result.stdout
    assert "index.sqlite3" in result.stdout
