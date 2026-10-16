"""Public offline generation commands over real SQLite and immutable objects."""

from __future__ import annotations

import json
from pathlib import Path

from test_policy_generations import NO_ENV, clean_environment, seed_legacy  # noqa: F401
from typer.testing import CliRunner

from policynim.interfaces import generation_cli
from policynim.interfaces.cli import app
from policynim.settings import Settings
from policynim.storage.generations import GenerationStore


def test_cli_migrate_inspect_export_import_and_rollback(monkeypatch, tmp_path: Path) -> None:
    """Exercise public commands with exact IDs, checksums, stale tokens, and preserved originals."""
    from test_ingest_service import MockEmbedder

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    before = index.read_bytes(), rules.read_bytes()
    monkeypatch.setattr(generation_cli, "get_settings", lambda: settings)

    def forbid_provider(*args, **kwargs):
        """Offline lifecycle commands mustn't construct an HTTP client."""
        raise AssertionError("Provider construction")

    monkeypatch.setattr("policynim.providers.nvidia.OpenAI", forbid_provider)
    runner = CliRunner()

    def invoke(*args: str) -> dict:
        """Require success and decode the public structured payload."""
        result = runner.invoke(app, ["generation", *args, "--format", "json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    first = invoke("migrate")["generation_id"]
    inspected = invoke("inspect")
    assert inspected["generation_id"] == inspected["catalog"]["generation_id"] == first
    bundle = tmp_path / "export.zip"
    exported = invoke("export", str(bundle))
    assert exported["generation_id"] == first
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    source = settings.corpus_dir
    assert source is not None
    member = next(source.rglob("*.md"))
    member.write_text(member.read_text() + "\nReviewed additional step.\n")
    second = store.build(settings, embedder=MockEmbedder()).generation_id
    stale = runner.invoke(
        app, ["generation", "rollback", "--expect-active", first, "--format", "json"]
    )
    assert stale.exit_code == 1 and json.loads(stale.stdout)["status"] == "error"
    assert invoke("inspect")["generation_id"] == second
    assert invoke("rollback", "--expect-active", second)["generation_id"] == first
    assert invoke("activate", second, "--expect-active", first)["generation_id"] == second
    assert (
        invoke("import", str(bundle), "--expect-digest", exported["digest"])["generation_id"]
        == first
    )
    assert first in invoke("prune", "--keep-recent", "0")["kept"]
    assert (index.read_bytes(), rules.read_bytes()) == before


def test_cli_errors_are_nonzero_sanitized_and_dont_create_store(
    monkeypatch, tmp_path: Path
) -> None:
    """Reject invalid transport bytes and absent state without setup prompts or source writes."""
    settings = Settings(**NO_ENV, generations_dir=tmp_path / "generations")
    monkeypatch.setattr(generation_cli, "get_settings", lambda: settings)
    bundle = tmp_path / "private-canary.zip"
    bundle.write_bytes(b"PRIVATE-CANARY")
    runner = CliRunner()
    for args in (["inspect"], ["import", str(bundle), "--expect-digest", "sha256:" + "a" * 64]):
        result = runner.invoke(app, ["generation", *args, "--format", "json"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["status"] == "error"
        assert "PRIVATE-CANARY" not in result.output and "private-canary" not in result.output
    assert bundle.read_bytes() == b"PRIVATE-CANARY"
    assert not settings.generations_dir.exists()


def test_inspection_error_exposes_owned_pointer_for_corrupt_active_recovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Read the token from failed inspection and recover through the public rollback command."""
    from test_ingest_service import MockEmbedder

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    source = settings.corpus_dir
    assert source is not None
    member = next(source.rglob("*.md"))
    member.write_text(member.read_text() + "\nNext revision.\n")
    second = store.build(settings, embedder=MockEmbedder()).generation_id
    damaged = store.root / "objects" / second / "index.sqlite3"
    damaged.write_bytes(b"PRIVATE-CANARY damaged index")
    monkeypatch.setattr(generation_cli, "get_settings", lambda: settings)
    runner = CliRunner()
    inspected = runner.invoke(app, ["generation", "inspect", "--format", "json"])
    assert inspected.exit_code == 1 and "PRIVATE-CANARY" not in inspected.output
    activation = json.loads(inspected.stdout)["activation"]
    assert activation["active"] == second and activation["previous"] == first
    recovered = runner.invoke(
        app, ["generation", "rollback", "--expect-active", activation["active"], "--format", "json"]
    )
    assert recovered.exit_code == 0, recovered.output
    assert json.loads(recovered.stdout)["generation_id"] == first
    assert damaged.read_bytes() == b"PRIVATE-CANARY damaged index"
