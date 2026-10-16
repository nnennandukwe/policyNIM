"""Public offline generation commands over real SQLite and immutable objects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_policy_generations import NO_ENV, clean_environment, seed_legacy  # noqa: F401
from typer.testing import CliRunner

from policynim.interfaces import generation_cli
from policynim.interfaces.cli import app
from policynim.services.generations import GenerationStore
from policynim.settings import Settings


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


def test_text_catalog_views_identify_generation_and_explicit_source_scope(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Text show/validate expose the same authoritative scope and ID as JSON."""
    from policynim.interfaces import cli
    from policynim.services.catalog import build_policy_catalog

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    active = (
        GenerationStore(root=settings.generations_dir, embedding=identity)
        .migrate(settings, index_path=index, rules_path=rules)
        .generation_id
    )
    monkeypatch.setattr(
        cli,
        "build_policy_catalog",
        lambda source_only=False: build_policy_catalog(settings=settings, source_only=source_only),
    )
    runner = CliRunner()
    for args in (["show", "A"], ["validate"]):
        result = runner.invoke(app, ["policies", *args])
        assert result.exit_code == 0, result.output
        assert active in result.stdout and "generation" in result.stdout.lower()
        source = runner.invoke(app, ["policies", *args, "--source"])
        assert source.exit_code == 0, source.output
        assert active not in source.stdout and "source" in source.stdout.lower()


def test_operator_inspection_revalidates_bytes_after_a_warm_runtime_reader(
    monkeypatch, tmp_path: Path
) -> None:
    """The operator command must check artifact bytes even when runtime validation is cached."""
    import policynim.services.generations as generation_module

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    with store.pin():
        pass
    read_regular = generation_module._read_regular
    reads: list[str] = []

    def record(path: Path, **kwargs):
        """Observe full index hashing across actual CLI invocations."""
        if path.name == "index.sqlite3":
            reads.append(path.name)
        return read_regular(path, **kwargs)

    monkeypatch.setattr(generation_cli, "get_settings", lambda: settings)
    monkeypatch.setattr(generation_module, "_read_regular", record)
    for expected_reads in (1, 2):
        result = CliRunner().invoke(app, ["generation", "inspect", "--format", "json"])
        assert result.exit_code == 0, result.output
        assert len(reads) == expected_reads
    assert not list((store.root / "leases").rglob("*.lock"))


@pytest.mark.parametrize("command", ["inspect", "export"])
def test_explicit_blank_id_never_falls_back_to_active(
    monkeypatch, tmp_path: Path, command: str
) -> None:
    """An empty shell variable must fail selection rather than inspecting/exporting active state."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    monkeypatch.setattr(generation_cli, "get_settings", lambda: settings)
    destination = tmp_path / "blank-id.zip"
    args = ["generation", command]
    if command == "export":
        args.append(str(destination))
    result = CliRunner().invoke(app, [*args, "--id", "", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["status"] == "error"
    assert not destination.exists()


def test_source_recovery_hint_checks_edited_markdown_after_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Following the actual recovery command must inspect broken edits despite a valid active ID."""
    import re
    import shlex

    from policynim.interfaces import cli
    from policynim.services.catalog import build_policy_catalog
    from policynim.services.inventory import inspect_source_inventory

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    active = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    assert settings.corpus_dir is not None
    broken = settings.corpus_dir / "A.md"
    broken.write_bytes(b"---\npolicy_id: [\n---\nbroken source edit\n")
    before = broken.read_bytes(), (store.root / "active.json").read_bytes()
    monkeypatch.setattr(
        cli,
        "build_policy_catalog",
        lambda source_only=False: build_policy_catalog(settings=settings, source_only=source_only),
    )
    runner = CliRunner()
    existing = runner.invoke(app, ["policies", "validate", "--format", "json"])
    assert existing.exit_code == 0
    assert json.loads(existing.stdout)["generation_id"] == active
    inventory = inspect_source_inventory(settings)
    assert inventory.status == "unavailable" and inventory.reason is not None
    match = re.search(r"Run `([^`]+)`", inventory.reason)
    assert match is not None
    advised = runner.invoke(app, shlex.split(match[1])[1:])
    assert advised.exit_code == 1, advised.output
    assert json.loads(advised.stdout)["status"] == "error"
    assert "A.md" in advised.output
    assert before == (broken.read_bytes(), (store.root / "active.json").read_bytes())
