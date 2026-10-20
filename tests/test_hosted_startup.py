"""Offline hosted startup, retained selection, and bounded failure contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_policy_generations import seed_legacy

from policynim.errors import GenerationError
from policynim.services.generations import GenerationStore
from policynim.settings import Settings

HostedSeed = tuple[Settings, GenerationStore, Path, str]
NO_ENV: dict[str, Any] = {"_env_file": None}


@pytest.fixture
def hosted_seed(tmp_path: Path) -> HostedSeed:
    """Export a real complete generation with offline vectors and packaged source proof."""
    settings, identity, index, rules = seed_legacy(tmp_path / "source")
    source = GenerationStore(root=tmp_path / "source-generations", embedding=identity)
    result = source.migrate(settings, index_path=index, rules_path=rules)
    seed = tmp_path / "seed"
    seed.mkdir()
    digest = source.export_bundle(seed / "generation.zip", result.generation_id)
    (seed / "generation.sha256").write_text(digest + "\n", encoding="ascii")
    settings = settings.model_copy(update={"generations_dir": tmp_path / "volume/generations"})
    target = GenerationStore(root=settings.generations_dir, embedding=identity)
    return settings, target, seed, result.generation_id


def test_first_start_imports_real_bundle_and_restart_preserves_pointer(
    hosted_seed: HostedSeed,
) -> None:
    """An absent volume gets one validated generation; restarting doesn't reactivate it."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, expected = hosted_seed
    assert prepare_hosted_generation(settings, seed_dir=seed) == expected
    before = (target.root / "active.json").read_bytes()
    assert prepare_hosted_generation(settings, seed_dir=seed) == expected
    assert (target.root / "active.json").read_bytes() == before
    with target.pin(force_validation=True) as snapshot:
        assert snapshot.generation_id == expected


def test_start_preserves_explicit_operator_generation(
    hosted_seed: HostedSeed, tmp_path: Path
) -> None:
    """A newer image seed can't overwrite an operator's retained valid selection."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, image_id = hosted_seed
    custom, _, index, rules = seed_legacy(tmp_path / "custom", ids=["CUSTOM"])
    selected = target.migrate(custom, index_path=index, rules_path=rules)
    before = (target.root / "active.json").read_bytes()
    assert prepare_hosted_generation(settings, seed_dir=seed) == selected.generation_id
    assert selected.generation_id != image_id
    assert (target.root / "active.json").read_bytes() == before


@pytest.mark.parametrize("state", ["empty", "unowned", "corrupt_pointer", "missing_object"])
def test_start_refuses_existing_incomplete_or_damaged_root(
    hosted_seed: HostedSeed, state: str
) -> None:
    """Startup preserves recovery evidence instead of treating a damaged root as new."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, _ = hosted_seed
    if state in {"empty", "unowned"}:
        target.root.mkdir(parents=True)
        if state == "unowned":
            (target.root / "notes.txt").write_text("operator-owned", encoding="ascii")
    else:
        target.import_bundle(
            seed / "generation.zip",
            expected_digest=(seed / "generation.sha256").read_text().strip(),
        )
        pointer = target.root / "active.json"
        pointer.write_text(
            "broken"
            if state == "corrupt_pointer"
            else json.dumps({"schema_version": 1, "active": "0" * 64, "previous": None}),
            encoding="ascii",
        )
    before = {
        path.relative_to(target.root): path.read_bytes()
        for path in target.root.rglob("*")
        if path.is_file()
    }
    with pytest.raises(GenerationError):
        prepare_hosted_generation(settings, seed_dir=seed)
    assert all((target.root / name).read_bytes() == value for name, value in before.items())


@pytest.mark.parametrize("checksum", ["", "private\n" * 200, "sha256:" + "0" * 64])
def test_bad_seed_checksum_never_initializes_target(hosted_seed: HostedSeed, checksum: str) -> None:
    """Untrusted seed metadata fails before target ownership or object writes."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, _ = hosted_seed
    (seed / "generation.sha256").write_text(checksum, encoding="ascii")
    with pytest.raises(GenerationError):
        prepare_hosted_generation(settings, seed_dir=seed)
    assert not target.root.exists()


def test_entrypoint_replaces_process_after_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hosted process inherits normal signal delivery through exec, without a shell."""
    from policynim.interfaces import hosted

    observed: list[object] = []
    monkeypatch.setattr(hosted, "get_settings", lambda: Settings(**NO_ENV))
    monkeypatch.setattr(hosted, "prepare_hosted_generation", lambda *args, **kwargs: "a" * 64)
    monkeypatch.setattr(hosted.os, "execvp", lambda command, argv: observed.append((command, argv)))
    assert hosted.main() == 0
    assert observed == [("policynim", ["policynim", "mcp", "--transport", "streamable-http"])]


def test_entrypoint_failure_is_nonzero_and_redacts_private_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed startup never starts MCP or echoes private exception payloads."""
    from policynim.interfaces import hosted

    def fail(*args: Any, **kwargs: Any) -> str:
        """Represent a path or provider secret inside an underlying exception."""
        raise OSError("private-secret-sentinel")

    monkeypatch.setattr(hosted, "get_settings", fail)
    monkeypatch.setattr(hosted.os, "execvp", lambda *args: pytest.fail("MCP mustn't start"))
    assert hosted.main() == 1
    error = capsys.readouterr().err
    assert "private-secret-sentinel" not in error
    assert "generation inspect" in error
    assert json.loads(error)["status"] == "error"
