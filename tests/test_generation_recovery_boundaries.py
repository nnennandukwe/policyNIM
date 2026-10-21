"""Public recovery diagnostics preserve selected scope and generation ownership."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_policy_generations import clean_environment, seed_legacy  # noqa: F401
from typer.testing import CliRunner

from policynim.errors import GenerationError
from policynim.interfaces import cli
from policynim.interfaces import mcp as mcp_module
from policynim.services.catalog import build_policy_catalog
from policynim.services.generations import GenerationStore


@pytest.mark.parametrize("source", [False, True])
@pytest.mark.parametrize("output_format", ["json", "text"])
def test_missing_policy_error_keeps_selected_catalog_scope(
    monkeypatch, tmp_path: Path, source: bool, output_format: str
) -> None:
    """An unknown ID must identify the actual sealed or mutable catalog it searched."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    published = GenerationStore(root=settings.generations_dir, embedding=identity).migrate(
        settings, index_path=index, rules_path=rules
    )
    monkeypatch.setattr(
        cli,
        "build_policy_catalog",
        lambda source_only=False: build_policy_catalog(settings=settings, source_only=source_only),
    )
    args = ["policies", "show", "ABSENT", "--format", output_format]
    if source:
        args.append("--source")
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 1, result.output
    scope = "source" if source else "generation"
    message = result.output
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["scope"] == scope
        assert payload["generation_id"] == (None if source else published.generation_id)
        message = payload["message"]
    assert f"{scope} catalog" in message
    assert ("policies list --source" if source else "policies list`") in message


@pytest.mark.parametrize("fault", ["publication_lock", "lease_directory", "lease_marker"])
def test_inventory_reports_reader_permission_failure_and_allows_retry(
    monkeypatch, tmp_path: Path, fault: str
) -> None:
    """Real admission I/O failures become structured diagnostics without changing active data."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    published = store.migrate(settings, index_path=index, rules_path=rules)
    root = settings.generations_dir
    pointer = (root / "active.json").read_bytes()
    monkeypatch.setattr("policynim.services.inventory.get_settings", lambda: settings)
    original_open, original_mkdir = os.open, Path.mkdir

    def deny_open(path, *args, **kwargs):
        """Inject an OS denial at the selected real descriptor boundary."""
        selected = Path(path)
        if (fault == "publication_lock" and selected == root / ".publish.lock") or (
            fault == "lease_marker" and selected.parent == root / "leases" / published.generation_id
        ):
            raise PermissionError("private-permission-sentinel")
        return original_open(path, *args, **kwargs)

    def deny_mkdir(path, *args, **kwargs):
        """Inject an OS denial before the selected reader directory can be created."""
        if fault == "lease_directory" and path == root / "leases" / published.generation_id:
            raise PermissionError("private-permission-sentinel")
        return original_mkdir(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", deny_open)
        patch.setattr(Path, "mkdir", deny_mkdir)
        result = CliRunner().invoke(cli.app, ["inventory", "--format", "json"])
        assert result.exit_code == 0, (result.output, result.exception)
        payload = json.loads(result.stdout)
        assert payload["generation_state"] == "invalid"
        assert "permission" in payload["generation_reason"].lower()
        assert "private-permission-sentinel" not in result.output
        for reader in (lambda: store.pin(), lambda: store.pin_retained(published.generation_id)):
            with pytest.raises(GenerationError):
                reader()
    assert (root / "active.json").read_bytes() == pointer
    retry = CliRunner().invoke(cli.app, ["inventory", "--format", "json"])
    assert retry.exit_code == 0, retry.output
    assert json.loads(retry.stdout)["generation_id"] == published.generation_id
    assert json.loads(retry.stdout)["generation_state"] == "ready"


@pytest.mark.parametrize(
    "tool,arguments",
    [("policy_search", {"query": "logging"}), ("policy_preflight", {"task": "logging"})],
)
def test_missing_generation_mcp_tool_keeps_safe_recovery_guidance(
    monkeypatch, tmp_path: Path, tool: str, arguments: dict[str, str]
) -> None:
    """Actual service construction must fail before providers and retain generation recovery."""
    settings, _, _, _ = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "missing-generations"})
    monkeypatch.setattr(mcp_module, "get_settings", lambda: settings)

    def forbid_provider(*args, **kwargs):
        """Missing local state must stop before any provider client is constructed."""
        pytest.fail("Missing generation constructed a provider.")

    monkeypatch.setattr("policynim.providers.nvidia.OpenAI", forbid_provider)
    with pytest.raises(ToolError) as caught:
        asyncio.run(mcp_module.mcp.call_tool(tool, arguments))
    message = str(caught.value)
    assert "generation" in message.lower()
    assert "policynim generation inspect" in message
    assert "verified bundle" in message
    assert "preserve" in message.lower()
    assert str(tmp_path) not in message
    assert not settings.generations_dir.exists()


@pytest.mark.parametrize(
    "command", [["search", "--query", "logging"], ["preflight", "--task", "logging"]]
)
def test_fresh_generation_cli_explains_ingest_before_inspection(
    monkeypatch, tmp_path: Path, command: list[str]
) -> None:
    """First retrieval must explain how to create state before asking to inspect it."""
    settings, _, _, _ = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "fresh-generations"})
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def forbid_provider(*args, **kwargs):
        """A missing local generation must fail before provider construction."""
        pytest.fail("Fresh generation constructed a provider.")

    monkeypatch.setattr("policynim.providers.nvidia.OpenAI", forbid_provider)
    result = CliRunner().invoke(cli.app, command)
    assert result.exit_code == 1, (result.output, result.exception)
    assert "policynim ingest" in result.output
    assert "policynim generation inspect" not in result.output
    assert "verified bundle" in result.output
    assert "migrate" in result.output
    assert not settings.generations_dir.exists()
