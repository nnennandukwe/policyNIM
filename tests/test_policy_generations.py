"""Offline lifecycle proofs for complete immutable policy generations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_ingest_service import MockEmbedder
from test_policy_profiles import write_pack

from policynim.services.ingest import IngestService
from policynim.settings import Settings, get_settings
from policynim.storage.generations import GenerationStore
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import EmbeddingIdentity

NO_ENV: dict[str, Any] = {"_env_file": None}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch):
    """Keep personal settings and credentials outside generation fixtures."""
    import os

    for key in list(os.environ):
        if key.startswith("POLICYNIM_") or key == "NVIDIA_API_KEY":
            monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_migration_seals_matching_sources_index_and_rules(tmp_path: Path) -> None:
    """Migration validates actual SQLite/source/rules and preserves the standalone originals."""
    source = write_pack(tmp_path / "policies", "test.public", ["A", "B"])
    identity = EmbeddingIdentity(model="test-embed", endpoint="https://example.invalid/v1")
    index = tmp_path / "legacy.sqlite3"
    rules = tmp_path / "legacy-rules.json"
    settings = Settings(
        **NO_ENV,
        corpus_dir=source,
        nvidia_embed_model=identity.model,
        nvidia_base_url=identity.endpoint,
        index_db_path=index,
        runtime_rules_artifact_path=rules,
    )
    legacy = SQLiteVecIndexStore(path=index, embedding_identity=identity)
    with IngestService(
        embedder=MockEmbedder(),
        index_store=legacy,
        corpus_root=source,
        embedding_model=identity.model,
        runtime_rules_artifact_path=rules,
    ) as service:
        service.run()
    before_index, before_rules = index.read_bytes(), rules.read_bytes()
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    published = generations.migrate(settings, index_path=index, rules_path=rules)
    assert not published.warnings
    with generations.pin() as snapshot:
        assert snapshot.generation_id == published.generation_id
        assert snapshot.catalog.effective_count == 2
        assert snapshot.manifest.embedding == identity
        assert snapshot.manifest.dimension == 2
        assert (
            SQLiteVecIndexStore(path=snapshot.index_path, embedding_identity=identity).list_chunks()
            == legacy.list_chunks()
        )
        assert snapshot.rules_path.read_bytes() == before_rules
    assert index.read_bytes() == before_index
    assert rules.read_bytes() == before_rules
    pointer = json.loads((generations.root / "active.json").read_text())
    assert pointer["active"] == published.generation_id


def seed_legacy(tmp_path: Path, ids: list[str] | None = None):
    """Build a real standalone pair with deterministic offline vectors."""
    source = write_pack(tmp_path / "policies", "test.public", ids or ["A", "B"])
    identity = EmbeddingIdentity(model="test-embed", endpoint="https://example.invalid/v1")
    index, rules = tmp_path / "legacy.sqlite3", tmp_path / "legacy-rules.json"
    settings = Settings(
        **NO_ENV,
        corpus_dir=source,
        nvidia_embed_model=identity.model,
        nvidia_base_url=identity.endpoint,
        index_db_path=index,
        runtime_rules_artifact_path=rules,
    )
    with IngestService(
        embedder=MockEmbedder(),
        index_store=SQLiteVecIndexStore(path=index, embedding_identity=identity),
        corpus_root=source,
        embedding_model=identity.model,
        runtime_rules_artifact_path=rules,
    ) as service:
        service.run()
    return settings, identity, index, rules


def test_profile_snapshot_survives_source_removal(tmp_path: Path) -> None:
    """The sealed effective catalog stays valid after all original packs/profile are unavailable."""
    import shutil

    from test_policy_profiles import compose

    from policynim.services.profiles import initialize_profile, resolve_profile

    baseline = write_pack(tmp_path / "baseline", "public.baseline", ["A", "B", "C"])
    custom = write_pack(tmp_path / "custom", "team.custom", ["D", "E"])
    profile = tmp_path / "profile.toml"
    initialize_profile(profile, [baseline, custom], profile_id="sealed")
    compose(profile)
    expected = resolve_profile(profile).catalog
    settings = Settings(**NO_ENV, profile=profile)
    identity = EmbeddingIdentity(
        model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
    )
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    result = generations.build(settings, MockEmbedder())
    shutil.rmtree(baseline)
    shutil.rmtree(custom)
    profile.unlink()
    with generations.pin() as snapshot:
        assert snapshot.generation_id == result.generation_id
        assert snapshot.catalog == expected
        assert {document.metadata.policy_id for document in snapshot.documents} == {"C", "D", "E"}
        assert {
            entry.policy.policy_id for entry in snapshot.catalog.policies if entry.state != "active"
        } == {"A", "B"}


def test_tampered_rules_with_updated_hash_still_fail_source_validation(tmp_path: Path) -> None:
    """Rehashing a mismatched rules file can't make it agree with the source snapshot."""
    import hashlib

    from policynim.storage.generation_sources import digest_bytes
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    result = generations.migrate(settings, index_path=index, rules_path=rules)
    directory = generations.root / "objects" / result.generation_id
    (directory / "runtime_rules.json").write_text('{"schema_version":1,"rules":[]}')
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["runtime_rules.json"] = digest_bytes(
        (directory / "runtime_rules.json").read_bytes()
    )
    manifest_path.write_text(json.dumps(manifest))
    changed_id = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    directory.rename(directory.with_name(changed_id))
    (generations.root / "active.json").write_text(
        json.dumps({"schema_version": 1, "active": changed_id})
    )
    with pytest.raises(GenerationError, match="couldn't be validated"):
        generations.pin()


def test_same_dimension_other_model_is_rejected(tmp_path: Path) -> None:
    """Configured model identity remains mandatory even when vector dimensions match."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    generations.migrate(settings, index_path=index, rules_path=rules)
    wrong = GenerationStore(
        root=generations.root,
        embedding=identity.model_copy(update={"model": "different-model-with-two-dimensions"}),
    )
    with pytest.raises(GenerationError):
        wrong.pin()


@pytest.mark.parametrize("invalid_rules", ["", "not JSON", '{"schema_version":1,"rules":[]}'])
def test_failed_migration_preserves_active_and_originals(
    tmp_path: Path, invalid_rules: str
) -> None:
    """An unrelated standalone rules artifact can't partially activate a new generation."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = generations.migrate(settings, index_path=index, rules_path=rules)
    pointer = (generations.root / "active.json").read_bytes()
    bad_rules = tmp_path / "unrelated.json"
    bad_rules.write_text(invalid_rules)
    index_before, rules_before = index.read_bytes(), bad_rules.read_bytes()
    with pytest.raises(GenerationError):
        generations.migrate(settings, index_path=index, rules_path=bad_rules)
    assert (generations.root / "active.json").read_bytes() == pointer
    assert generations.inspect().generation_id == first.generation_id
    assert index.read_bytes() == index_before and bad_rules.read_bytes() == rules_before
    assert not list((generations.root / "staging").iterdir())


def test_precommit_fault_preserves_active_generation(tmp_path: Path) -> None:
    """A failed pointer switch leaves the entire earlier generation active."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = generations.migrate(settings, index_path=index, rules_path=rules)
    pointer = (generations.root / "active.json").read_bytes()
    assert settings.corpus_dir is not None
    (settings.corpus_dir / "A.md").write_text(
        (settings.corpus_dir / "A.md").read_text() + "\nNew rule.\n"
    )

    def fail(name: str) -> None:
        if name == "before_activate":
            raise OSError("Injected activation failure.")

    failing = GenerationStore(root=generations.root, embedding=identity, fault=fail)
    with pytest.raises(GenerationError):
        failing.build(settings, MockEmbedder())
    assert (generations.root / "active.json").read_bytes() == pointer
    assert generations.inspect().generation_id == first.generation_id


def test_postcommit_fault_reports_active_with_warning(tmp_path: Path) -> None:
    """A directory-sync failure can't report an aborted activation after pointer replacement."""
    settings, identity, index, rules = seed_legacy(tmp_path)

    def fail(name: str) -> None:
        if name == "active_directory_fsync":
            raise OSError("Injected postcommit sync failure.")

    generations = GenerationStore(root=tmp_path / "generations", embedding=identity, fault=fail)
    result = generations.migrate(settings, index_path=index, rules_path=rules)
    assert result.warnings
    assert generations.inspect().generation_id == result.generation_id


def test_leased_reader_survives_publication_prune_and_rollback(tmp_path: Path) -> None:
    """A reader's complete generation survives every activation until its lease closes."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = generations.migrate(settings, index_path=index, rules_path=rules)
    reader = generations.pin()
    initial_chunks = SQLiteVecIndexStore(
        path=reader.index_path, embedding_identity=identity
    ).list_chunks()
    assert settings.corpus_dir is not None
    policy = settings.corpus_dir / "A.md"
    receipts = []
    for number in range(5):
        policy.write_text(policy.read_text() + f"\nReviewed release {number}.\n")
        receipts.append(generations.build(settings, MockEmbedder()))
    generations.prune(keep_recent=0)
    assert reader.index_path.exists()
    assert (
        SQLiteVecIndexStore(path=reader.index_path, embedding_identity=identity).list_chunks()
        == initial_chunks
    )
    assert generations.inspect().generation_id == receipts[-1].generation_id
    rollback = generations.rollback(expected_active=receipts[-1].generation_id)
    assert rollback.generation_id == receipts[-2].generation_id
    assert rollback.previous_generation_id == receipts[-1].generation_id
    assert reader.generation_id == first.generation_id
    reader.close()
    reader.close()
    result = generations.prune(keep_recent=0)
    assert first.generation_id in result.removed
    assert not reader.directory.exists()
    assert generations.inspect().generation_id == rollback.generation_id


def test_stale_rollback_token_preserves_newer_publication(tmp_path: Path) -> None:
    """Rollback can't overwrite an activation that appeared after the operator inspected it."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = generations.migrate(settings, index_path=index, rules_path=rules)
    second = generations.build(settings, MockEmbedder())
    pointer = (generations.root / "active.json").read_bytes()
    with pytest.raises(GenerationError, match="Active generation changed"):
        generations.rollback(expected_active=first.generation_id)
    assert (generations.root / "active.json").read_bytes() == pointer
    assert generations.inspect().generation_id == second.generation_id


def test_concurrent_publishers_serialize_activation_state(tmp_path: Path) -> None:
    """A second publisher waits for the first and retains its completed activation as previous."""
    import threading

    settings, identity, index, rules = seed_legacy(tmp_path)
    root = tmp_path / "generations"
    generations = GenerationStore(root=root, embedding=identity)
    generations.migrate(settings, index_path=index, rules_path=rules)
    first_entered, release_first, second_attempted, second_entered = (
        threading.Event() for _ in range(4)
    )
    receipts: dict[str, Any] = {}
    errors: list[BaseException] = []

    def first_step(name: str) -> None:
        if name == "before_activate":
            first_entered.set()
            assert release_first.wait(5)

    def second_step(name: str) -> None:
        if name == "stage_mkdir":
            second_entered.set()

    def publish(label: str, store: GenerationStore) -> None:
        try:
            if label == "second":
                second_attempted.set()
            receipts[label] = store.build(settings, MockEmbedder())
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(
        target=publish,
        args=("first", GenerationStore(root=root, embedding=identity, fault=first_step)),
    )
    second = threading.Thread(
        target=publish,
        args=("second", GenerationStore(root=root, embedding=identity, fault=second_step)),
    )
    first.start()
    assert first_entered.wait(5)
    second.start()
    assert second_attempted.wait(5)
    assert not second_entered.wait(0.1)
    release_first.set()
    first.join(10)
    second.join(10)
    assert not first.is_alive() and not second.is_alive() and not errors
    assert second_entered.is_set()
    assert receipts["second"].previous_generation_id == receipts["first"].generation_id
    assert generations.inspect().generation_id == receipts["second"].generation_id


def test_unexpected_retention_entry_is_preserved(tmp_path: Path) -> None:
    """Prune never deletes a neighbor whose generation ownership can't be validated."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    generations.migrate(settings, index_path=index, rules_path=rules)
    private = generations.root / "objects" / "manual-recovery"
    private.mkdir()
    (private / "notes.txt").write_text("RETAIN-MANUAL-NOTES")
    result = generations.prune(keep_recent=0)
    assert result.warnings
    assert (private / "notes.txt").read_text() == "RETAIN-MANUAL-NOTES"


def test_bundle_round_trip_is_offline_and_preserves_generation_id(tmp_path: Path) -> None:
    """A portable prebuilt bundle imports byte-for-byte without embedding credentials."""
    from policynim.storage.generation_sources import digest_bytes

    settings, identity, index, rules = seed_legacy(tmp_path)
    original = GenerationStore(root=tmp_path / "first-generations", embedding=identity)
    published = original.migrate(settings, index_path=index, rules_path=rules)
    bundle = tmp_path / "public-bundle.zip"
    original.export_bundle(bundle)
    repeated = tmp_path / "same-bundle.zip"
    original.export_bundle(repeated)
    assert repeated.read_bytes() == bundle.read_bytes()
    imported = GenerationStore(root=tmp_path / "second-generations", embedding=identity)
    result = imported.import_bundle(bundle, expected_digest=digest_bytes(bundle.read_bytes()))
    assert result.generation_id == published.generation_id
    assert imported.inspect().catalog == original.inspect().catalog


_PRECOMMIT_POINTS = [
    "root_mkdir",
    "stage_mkdir",
    "index_prepare",
    "index_open_sync",
    "index_fsync",
    "index_close_sync",
    "staging_validate",
    "inspect",
    "stage_directory_fsync",
    "object_rename",
    "objects_fsync",
    "staging_parent_fsync",
    "managed_directories_fsync",
    "before_activate",
] + [
    f"{artifact}_{operation}"
    for artifact in ("stage_owner", "snapshot", "catalog", "rules", "manifest", "pointer")
    for operation in ("create", "write", "flush", "fsync", "close")
]


@pytest.mark.parametrize("point", _PRECOMMIT_POINTS)
def test_each_precommit_failure_can_retry_without_mixed_state(tmp_path: Path, point: str) -> None:
    """Each effect boundary preserves a complete prior pointer and permits a fresh retry."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    root = tmp_path / "generations"
    store = GenerationStore(root=root, embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules)
    pointer = (root / "active.json").read_bytes()
    assert settings.corpus_dir is not None
    source = settings.corpus_dir / "A.md"
    source.write_text(source.read_text() + "\nA reviewed change.\n")
    hit = False

    def fail(name: str) -> None:
        nonlocal hit
        if name == point:
            hit = True
            raise OSError("Injected operation failure.")

    with pytest.raises(GenerationError):
        GenerationStore(root=root, embedding=identity, fault=fail).build(settings, MockEmbedder())
    assert hit
    assert (root / "active.json").read_bytes() == pointer
    assert store.inspect().generation_id == first.generation_id
    assert not list((root / "staging").iterdir())
    retried = store.build(settings, MockEmbedder())
    assert store.inspect().generation_id == retried.generation_id != first.generation_id


@pytest.mark.parametrize(
    "point",
    ["root_parent_fsync"]
    + [
        f"owner_{operation}" for operation in ("create", "write", "flush", "fsync", "close", "link")
    ],
)
def test_initialization_failure_is_retryable_without_partial_activation(
    tmp_path: Path, point: str
) -> None:
    """An interrupted owner-marker creation doesn't make a fresh store unrecoverable."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    root = tmp_path / "generations"
    hit = False

    def fail(name: str) -> None:
        nonlocal hit
        if name == point:
            hit = True
            raise OSError("Injected owner initialization failure.")

    with pytest.raises(GenerationError):
        GenerationStore(root=root, embedding=identity, fault=fail).migrate(
            settings, index_path=index, rules_path=rules
        )
    assert hit and not (root / "active.json").exists()
    store = GenerationStore(root=root, embedding=identity)
    result = store.migrate(settings, index_path=index, rules_path=rules)
    assert store.inspect().generation_id == result.generation_id


@pytest.mark.parametrize(
    "point",
    ["snapshot_write", "staging_validate", "object_rename", "before_activate", "after_activate"],
)
def test_process_termination_exposes_one_complete_generation(tmp_path: Path, point: str) -> None:
    """A real killed publisher leaves old/new complete state and releases its process lock."""
    import subprocess
    import sys

    settings, identity, index, rules = seed_legacy(tmp_path)
    root = tmp_path / "generations"
    store = GenerationStore(root=root, embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules)
    assert settings.corpus_dir is not None
    source = settings.corpus_dir / "A.md"
    source.write_text(source.read_text() + "\nA reviewed process-boundary change.\n")
    script = """
import json,os,sys
from pathlib import Path
sys.path.insert(0,"tests")
from test_ingest_service import MockEmbedder
from policynim.settings import Settings
from policynim.storage.generations import GenerationStore
from policynim.types import EmbeddingIdentity
payload=json.loads(sys.argv[1])
settings=Settings(_env_file=None,corpus_dir=Path(payload["source"]),
                  nvidia_embed_model=payload["model"],nvidia_base_url=payload["endpoint"])
def terminate(name):
 if name==payload["point"]: os._exit(73)
GenerationStore(root=Path(payload["root"]),embedding=EmbeddingIdentity(
 model=payload["model"],endpoint=payload["endpoint"]),fault=terminate).build(settings,MockEmbedder())
"""
    payload = json.dumps(
        {
            "source": str(settings.corpus_dir),
            "root": str(root),
            "model": identity.model,
            "endpoint": identity.endpoint,
            "point": point,
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, payload], capture_output=True, text=True, timeout=20
    )
    assert completed.returncode == 73, completed.stderr
    with store.pin() as snapshot:
        if point == "after_activate":
            assert snapshot.generation_id != first.generation_id
        else:
            assert snapshot.generation_id == first.generation_id
        assert (
            SQLiteVecIndexStore(path=snapshot.index_path, embedding_identity=identity).count() > 0
        )
    result = store.build(settings, MockEmbedder())
    assert store.inspect().generation_id == result.generation_id


@pytest.mark.parametrize(
    "fault",
    ["checksum", "traversal", "duplicate", "symlink", "missing", "schema", "dimension", "model"],
)
def test_bundle_rejection_preserves_active_state(tmp_path: Path, fault: str) -> None:
    """Checksums don't replace archive-boundary, schema, shape, or model validation."""
    import io
    import stat
    import warnings
    import zipfile

    from policynim.storage.generation_sources import digest_bytes
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules)
    original = tmp_path / "original.zip"
    store.export_bundle(original)
    with zipfile.ZipFile(original) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    if fault in {"schema", "dimension", "model"}:
        manifest = json.loads(contents["manifest.json"])
        if fault == "schema":
            manifest["schema_version"] = True
        elif fault == "dimension":
            manifest["dimension"] += 1
        else:
            manifest["embedding"]["model"] = "other-model-same-dimensions"
        contents["manifest.json"] = json.dumps(manifest).encode()
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in contents.items():
                if fault == "missing" and name == "catalog.json":
                    continue
                if fault == "traversal" and name == "catalog.json":
                    name = "../catalog.json"
                info = zipfile.ZipInfo(name)
                if fault == "symlink" and name == "index.sqlite3":
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, data)
            if fault == "duplicate":
                archive.writestr("catalog.json", contents["catalog.json"])
    malicious = tmp_path / "rejected.zip"
    malicious.write_bytes(buffer.getvalue())
    digest = "sha256:" + "0" * 64 if fault == "checksum" else digest_bytes(malicious.read_bytes())
    importer = store
    if fault == "model":
        importer = GenerationStore(
            root=store.root,
            embedding=identity.model_copy(update={"model": "other-model-same-dimensions"}),
        )
    pointer = (store.root / "active.json").read_bytes()
    with pytest.raises(GenerationError):
        importer.import_bundle(malicious, expected_digest=digest)
    assert (store.root / "active.json").read_bytes() == pointer
    assert store.inspect().generation_id == first.generation_id
    assert not (tmp_path / "catalog.json").exists()


def test_bundle_source_match_guard_runs_before_store_mutation(tmp_path: Path) -> None:
    """Application builds reject stale bundles before changing managed state."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    original = GenerationStore(root=tmp_path / "original", embedding=identity)
    original.migrate(settings, index_path=index, rules_path=rules)
    bundle = tmp_path / "bundle.zip"
    checksum = original.export_bundle(bundle)
    matching = GenerationStore(root=tmp_path / "matching", embedding=identity)
    matching.import_bundle(bundle, expected_digest=checksum, source_settings=settings)
    assert settings.corpus_dir is not None
    policy = settings.corpus_dir / "A.md"
    policy.write_text(policy.read_text() + "\nA new source release.\n")
    stale = GenerationStore(root=tmp_path / "must-not-create", embedding=identity)
    with pytest.raises(GenerationError, match="don't match the packaged source"):
        stale.import_bundle(bundle, expected_digest=checksum, source_settings=settings)
    assert not stale.root.exists()


def test_bundle_export_refuses_overwrite_and_checks_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Export preserves manual files and rejects a destination replaced before verification."""
    from policynim.storage import generations

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    existing = tmp_path / "manual.zip"
    existing.write_bytes(b"PRESERVE-MANUAL-FILE")
    with pytest.raises(FileExistsError):
        store.export_bundle(existing)
    assert existing.read_bytes() == b"PRESERVE-MANUAL-FILE"
    destination = tmp_path / "new.zip"
    real_sync = generations._sync_directory

    def replace_after_write(path: Path) -> None:
        """Replace only the exported directory entry before its readback verification."""
        real_sync(path)
        if path == destination.parent:
            destination.rename(tmp_path / "original-output.zip")
            destination.write_bytes(b"REPLACEMENT-CANARY")

    monkeypatch.setattr(generations, "_sync_directory", replace_after_write)
    with pytest.raises(generations.GenerationError, match="changed before verification"):
        store.export_bundle(destination)
    assert destination.read_bytes() == b"REPLACEMENT-CANARY"


def test_staging_tamper_before_commit_preserves_prior_active(tmp_path: Path) -> None:
    """A source/rules change between initial validation and activation never becomes canonical."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules)
    pointer = (store.root / "active.json").read_bytes()
    changed = []

    def tamper(name: str) -> None:
        """Replace the newly installed rules immediately before activation."""
        if name == "before_activate":
            target = next(
                path
                for path in (store.root / "objects").iterdir()
                if path.name != first.generation_id
            )
            (target / "runtime_rules.json").write_text('{"schema_version":1,"rules":[]}')
            changed.append(target)

    with pytest.raises(GenerationError):
        GenerationStore(root=store.root, embedding=identity, fault=tamper).build(
            settings, MockEmbedder()
        )
    assert changed and changed[0].exists()
    assert (store.root / "active.json").read_bytes() == pointer
    assert store.inspect().generation_id == first.generation_id


def test_changed_active_pointer_is_preserved_at_commit_boundary(tmp_path: Path) -> None:
    """A manual activation change during preparation isn't overwritten by a stale commit."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules)
    second = store.build(settings, MockEmbedder())
    replacement = json.dumps(
        {"schema_version": 1, "active": first.generation_id, "previous": second.generation_id}
    ).encode()

    def change_pointer(name: str) -> None:
        """Simulate an uncooperative external editor at the last validation boundary."""
        if name == "before_activate":
            (store.root / "active.json").write_bytes(replacement)

    with pytest.raises(GenerationError, match="Active state changed"):
        GenerationStore(root=store.root, embedding=identity, fault=change_pointer).build(
            settings, MockEmbedder()
        )
    assert (store.root / "active.json").read_bytes() == replacement
    assert store.inspect().generation_id == first.generation_id


@pytest.mark.parametrize("generation_id", ["", "../outside", "A" * 64, "https://example.invalid"])
def test_explicit_invalid_generation_ids_never_fall_back_to_active(
    tmp_path: Path, generation_id: str
) -> None:
    """An invalid explicit selection can't silently open a different generation."""
    from policynim.storage.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    with pytest.raises(GenerationError):
        store.pin(generation_id)


def test_invalid_runtime_regex_fails_before_embedding(tmp_path: Path) -> None:
    """A complete generation can't publish rules that the real runtime consumer rejects."""
    from policynim.storage.generations import GenerationError

    source = write_pack(tmp_path / "policies", "test.public", ["A"])
    policy = source / "A.md"
    policy.write_text(policy.read_text().replace('"^A$"', '"["'))
    settings = Settings(
        **NO_ENV,
        corpus_dir=source,
        nvidia_embed_model="test-embed",
        nvidia_base_url="https://example.invalid/v1",
    )
    identity = EmbeddingIdentity(
        model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
    )

    class RecordingEmbedder(MockEmbedder):
        """Count attempts so source rejection can't hide a model request."""

        calls = 0

        def embed_documents(self, texts):
            """Record only an explicit embedding invocation."""
            self.calls += 1
            return super().embed_documents(texts)

    embedder = RecordingEmbedder()
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    with pytest.raises(GenerationError):
        store.build(settings, embedder)
    assert embedder.calls == 0
    assert not (store.root / "active.json").exists()
