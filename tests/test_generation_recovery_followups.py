"""Public regressions for generation recovery, retention and unavailable change stamps."""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path

import pytest
from test_ingest_service import MockEmbedder
from test_policy_generations import clean_environment, seed_legacy  # noqa: F401
from typer.testing import CliRunner

import policynim.services.generations as generation_module
from policynim.errors import GenerationError
from policynim.interfaces import cli, generation_cli
from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store


@pytest.mark.parametrize("operation", ["activate", "rollback"])
@pytest.mark.parametrize("root_state", ["missing", "unknown"])
def test_refused_recovery_cli_doesnt_initialize_or_modify_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str, root_state: str
) -> None:
    """A mistyped recovery destination mustn't become a managed store after refusal."""
    settings, _, _, _ = seed_legacy(tmp_path)
    root = tmp_path / "recovery-root"
    if root_state == "unknown":
        root.mkdir()
        (root / "operator-note").write_bytes(b"preserve unknown data")
    before = {p.name: p.read_bytes() for p in root.iterdir()} if root.exists() else None
    settings = settings.model_copy(update={"generations_dir": root})
    monkeypatch.setattr(generation_cli, "get_settings", lambda: settings)
    args = ["generation", operation]
    if operation == "activate":
        args.append("a" * 64)
    result = CliRunner().invoke(cli.app, [*args, "--expect-active", "b" * 64, "--format", "json"])
    assert result.exit_code == 1, result.output
    if before is None:
        assert not root.exists()
    else:
        assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_ingest_names_bad_utf8_notice_and_preserves_prior_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A notice decoding failure must identify its source before any provider work."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    root = tmp_path / "generations"
    settings = settings.model_copy(update={"generations_dir": root})
    store = GenerationStore(root=root, embedding=identity)
    published = store.migrate(settings, index_path=index, rules_path=rules)
    pointer = (root / "active.json").read_bytes()
    assert settings.corpus_dir is not None
    notice = settings.corpus_dir / "licenses" / "NOTICE.txt"
    notice.parent.mkdir()
    notice.write_bytes(b"Notice with invalid UTF-8: \xff")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def forbid_provider(*args, **kwargs):
        """Invalid captured sources must stop before a provider client is constructed."""
        pytest.fail("Invalid notice constructed a provider")

    monkeypatch.setattr("policynim.providers.nvidia.OpenAI", forbid_provider)
    result = CliRunner().invoke(cli.app, ["ingest"])
    assert result.exit_code == 1, (result.output, result.exception)
    assert "licenses/NOTICE.txt" in result.output
    assert "UTF-8" in result.output
    assert "codec can't decode" not in result.output
    assert (root / "active.json").read_bytes() == pointer
    assert notice.read_bytes().endswith(b"\xff")
    with store.pin() as retained:
        assert retained.generation_id == published.generation_id


@pytest.mark.parametrize("initially_available", [False, True])
def test_pinned_reader_survives_change_stamp_availability_transition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, initially_available: bool
) -> None:
    """Unavailable Windows metadata mustn't turn unchanged content into a permanent denial."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    available = [initially_available]

    def stamp(path: Path, observed: os.stat_result) -> int | None:
        """Model a transient metadata-query failure without changing artifact bytes."""
        return observed.st_ctime_ns if available[0] else None

    monkeypatch.setattr(generation_module, "_file_change_stamp", stamp)
    with closing(create_index_store(settings)) as reader:
        available[0] = not initially_available
        assert reader.list_chunks()
        available[0] = initially_available
        assert reader.list_chunks()


def test_unknown_change_stamps_still_detect_same_size_preserved_mtime_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing metadata stamp requires content validation instead of weaker identity checks."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    published = store.migrate(settings, index_path=index, rules_path=rules)
    monkeypatch.setattr(generation_module, "_file_change_stamp", lambda path, observed: None)
    with closing(create_index_store(settings)) as reader:
        catalog = store.root / "objects" / published.generation_id / "catalog.json"
        observed = catalog.stat()
        data = catalog.read_bytes()
        catalog.write_bytes(b"[" + data[1:])
        os.utime(catalog, ns=(observed.st_atime_ns, observed.st_mtime_ns))
        with pytest.raises(GenerationError):
            reader.list_chunks()


@pytest.mark.parametrize("boundary", ["reinspection", "rename"])
def test_retention_preserves_changed_candidate_and_continues_other_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: str
) -> None:
    """A changed object mustn't prevent unrelated candidates, trash or staging recovery."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    ids = [store.migrate(settings, index_path=index, rules_path=rules).generation_id]
    assert settings.corpus_dir is not None
    policy = settings.corpus_dir / "A.md"
    for suffix in ("second", "third", "fourth"):
        policy.write_text(policy.read_text() + f"\n{suffix}\n")
        ids.append(store.build(settings, MockEmbedder()).generation_id)
    target = store.root / "objects" / ids[0]
    stage = store.root / "staging" / "build-abcdefgh"
    trash = store.root / "trash" / (ids[-1] + "-" + "a" * 32)
    stage.mkdir()
    trash.mkdir()
    pointer = (store.root / "active.json").read_bytes()
    counts: dict[str, int] = {}
    current: list[Path] = []
    original_inspect = GenerationStore._inspect

    def damage() -> None:
        """Change one sealed artifact at the selected real retention boundary."""
        catalog = target / "catalog.json"
        catalog.write_bytes(catalog.read_bytes() + b" ")

    def inspect(self, directory: Path, **kwargs):
        """Track the second candidate inspection without replacing its real validation."""
        if directory.parent == store.root / "objects":
            counts[directory.name] = counts.get(directory.name, 0) + 1
            if counts[directory.name] == 2:
                current[:] = [directory]
                if directory == target and boundary == "reinspection":
                    damage()
        return original_inspect(self, directory, **kwargs)

    def fault(step: str) -> None:
        """Change the selected candidate immediately before its real rename guard."""
        if step == "prune_rename" and boundary == "rename" and current == [target]:
            damage()

    monkeypatch.setattr(GenerationStore, "_inspect", inspect)
    observed = GenerationStore(root=store.root, embedding=identity, fault=fault).prune(
        keep_recent=0
    )
    assert target.is_dir()
    assert (target / "catalog.json").read_bytes().endswith(b" ")
    assert ids[1] in observed.removed
    assert any(ids[0] in warning for warning in observed.warnings)
    assert not stage.exists()
    assert not trash.exists()
    assert (store.root / "active.json").read_bytes() == pointer
