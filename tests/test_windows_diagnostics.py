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
