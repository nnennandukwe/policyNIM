"""Offline hosted startup, retained selection, and bounded failure contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from test_policy_generations import seed_legacy

from policynim.errors import GenerationError
from policynim.services.generations import GenerationStore
from policynim.settings import Settings

HostedSeed = tuple[Settings, GenerationStore, Path, str]
NO_ENV: dict[str, Any] = {"_env_file": None}


def test_production_example_loads_persistent_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped environment example keeps mutable state on the mounted volume."""
    for name in tuple(os.environ):
        if name.startswith(("POLICYNIM_", "NVIDIA_")) or name == "PORT":
            monkeypatch.delenv(name)
    options: dict[str, Any] = {
        "_env_file": Path(__file__).resolve().parents[1] / ".env.production.example"
    }
    settings = Settings(**options)
    assert settings.policynim_env == "production"
    assert settings.generations_dir == Path("/app/state/generations")
    assert settings.eval_workspace_dir == Path("/app/state/evals/workspace")
    assert settings.beta_auth_db_path == Path("/app/state/auth.sqlite3")
    assert settings.runtime_evidence_db_path == Path("/app/state/runtime_evidence.sqlite3")
    assert settings.hosted_state_mount == Path("/app/state")


def persistent_settings(settings: Settings) -> Settings:
    """Place every mutable hosted path below the fixture's declared volume root."""
    mount = settings.generations_dir.parent
    return settings.model_copy(
        update={
            "hosted_state_mount": mount,
            "beta_auth_db_path": mount / "auth.sqlite3",
            "eval_workspace_dir": mount / "evals/workspace",
            "runtime_evidence_db_path": mount / "runtime_evidence.sqlite3",
        }
    )


def test_missing_declared_mount_never_creates_state(hosted_seed: HostedSeed) -> None:
    """An absent declared volume can't be replaced by apparently durable image storage."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, _ = hosted_seed
    with pytest.raises(GenerationError, match="isn't mounted"):
        prepare_hosted_generation(persistent_settings(settings), seed_dir=seed)
    assert not target.root.exists()


@pytest.mark.parametrize(
    "field",
    ["generations_dir", "beta_auth_db_path", "eval_workspace_dir", "runtime_evidence_db_path"],
)
def test_declared_mount_rejects_a_state_path_outside_it(
    hosted_seed: HostedSeed, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
) -> None:
    """A mounted generation root alone can't make the other mutable stores durable."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, _ = hosted_seed
    settings.generations_dir.parent.mkdir()
    monkeypatch.setattr(Path, "is_mount", lambda path: True)
    outside = tmp_path / "outside"
    configured = persistent_settings(settings).model_copy(update={field: outside})
    with pytest.raises(GenerationError, match="outside the configured hosted state volume"):
        prepare_hosted_generation(configured, seed_dir=seed)
    assert not target.root.exists()
    assert not outside.exists()


def test_declared_mount_rejects_parent_directory_components(
    hosted_seed: HostedSeed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lexical mount parent can't admit state on its unmounted ancestor."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, _ = hosted_seed
    mount = settings.generations_dir.parent
    mount.mkdir()
    declared = mount / ".."
    monkeypatch.setattr(Path, "is_mount", lambda path: path == declared)
    configured = persistent_settings(settings).model_copy(update={"hosted_state_mount": declared})
    with pytest.raises(GenerationError, match="isn't mounted"):
        prepare_hosted_generation(configured, seed_dir=seed)
    assert not target.root.exists()


def test_declared_mount_admits_all_volume_backed_paths(
    hosted_seed: HostedSeed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid configured paths use normal generation publication after mount admission."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, _, seed, expected = hosted_seed
    settings.generations_dir.parent.mkdir()
    monkeypatch.setattr(Path, "is_mount", lambda path: True)
    assert prepare_hosted_generation(persistent_settings(settings), seed_dir=seed) == expected


def test_declared_mount_rejects_a_state_directory_symlink_escape(
    hosted_seed: HostedSeed, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lexical volume prefix can't hide an evaluation directory pointing elsewhere."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, _ = hosted_seed
    mount = settings.generations_dir.parent
    mount.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (mount / "evals").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks isn't permitted on this platform.")
    monkeypatch.setattr(Path, "is_mount", lambda path: True)
    with pytest.raises(GenerationError, match="outside the configured hosted state volume"):
        prepare_hosted_generation(persistent_settings(settings), seed_dir=seed)
    assert not target.root.exists()
    assert not list(outside.iterdir())


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


@pytest.mark.parametrize("seed_state", ["missing", "malformed"])
def test_valid_existing_volume_doesnt_need_image_seed(
    hosted_seed: HostedSeed, seed_state: str
) -> None:
    """A valid mounted generation remains usable when the unused image seed is damaged."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, expected = hosted_seed
    assert prepare_hosted_generation(settings, seed_dir=seed) == expected
    before = (target.root / "active.json").read_bytes()
    checksum = seed / "generation.sha256"
    if seed_state == "missing":
        checksum.unlink()
    else:
        checksum.write_text("invalid", encoding="ascii")
    assert prepare_hosted_generation(settings, seed_dir=seed) == expected
    assert (target.root / "active.json").read_bytes() == before


@pytest.mark.parametrize("matches_seed", [True, False])
def test_fresh_explicit_selection_is_checked_before_activation(
    hosted_seed: HostedSeed, matches_seed: bool
) -> None:
    """A configured retained ID can't leave a different seed activated after failure."""
    from policynim.interfaces.hosted import prepare_hosted_generation

    settings, target, seed, image_id = hosted_seed
    settings = settings.model_copy(update={"generation_id": image_id if matches_seed else "0" * 64})
    if matches_seed:
        assert prepare_hosted_generation(settings, seed_dir=seed) == image_id
    else:
        with pytest.raises(GenerationError):
            prepare_hosted_generation(settings, seed_dir=seed)
        assert not target.root.exists()


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
    assert json.loads(error)["phase"] == "settings"
    assert "environment permissions" in error
    assert json.loads(error)["status"] == "error"


def test_invalid_settings_stop_before_storage_with_safe_field_names(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Actual validation failures identify configuration fields without input values."""
    from policynim.interfaces import hosted

    def invalid_settings() -> Settings:
        """Load a private invalid timeout through the actual settings validator."""
        options: dict[str, Any] = {
            "nvidia_timeout_seconds": "private-invalid-sentinel",
            "_env_file": None,
        }
        return Settings(**options)

    monkeypatch.setattr(hosted, "get_settings", invalid_settings)
    monkeypatch.setattr(
        hosted, "prepare_hosted_generation", lambda *args: pytest.fail("Storage mustn't start")
    )
    monkeypatch.setattr(hosted.os, "execvp", lambda *args: pytest.fail("MCP mustn't start"))
    assert hosted.main() == 1
    error = json.loads(capsys.readouterr().err)
    assert error["phase"] == "settings"
    assert error["invalid_settings"] == ["nvidia_timeout_seconds"]
    assert "private-invalid-sentinel" not in json.dumps(error)
    assert "generation inspect" not in error["message"]


@pytest.mark.parametrize(
    "state,phase,needle",
    [
        ("checksum", "bootstrap", "image seed checksum isn't valid"),
        ("unowned", "open", "isn't an owned generation store"),
        ("pointer", "inspect", "activation pointer isn't valid"),
    ],
)
def test_entrypoint_preserves_safe_generation_diagnosis(
    hosted_seed: HostedSeed,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: str,
    phase: str,
    needle: str,
) -> None:
    """Actual checksum, ownership, and pointer failures keep their safe phase and detail."""
    from policynim.interfaces import hosted

    settings, target, seed, _ = hosted_seed
    prepare = hosted.prepare_hosted_generation
    if state == "checksum":
        (seed / "generation.sha256").write_text("broken", encoding="ascii")
    elif state == "unowned":
        target.root.mkdir(parents=True)
        (target.root / "private-notes.txt").write_text("private-note-sentinel")
    else:
        prepare(settings, seed_dir=seed)
        (target.root / "active.json").write_text("broken", encoding="ascii")
    monkeypatch.setattr(hosted, "get_settings", lambda: settings)
    monkeypatch.setattr(
        hosted, "prepare_hosted_generation", lambda current: prepare(current, seed_dir=seed)
    )
    monkeypatch.setattr(hosted.os, "execvp", lambda *args: pytest.fail("MCP mustn't start"))
    assert hosted.main() == 1
    error = json.loads(capsys.readouterr().err)
    assert error["generation_phase"] == phase and needle in error["message"]
    assert "private-note-sentinel" not in error["message"]
    assert str(target.root) not in error["message"]


def test_cached_settings_validation_names_fields_without_exposing_inputs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise the actual cached loader and its wrapped validation failure."""
    from policynim.interfaces import hosted

    hosted.get_settings.cache_clear()
    monkeypatch.setenv("POLICYNIM_NVIDIA_TIMEOUT_SECONDS", "private-invalid-sentinel")
    monkeypatch.setattr(
        hosted, "prepare_hosted_generation", lambda *args: pytest.fail("Storage mustn't start")
    )
    try:
        assert hosted.main() == 1
    finally:
        hosted.get_settings.cache_clear()
    error = json.loads(capsys.readouterr().err)
    assert error["phase"] == "settings"
    assert error["invalid_settings"] == ["nvidia_timeout_seconds"]
    assert "private-invalid-sentinel" not in json.dumps(error)
