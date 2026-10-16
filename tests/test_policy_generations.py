"""Offline lifecycle proofs for complete immutable policy generations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_ingest_service import MockEmbedder
from test_policy_profiles import write_pack

from policynim.services.generations import GenerationStore
from policynim.services.ingest import IngestService
from policynim.settings import Settings, get_settings
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


@pytest.mark.parametrize("spelling", ["./A.md", "nested//./A.md"])
@pytest.mark.parametrize("producer", ["build", "migrate"])
@pytest.mark.parametrize("profiled", [False, True])
def test_normalized_pack_members_survive_generation_and_bundle_transport(
    tmp_path: Path, spelling: str, producer: str, profiled: bool
) -> None:
    """Every spelling accepted by the ordinary pack loader keeps its captured identity."""
    from pathlib import PurePosixPath

    from policynim.ingest.loader import load_policy_documents
    from policynim.services.generation_sources import SourceSnapshot
    from policynim.services.profiles import initialize_profile, resolve_profile

    source = write_pack(tmp_path / "policies", "test.paths", ["A"])
    relative = PurePosixPath(spelling).as_posix()
    if relative != "A.md":
        destination = source / relative
        destination.parent.mkdir()
        (source / "A.md").rename(destination)
    manifest = source / "pack.toml"
    raw_manifest = manifest.read_text().replace('path = "A.md"', f'path = "{spelling}"')
    manifest.write_text(raw_manifest)
    raw_manifest = manifest.read_bytes().decode("utf-8")
    profile = tmp_path / "profile.toml" if profiled else None
    if profile is not None:
        initialize_profile(profile, [source], profile_id="paths")
        expected = resolve_profile(profile).documents
    else:
        expected = load_policy_documents(source)
    identity = EmbeddingIdentity(model="test-embed", endpoint="https://example.invalid/v1")
    settings = Settings(
        **NO_ENV,
        corpus_dir=source if profile is None else None,
        profile=profile,
        nvidia_embed_model=identity.model,
        nvidia_base_url=identity.endpoint,
        index_db_path=tmp_path / "legacy.sqlite3",
        runtime_rules_artifact_path=tmp_path / "legacy-rules.json",
    )
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    if producer == "migrate":
        with IngestService(
            embedder=MockEmbedder(),
            index_store=SQLiteVecIndexStore(
                path=settings.index_db_path, embedding_identity=identity
            ),
            corpus_root=source,
            profile_path=profile,
            embedding_model=identity.model,
            runtime_rules_artifact_path=settings.runtime_rules_artifact_path,
        ) as service:
            service.run()
        result = store.migrate(
            settings,
            index_path=settings.index_db_path,
            rules_path=settings.runtime_rules_artifact_path,
        )
    else:
        result = store.build(settings, MockEmbedder())
    with store.pin() as snapshot:
        assert snapshot.documents == expected
        captured = SourceSnapshot.model_validate_json(
            (snapshot.directory / "snapshot.json").read_bytes()
        )
        assert captured.packs[0].manifest_text == raw_manifest
        assert manifest.read_bytes() == raw_manifest.encode("utf-8")
        assert captured.packs[0].members[0].path == relative
    bundle = tmp_path / "paths.zip"
    digest = store.export_bundle(bundle)
    manifest.rename(source / "pack.toml.preserved")
    imported = GenerationStore(root=tmp_path / "imported", embedding=identity)
    assert (
        imported.import_bundle(bundle, expected_digest=digest).generation_id == result.generation_id
    )
    with imported.pin() as snapshot:
        assert snapshot.documents == expected


def test_snapshot_manifest_rejects_normalized_duplicate_declarations(tmp_path: Path) -> None:
    """Pure reconstruction must reject aliases even when one declaration matches the document."""
    from policynim.errors import InvalidPolicyDocumentError
    from policynim.ingest.loader import load_policy_documents
    from policynim.services.generation_sources import SourceMember, SourcePack

    source = write_pack(tmp_path / "policies", "test.paths", ["A"])
    manifest = source / "pack.toml"
    raw = manifest.read_text().replace('policy_id = "A"', 'policy_id = "B"')
    raw += '\n[[policies]]\npolicy_id = "A"\npath = "./A.md"\n'
    manifest.write_text(raw)
    with pytest.raises(InvalidPolicyDocumentError, match="repeats a member"):
        load_policy_documents(source)
    snapshot = SourcePack(
        root_name="policies",
        manifest_text=raw,
        members=[SourceMember(path="./A.md", text=(source / "A.md").read_text())],
    )
    with pytest.raises(InvalidPolicyDocumentError, match="repeats a normalized member"):
        snapshot.parse()


@pytest.mark.parametrize("filename", ["invalid:v1.md", "invalid\\v1.md"])
def test_snapshot_manifest_cant_promote_legacy_only_filename_syntax(
    tmp_path: Path, filename: str
) -> None:
    """Captured manifests retain the ordinary pack contract without narrowing legacy directories."""
    from policynim.errors import InvalidPolicyDocumentError
    from policynim.services.generation_sources import SourceMember, SourcePack

    source = write_pack(tmp_path / "policies", "test.paths", ["A"])
    raw = (
        (source / "pack.toml")
        .read_text()
        .replace('path = "A.md"', "path = " + json.dumps(filename))
    )
    snapshot = SourcePack(
        root_name="policies",
        manifest_text=raw,
        members=[SourceMember(path=filename, text=(source / "A.md").read_text())],
    )
    with pytest.raises(InvalidPolicyDocumentError, match="relative Markdown"):
        snapshot.parse()


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

    from policynim.services.generation_sources import digest_bytes
    from policynim.services.generations import GenerationError

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
    from policynim.services.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    generations.migrate(settings, index_path=index, rules_path=rules)
    with generations.pin():
        pass
    wrong = GenerationStore(
        root=generations.root,
        embedding=identity.model_copy(update={"model": "different-model-with-two-dimensions"}),
    )
    with pytest.raises(GenerationError):
        wrong.pin()


def test_dangling_object_entry_rejects_before_stage_installation(tmp_path: Path) -> None:
    """Unexpected names stay preserved, and failed activation leaves no ownerless stage."""
    import os

    from policynim.errors import GenerationError

    if os.name == "nt":
        pytest.skip("Creating test symlinks requires Windows elevation.")
    settings, identity, index, rules = seed_legacy(tmp_path)
    original = GenerationStore(root=tmp_path / "original", embedding=identity)
    first = original.migrate(settings, index_path=index, rules_path=rules).generation_id
    bundle = tmp_path / "original.zip"
    digest = original.export_bundle(bundle)
    store = GenerationStore(root=tmp_path / "destination", embedding=identity)
    current = store.build(settings, MockEmbedder()).generation_id
    assert current != first
    target = store.root / "objects" / first
    missing = tmp_path / "preserved-missing-target"
    target.symlink_to(missing, target_is_directory=True)
    pointer = (store.root / "active.json").read_bytes()
    steps: list[str] = []
    observed = GenerationStore(root=store.root, embedding=identity, fault=steps.append)
    with pytest.raises(GenerationError) as error:
        observed.import_bundle(bundle, expected_digest=digest)
    assert error.value.phase == "inspect"
    assert "object_rename" not in steps
    assert target.is_symlink() and target.readlink() == missing
    assert not missing.exists()
    assert (store.root / "active.json").read_bytes() == pointer
    assert not list((store.root / "staging").iterdir())


def test_queued_build_rejects_changed_sources_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A waiting build can't restore captured stale policy text over a newer publication."""
    from threading import Event, Thread, current_thread

    import policynim.services.generations as generation_module
    from policynim.errors import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    assert settings.corpus_dir is not None
    member = next(settings.corpus_dir.rglob("*.md"))
    old = member.read_text()
    newer = old + "\nNewest reviewed revision.\n"
    member.write_text(newer)
    blocked, release, captured = Event(), Event(), Event()
    original_capture = generation_module.capture_sources
    successful: list[str] = []
    first_errors: list[BaseException] = []
    queued_errors: list[BaseException] = []
    factory_calls: list[str] = []

    def capture(settings: Settings):
        """Observe the queued caller's detached snapshot before it acquires writer ownership."""
        result = original_capture(settings)
        if current_thread().name == "queued-publisher":
            captured.set()
        return result

    class BlockingEmbedder(MockEmbedder):
        def embed_documents(self, texts):
            """Keep the first actual producer in flight while the other caller captures old text."""
            blocked.set()
            assert release.wait(10)
            return super().embed_documents(texts)

    def publish_newer() -> None:
        """Finish the newer captured revision before the waiting producer can acquire its lock."""
        try:
            successful.append(store.build(settings, BlockingEmbedder()).generation_id)
        except BaseException as error:
            first_errors.append(error)

    def provider():
        """Record construction so stale queued work must reject before any usage can begin."""
        factory_calls.append("create")
        return MockEmbedder()

    def publish_queued() -> None:
        """Try to publish an earlier source snapshot after a real writer wait."""
        try:
            store.build(settings, embedder_factory=provider)
        except BaseException as error:
            queued_errors.append(error)

    monkeypatch.setattr(generation_module, "capture_sources", capture)
    writer = Thread(target=publish_newer, name="newer-publisher")
    queued = Thread(target=publish_queued, name="queued-publisher")
    writer.start()
    try:
        assert blocked.wait(3)
        member.write_text(old)
        queued.start()
        assert captured.wait(3)
        member.write_text(newer)
    finally:
        release.set()
        for thread in (writer, queued):
            if thread.ident is not None:
                thread.join(5)
    assert not writer.is_alive() and not queued.is_alive()
    assert not first_errors and len(successful) == 1
    assert len(queued_errors) == 1 and isinstance(queued_errors[0], GenerationError)
    assert factory_calls == []
    with store.pin() as snapshot:
        assert snapshot.generation_id == successful[0]
        assert snapshot.activation is not None and snapshot.activation.previous == first
        assert any("Newest reviewed revision" in document.body for document in snapshot.documents)
    assert member.read_text() == newer


def test_retention_records_safe_cause_and_location_for_each_failed_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Cleanup diagnostics identify owned locations and causes without raw private error text."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    trash = store.root / "trash" / "permission-case"
    trash.mkdir()
    stage = store.root / "staging" / "unexpected-case"
    stage.mkdir()
    canary = stage / "private.txt"
    canary.write_text("PRIVATE-CANARY")

    def deny(path: Path) -> None:
        """Inject a filesystem access error at the actual retention cleanup boundary."""
        raise PermissionError(13, "PRIVATE-CANARY")

    monkeypatch.setattr(store, "_clean_trash", deny)
    with caplog.at_level("WARNING", logger="policynim.services.generations"):
        result = store.prune(keep_recent=0)
    output = "\n".join(result.warnings) + caplog.text
    assert (
        "trash/permission-case" in output and "PermissionError" in output and "errno=13" in output
    )
    assert "staging/unexpected-case" in output and "GenerationError" in output
    assert "Unexpected staging contents" in output
    assert "PRIVATE-CANARY" not in output
    assert trash.is_dir() and canary.read_text() == "PRIVATE-CANARY"


@pytest.mark.parametrize("producer", ["migrate", "source_matched_import"])
def test_waiting_source_bound_producers_recheck_before_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, producer: str
) -> None:
    """Source-bound offline producers reject changed selection and permit a valid retry."""
    from contextlib import contextmanager

    from policynim.errors import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    bundle = tmp_path / "input.zip"
    digest = store.export_bundle(bundle)
    before = (
        index.read_bytes(),
        rules.read_bytes(),
        bundle.read_bytes(),
        (store.root / "active.json").read_bytes(),
    )
    assert settings.corpus_dir is not None
    member = next(settings.corpus_dir.rglob("*.md"))
    original_text = member.read_text()
    original_writer = store._writer

    @contextmanager
    def changed_selection(*, initialize: bool = False):
        """Change mutable inputs after actual writer ownership but before its operation begins."""
        with original_writer(initialize=initialize):
            member.write_text(original_text + "\nChanged while waiting.\n")
            yield

    def publish():
        """Exercise the actual public producer and its explicit freshness contract."""
        if producer == "migrate":
            return store.migrate(settings, index_path=index, rules_path=rules)
        return store.import_bundle(bundle, expected_digest=digest, source_settings=settings)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_writer", changed_selection)
        with pytest.raises(GenerationError, match="Sources changed while waiting"):
            publish()
    assert before == (
        index.read_bytes(),
        rules.read_bytes(),
        bundle.read_bytes(),
        (store.root / "active.json").read_bytes(),
    )
    assert not list((store.root / "staging").iterdir())
    assert "Changed while waiting" in member.read_text()
    member.write_text(original_text)
    result = publish()
    assert store.inspect().generation_id == result.generation_id


@pytest.mark.parametrize("artifact", ["manifest.json", "index.sqlite3"])
def test_export_rejects_bytes_changed_after_validation_before_packaging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, artifact: str
) -> None:
    """The exported bytes must match the validated manifest before any destination is written."""
    import policynim.services.generations as generation_module
    from policynim.errors import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    active = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    target = store.root / "objects" / active / artifact
    pointer = (store.root / "active.json").read_bytes()
    original = generation_module._read_regular
    calls: list[str] = []

    def replace_before_read(path: Path, **kwargs):
        """Change the bytes only on the packaging read, after full validation already passed."""
        if path == target:
            calls.append(path.name)
            if len(calls) == 2:
                target.write_bytes(target.read_bytes() + b" \n")
        return original(path, **kwargs)

    monkeypatch.setattr(generation_module, "_read_regular", replace_before_read)
    destination = tmp_path / "must-not-exist.zip"
    with pytest.raises(GenerationError):
        store.export_bundle(destination)
    assert len(calls) == 2
    assert not destination.exists()
    assert target.read_bytes().endswith(b" \n")
    assert (store.root / "active.json").read_bytes() == pointer


@pytest.mark.parametrize("mutation", ["missing", "equal_count_orphan"])
@pytest.mark.parametrize("producer", ["migrate", "import"])
def test_publication_rejects_unpaired_vector_rows_without_changing_prior_state(
    tmp_path: Path, mutation: str, producer: str
) -> None:
    """Vector counts and row ownership must agree before an imported index becomes active."""
    from contextlib import closing
    from hashlib import sha256
    from zipfile import ZipFile

    from policynim.errors import GenerationError
    from policynim.storage.sqlite_vec import _connect

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    pointer = (store.root / "active.json").read_bytes()
    original = {p.name: p.read_bytes() for p in (store.root / "objects" / first).iterdir()}
    if producer == "import":
        index = tmp_path / "bundle-index.sqlite3"
        index.write_bytes(original["index.sqlite3"])
    with closing(_connect(index, configure_wal=False)) as conn:
        row = conn.execute("SELECT rowid, embedding FROM policy_vectors LIMIT 1").fetchone()
        conn.execute("DELETE FROM policy_vectors WHERE rowid=?", (row["rowid"],))
        if mutation == "equal_count_orphan":
            conn.execute(
                "INSERT INTO policy_vectors (rowid, embedding) VALUES (?, ?)",
                (row["rowid"] + 1000000, row["embedding"]),
            )
        chunk_count = conn.execute("SELECT COUNT(*) FROM policy_chunks").fetchone()[0]
        vector_count = conn.execute("SELECT COUNT(*) FROM policy_vectors").fetchone()[0]
        assert (chunk_count == vector_count) == (mutation == "equal_count_orphan")
    damaged = index.read_bytes()
    bundle = tmp_path / "unpaired.zip"
    if producer == "import":
        manifest = json.loads(original["manifest.json"])
        original["index.sqlite3"] = damaged
        manifest["artifacts"]["index.sqlite3"] = "sha256:" + sha256(damaged).hexdigest()
        original["manifest.json"] = json.dumps(manifest).encode()
        with ZipFile(bundle, "w") as archive:
            for name, contents in original.items():
                archive.writestr(name, contents)
    with pytest.raises(GenerationError):
        if producer == "import":
            store.import_bundle(
                bundle, expected_digest="sha256:" + sha256(bundle.read_bytes()).hexdigest()
            )
        else:
            store.migrate(settings, index_path=index, rules_path=rules)
    assert (store.root / "active.json").read_bytes() == pointer
    assert store.inspect().generation_id == first
    assert index.read_bytes() == damaged
    if producer == "import":
        retry_bundle = tmp_path / "valid-retry.zip"
        digest = store.export_bundle(retry_bundle)
        retried = store.import_bundle(retry_bundle, expected_digest=digest)
        assert retried.generation_id == first
    else:
        retry_index = tmp_path / "valid-retry.sqlite3"
        retry_index.write_bytes((store.root / "objects" / first / "index.sqlite3").read_bytes())
        retried = store.migrate(settings, index_path=retry_index, rules_path=rules)
    assert store.inspect().generation_id == retried.generation_id
    assert index.read_bytes() == damaged


@pytest.mark.parametrize("invalid_rules", ["", "not JSON", '{"schema_version":1,"rules":[]}'])
def test_failed_migration_preserves_active_and_originals(
    tmp_path: Path, invalid_rules: str
) -> None:
    """An unrelated standalone rules artifact can't partially activate a new generation."""
    from policynim.services.generations import GenerationError

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
    from policynim.services.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = generations.migrate(settings, index_path=index, rules_path=rules)
    pointer = (generations.root / "active.json").read_bytes()
    assert settings.corpus_dir is not None
    (settings.corpus_dir / "A.md").write_text(
        (settings.corpus_dir / "A.md").read_text() + "\nNew rule.\n"
    )

    def fail(name: str) -> None:
        """Interrupt the selected filesystem boundary to verify preservation and recovery."""
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
        """Interrupt the selected filesystem boundary to verify preservation and recovery."""
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
    from policynim.services.generations import GenerationError

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
        """Hold the first publisher inside its serialized preparation boundary."""
        if name == "before_activate":
            first_entered.set()
            assert release_first.wait(5)

    def second_step(name: str) -> None:
        """Record when the second publisher enters the serialized boundary."""
        if name == "stage_mkdir":
            second_entered.set()

    def publish(label: str, store: GenerationStore) -> None:
        """Build one complete generation and capture any competing-writer failure."""
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
    from policynim.services.generation_sources import digest_bytes

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
    from policynim.services.generations import GenerationError

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
        """Interrupt the selected filesystem boundary to verify preservation and recovery."""
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
    from policynim.services.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    root = tmp_path / "generations"
    hit = False

    def fail(name: str) -> None:
        """Interrupt the selected filesystem boundary to verify preservation and recovery."""
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
from policynim.services.generations import GenerationStore
from policynim.types import EmbeddingIdentity
payload=json.loads(sys.argv[1])
settings=Settings(_env_file=None,corpus_dir=Path(payload["source"]),
                  nvidia_embed_model=payload["model"],nvidia_base_url=payload["endpoint"])
def terminate(name):
 "Terminate the child process at the selected publication boundary."
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

    from policynim.services.generation_sources import digest_bytes
    from policynim.services.generations import GenerationError

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
    from policynim.services.generations import GenerationError

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
    from policynim.services import generations

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
    from policynim.services.generations import GenerationError

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
    from policynim.services.generations import GenerationError

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
    from policynim.services.generations import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    with pytest.raises(GenerationError):
        store.pin(generation_id)


def test_invalid_runtime_regex_fails_before_embedding(tmp_path: Path) -> None:
    """A complete generation can't publish rules that the real runtime consumer rejects."""
    from policynim.services.generations import GenerationError

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


def test_default_index_factory_pins_the_active_complete_generation(tmp_path: Path) -> None:
    """Runtime store selection uses active generation state rather than standalone paths."""
    from policynim.errors import MissingIndexError
    from policynim.services.index_store import create_index_store

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    generations = GenerationStore(root=settings.generations_dir, embedding=identity)
    published = generations.migrate(settings, index_path=index, rules_path=rules)
    store = create_index_store(settings)
    assert store.generation_id == published.generation_id
    assert store.path == generations.root / "objects" / published.generation_id / "index.sqlite3"
    assert list((generations.root / "leases" / published.generation_id).glob("*.lock"))
    store.close()
    store.close()
    with pytest.raises(MissingIndexError, match="closed"):
        store.list_chunks()


def test_default_runtime_uses_its_generation_rules_and_source_evidence(tmp_path: Path) -> None:
    """Unrelated legacy rules can't turn a sealed block rule into an unmatched allow."""
    from policynim.services.runtime_decision import create_runtime_decision_service
    from policynim.types import ShellCommandActionRequest

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    generations = GenerationStore(root=settings.generations_dir, embedding=identity)
    published = generations.migrate(settings, index_path=index, rules_path=rules)
    rules.write_text('{"schema_version":1,"rules":[]}')
    with create_runtime_decision_service(settings) as service:
        result = service.decide(
            ShellCommandActionRequest(
                kind="shell_command", command=["A"], task="Check sealed policy A.", cwd=tmp_path
            )
        )
    assert result.decision == "block"
    assert result.generation_id == published.generation_id
    assert result.citations and all(
        citation.chunk_id.startswith("source:") for citation in result.citations
    )


@pytest.mark.parametrize("alias", [False, True])
def test_managed_artifacts_reject_raw_adapter_mutation(tmp_path: Path, alias: bool) -> None:
    """Raw adapters can't overwrite managed objects through directory aliases."""
    from policynim.errors import IndexCompatibilityError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    snapshot = store.inspect()
    path = snapshot.index_path
    if alias:
        linked = tmp_path / "alias"
        linked.symlink_to(snapshot.directory, target_is_directory=True)
        path = linked / "index.sqlite3"
    raw = SQLiteVecIndexStore(path=path, embedding_identity=identity)
    before = snapshot.index_path.read_bytes()
    with pytest.raises(IndexCompatibilityError, match="immutable"):
        raw.validate_replacement()
    assert snapshot.index_path.read_bytes() == before


def test_generation_pin_obeys_deadline_while_publisher_lock_is_held(tmp_path: Path) -> None:
    """A waiting reader can stop before provider construction without leaving a held lease."""
    from threading import Event, Thread

    from policynim.errors import ProviderError
    from policynim.operations import operation_scope
    from policynim.services.index_store import create_index_store
    from policynim.storage.file_lock import exclusive_file_lock

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    held, release = Event(), Event()

    def hold_publisher() -> None:
        """Keep the real stable lock held until the waiting caller exits."""
        with exclusive_file_lock(store.root / ".publish.lock"):
            held.set()
            assert release.wait(5)

    thread = Thread(target=hold_publisher)
    thread.start()
    try:
        assert held.wait(2)
        with pytest.raises(ProviderError) as error, operation_scope(seconds=0.1):
            create_index_store(settings)
        assert error.value.failure_class == "deadline_exhausted"
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert not list((store.root / "leases").rglob("*.lock"))
    with store.pin():
        pass


def test_health_and_catalog_follow_activation_and_release_their_readers(tmp_path: Path) -> None:
    """Long-lived health checks reacquire active state while source catalogs stay explicit."""
    from policynim.services.catalog import build_policy_catalog
    from policynim.services.health import create_runtime_health_service

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    health = create_runtime_health_service(settings)
    assert health.check().generation_id == first
    source = settings.corpus_dir
    assert source is not None
    member = next(source.rglob("*.md"))
    member.write_text(member.read_text() + "\nReview deployment failures.\n")
    assert build_policy_catalog(settings=settings).generation_id == first
    assert build_policy_catalog(settings=settings, source_only=True).generation_id is None
    second = store.build(settings, embedder=MockEmbedder()).generation_id
    assert health.check().generation_id == second
    catalog = build_policy_catalog(settings=settings)
    assert catalog.generation_id == second and catalog.scope == "generation"
    assert catalog.indexed_count == 2
    assert not list((store.root / "leases").rglob("*.lock"))


def test_default_search_preflight_and_execution_keep_generation_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Use the real default service graph, SQLite search, and evidence store with offline models."""
    from test_index_identity import SpyEmbedder
    from test_preflight_service import MockReranker

    import policynim.services.compiler as compiler_module
    import policynim.services.preflight as preflight_module
    import policynim.services.router as router_module
    import policynim.services.search as search_module
    from policynim.services.evidence_trace import create_policy_evidence_trace_service
    from policynim.services.runtime_evidence_report import create_runtime_evidence_report_service
    from policynim.services.runtime_execution import create_runtime_execution_service
    from policynim.types import (
        FileWriteActionRequest,
        GeneratedCompiledPolicyDraft,
        GeneratedPolicyConstraint,
        GeneratedPolicyGuidance,
        GeneratedPreflightDraft,
        PreflightRequest,
        SearchRequest,
    )

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(
        update={
            "generations_dir": tmp_path / "generations",
            "runtime_evidence_db_path": tmp_path / "evidence.sqlite3",
        }
    )
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    original = store.migrate(settings, index_path=index, rules_path=rules).generation_id

    class PositiveReranker(MockReranker):
        def rerank(self, query, candidates, *, top_k):
            """Supply positive deterministic ranking signals for the real SQLite candidates."""
            return [chunk.model_copy(update={"score": 1.0}) for chunk in candidates[:top_k]]

    class Compiler:
        def compile_policy_packet(self, request, selection_packet, context):
            """Compile a cited constraint from the actual retrieved generation context."""
            return GeneratedCompiledPolicyDraft(
                required_steps=[
                    GeneratedPolicyConstraint(
                        statement="Review deployment evidence.", citation_ids=[context[0].chunk_id]
                    )
                ]
            )

        def close(self):
            """Record or release the offline resource at its ownership boundary."""
            pass

    class Generator:
        def generate_preflight(
            self, request, context, *, compiled_packet=None, regeneration_context=None
        ):
            """Return a grounded draft using the actual retained chunk and policy IDs."""
            return GeneratedPreflightDraft(
                summary="Review deployment evidence.",
                applicable_policies=[
                    GeneratedPolicyGuidance(
                        policy_id=context[0].policy.policy_id,
                        title=context[0].policy.title,
                        rationale="Selected policy evidence.",
                        citation_ids=[context[0].chunk_id],
                    )
                ],
                plan_steps=["Review deployment evidence."],
                citation_ids=[context[0].chunk_id],
            )

        def close(self):
            """Record or release the offline resource at its ownership boundary."""
            pass

    monkeypatch.setattr(
        search_module,
        "_create_default_search_components",
        lambda _: (SpyEmbedder(), PositiveReranker()),
    )
    monkeypatch.setattr(
        router_module,
        "_create_default_router_components",
        lambda _: (SpyEmbedder(), PositiveReranker()),
    )
    monkeypatch.setattr(compiler_module, "_create_default_policy_compiler", lambda _: Compiler())
    monkeypatch.setattr(preflight_module, "_create_default_generator", lambda _: Generator())
    with (
        search_module.create_search_service(settings) as search,
        preflight_module.create_preflight_service(settings) as preflight,
    ):
        source = settings.corpus_dir
        assert source is not None
        member = next(source.rglob("*.md"))
        for number in range(4):
            member.write_text(member.read_text() + f"\nReviewed release {number}.\n")
            store.build(settings, embedder=MockEmbedder())
        store.prune(keep_recent=0)
        found = search.search(SearchRequest(query="deployment"))
        trace_result = preflight.preflight_with_trace(PreflightRequest(task="deployment"))
        trace = create_policy_evidence_trace_service().build(trace_result)
        assert found.hits and not trace_result.result.insufficient_context
        assert found.generation_id == trace_result.result.generation_id == original
        assert trace_result.compiled_packet.generation_id == trace.generation_id == original
        assert all("Reviewed release" not in hit.text for hit in found.hits)
    store.prune(keep_recent=0)
    assert not (store.root / "objects" / original).exists()
    active = store.inspect().generation_id
    with create_runtime_execution_service(settings) as execution:
        executed = execution.execute(
            FileWriteActionRequest(
                kind="file_write",
                task="write output",
                session_id="generation-proof",
                cwd=tmp_path,
                path=tmp_path / "output",
                content="ok",
            )
        )
        failed = execution.execute(
            FileWriteActionRequest(
                kind="file_write",
                task="verify failure evidence",
                session_id="generation-proof",
                cwd=tmp_path,
                path=tmp_path,
                content="mustn't replace a directory",
            )
        )
    assert executed.generation_id == failed.generation_id == active
    assert failed.execution_outcome == "failed"
    from policynim.storage import RuntimeEvidenceStore

    events = RuntimeEvidenceStore(path=settings.runtime_evidence_db_path).list_session_events(
        "generation-proof"
    )
    assert [event.event_kind for event in events] == ["decision", "allowed", "decision", "failed"]
    assert all(event.generation_id == active for event in events)
    with create_runtime_evidence_report_service(settings) as reports:
        report = reports.report_session("generation-proof")
    assert all(item.generation_id == active for item in report.executions)
    from policynim.interfaces.cli import _runtime_evidence_report_markdown

    markdown = _runtime_evidence_report_markdown(report)
    assert "| Generation |" in markdown and markdown.count(active) == 2


@pytest.mark.parametrize("failure", [None, "embedding", "close"])
def test_owned_ingestion_provider_closes_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str | None,
) -> None:
    """The default command owns its lazy provider and preserves active state on provider failure."""
    from policynim.errors import GenerationError
    from policynim.services.ingest import create_ingest_service

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    before = (store.root / "active.json").read_bytes()
    calls: list[str] = []

    class OwnedEmbedder(MockEmbedder):
        def embed_documents(self, texts):
            """Record embedding work and inject the requested provider failure."""
            calls.append("embed")
            if failure == "embedding":
                raise RuntimeError("PRIVATE-CANARY embedding failure")
            return super().embed_documents(texts)

        def close(self):
            """Record or release the offline resource at its ownership boundary."""
            calls.append("close")
            if failure == "close":
                raise RuntimeError("PRIVATE-CANARY close failure")
            super().close()

    def factory(active_settings):
        """Construct the command-owned offline embedding provider lazily."""
        calls.append("create")
        return OwnedEmbedder()

    monkeypatch.setattr("policynim.services.ingest._create_default_embedder", factory)
    service = create_ingest_service(settings)
    assert calls == []
    with service:
        if failure:
            with pytest.raises(GenerationError) as error:
                service.run()
            assert "PRIVATE-CANARY" not in str(error.value)
            assert (store.root / "active.json").read_bytes() == before
        else:
            result = service.run()
            assert result.generation_id == store.inspect().generation_id
    assert calls == ["create", "embed", "close"]


@pytest.mark.parametrize("recovery", ["rollback", "activate", "import", "migrate"])
def test_offline_recovery_can_replace_corrupt_active_object_without_modifying_it(
    tmp_path: Path,
    recovery: str,
) -> None:
    """Validate the recovery target independently and preserve damaged old bytes."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    bundle = tmp_path / "trusted.zip"
    digest = store.export_bundle(bundle)
    source = settings.corpus_dir
    assert source is not None
    member = next(source.rglob("*.md"))
    original_text = member.read_text()
    member.write_text(original_text + "\nReviewed next revision.\n")
    second = store.build(settings, embedder=MockEmbedder()).generation_id
    member.write_text(original_text)
    damaged = store.root / "objects" / second / "index.sqlite3"
    damaged.write_bytes(damaged.read_bytes() + b"corrupt active canary")
    preserved = damaged.read_bytes()
    if recovery == "rollback":
        result = store.rollback(expected_active=second)
    elif recovery == "activate":
        result = store.activate(first, expected_active=second)
    elif recovery == "import":
        result = store.import_bundle(bundle, expected_digest=digest)
    else:
        result = store.migrate(settings, index_path=index, rules_path=rules)
    assert result.generation_id == first
    assert store.inspect().generation_id == first
    assert damaged.read_bytes() == preserved


def test_paid_build_rejects_corrupt_active_state_before_provider_construction(
    tmp_path: Path,
) -> None:
    """Offline recovery is explicit; ordinary builds can't spend usage on damaged active state."""
    from policynim.errors import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    active = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    damaged = store.root / "objects" / active / "index.sqlite3"
    damaged.write_bytes(damaged.read_bytes() + b"damaged")
    before = (store.root / "active.json").read_bytes()
    calls = []

    def provider():
        """Record any unauthorized construction before damaged-state rejection."""
        calls.append("create")
        return MockEmbedder()

    with pytest.raises(GenerationError):
        store.build(settings, embedder_factory=provider)
    assert calls == []
    assert (store.root / "active.json").read_bytes() == before


@pytest.mark.parametrize("filename", ["security:v1.md", "security\\v1.md", ".md"])
@pytest.mark.parametrize("producer", ["build", "migrate"])
def test_legacy_logical_filenames_survive_generation_and_bundle_transport(
    tmp_path: Path,
    filename: str,
    producer: str,
) -> None:
    """Captured paths are logical evidence, so valid POSIX legacy names need no materialization."""
    import os

    from policynim.ingest.loader import load_policy_documents

    if os.name == "nt" and (":" in filename or "\\" in filename):
        pytest.skip("These legacy filename characters require a POSIX source filesystem.")
    source = tmp_path / "legacy-policies"
    source.mkdir()
    (source / filename).write_text(
        "---\npolicy_id: LEGACY-001\ntitle: Legacy\n---\n# Legacy\nReview changes.\n"
    )
    identity = EmbeddingIdentity(model="test-embed", endpoint="https://example.invalid/v1")
    settings = Settings(
        **NO_ENV,
        corpus_dir=source,
        index_db_path=tmp_path / "legacy.sqlite3",
        runtime_rules_artifact_path=tmp_path / "legacy-rules.json",
        nvidia_embed_model=identity.model,
        nvidia_base_url=identity.endpoint,
    )
    expected = load_policy_documents(source)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    if producer == "migrate":
        with IngestService(
            embedder=MockEmbedder(),
            index_store=SQLiteVecIndexStore(
                path=settings.index_db_path, embedding_identity=identity
            ),
            corpus_root=source,
            embedding_model=identity.model,
            runtime_rules_artifact_path=settings.runtime_rules_artifact_path,
        ) as service:
            service.run()
        result = store.migrate(
            settings,
            index_path=settings.index_db_path,
            rules_path=settings.runtime_rules_artifact_path,
        )
    else:
        result = store.build(settings, embedder=MockEmbedder())
    bundle = tmp_path / "legacy.zip"
    digest = store.export_bundle(bundle)
    imported = GenerationStore(root=tmp_path / "imported", embedding=identity)
    assert (
        imported.import_bundle(bundle, expected_digest=digest).generation_id == result.generation_id
    )
    with imported.pin() as snapshot:
        assert snapshot.documents == expected


def test_paid_preparation_doesnt_block_new_readers_or_allow_pruning_its_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A real blocked provider keeps writers serialized while health and search read prior state."""
    from threading import Event, Thread

    from test_index_identity import SpyEmbedder
    from test_preflight_service import MockReranker

    import policynim.services.search as search_module
    from policynim.services.health import create_runtime_health_service
    from policynim.types import SearchRequest

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    source = settings.corpus_dir
    assert source is not None
    member = next(source.rglob("*.md"))
    member.write_text(member.read_text() + "\nNew prepared revision.\n")
    blocked, release, read_done, prune_done = Event(), Event(), Event(), Event()
    errors: list[BaseException] = []
    observed: list[str | None] = []

    class BlockingEmbedder(MockEmbedder):
        def embed_documents(self, texts):
            """Pause the actual provider boundary until the reader has attempted work."""
            blocked.set()
            assert release.wait(10)
            return super().embed_documents(texts)

    def publish() -> None:
        """Exercise real preparation and activation while recording any worker failure."""
        try:
            store.build(settings, embedder=BlockingEmbedder())
        except BaseException as exc:
            errors.append(exc)

    def read() -> None:
        """Exercise real default health and search against the still-active generation."""
        try:
            observed.append(create_runtime_health_service(settings).check().generation_id)
            with search_module.create_search_service(settings) as service:
                result = service.search(SearchRequest(query="deployment"))
                observed.append(result.generation_id)
                assert all("New prepared revision" not in hit.text for hit in result.hits)
        except BaseException as exc:
            errors.append(exc)
        finally:
            read_done.set()

    def prune() -> None:
        """Retention must wait for the serialized producer before inspecting staging."""
        try:
            store.prune(keep_recent=0)
        except BaseException as exc:
            errors.append(exc)
        finally:
            prune_done.set()

    monkeypatch.setattr(
        search_module,
        "_create_default_search_components",
        lambda _: (SpyEmbedder(), MockReranker()),
    )
    writer, reader, pruner = Thread(target=publish), Thread(target=read), Thread(target=prune)
    writer.start()
    try:
        assert blocked.wait(3)
        pruner.start()
        assert not prune_done.wait(0.1)
        reader.start()
        read_while_blocked = read_done.wait(3)
    finally:
        release.set()
        for worker in (writer, reader, pruner):
            if worker.ident is not None:
                worker.join(5)
    assert read_while_blocked, "Reader admission waited for the embedding provider"
    assert not errors
    assert observed == [first, first]
    assert prune_done.is_set() and store.inspect().generation_id != first


def test_warm_readers_reuse_validation_but_operator_and_changed_files_reinspect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Count actual artifact reads while exercising the default factory across store instances."""
    import policynim.services.generations as generation_module
    from policynim.errors import GenerationError
    from policynim.services.index_store import create_index_store

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    active = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    read_regular = generation_module._read_regular
    reads: list[str] = []

    def record(path: Path, **kwargs):
        """Observe corpus/index bytes rather than a cache implementation detail."""
        if path.name in {"index.sqlite3", "snapshot.json", "catalog.json", "runtime_rules.json"}:
            reads.append(path.name)
        return read_regular(path, **kwargs)

    monkeypatch.setattr(generation_module, "_read_regular", record)
    first = create_index_store(settings)
    first.close()
    assert "index.sqlite3" in reads
    count = len(reads)
    second = create_index_store(settings)
    second.close()
    assert len(reads) == count, "Unchanged reader rehashed the complete artifacts"
    store.inspect()
    assert len(reads) > count
    damaged = store.root / "objects" / active / "index.sqlite3"
    damaged.write_bytes(damaged.read_bytes() + b"changed artifact")
    with pytest.raises(GenerationError):
        create_index_store(settings)
    assert damaged.read_bytes().endswith(b"changed artifact")


def test_cached_snapshot_models_arent_shared_with_callers(tmp_path: Path) -> None:
    """Mutating one returned model can't change what a later reader sees."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    with store.pin() as first:
        expected = first.catalog.model_dump_json(), first.documents[0].body
        first.catalog.policies[0].body = "caller mutation"
        first.documents[0].body = "caller mutation"
    with store.pin() as second:
        assert (second.catalog.model_dump_json(), second.documents[0].body) == expected


def test_forked_reader_keeps_its_marker_after_parent_close(tmp_path: Path) -> None:
    """An inherited kernel lease must retain its object until the forked reader closes too."""
    import os
    import subprocess
    import sys

    if not hasattr(os, "fork"):
        pytest.skip("Inherited descriptors require POSIX fork support.")
    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    assert settings.corpus_dir is not None
    member = next(settings.corpus_dir.rglob("*.md"))
    for revision in range(3):
        member.write_text(member.read_text() + f"\nLater revision {revision}.\n")
        store.build(settings, MockEmbedder())
    script = r"""
import json,os,sys
from pathlib import Path
from policynim.services.generations import GenerationStore
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import EmbeddingIdentity
store=GenerationStore(root=Path(sys.argv[1]),embedding=EmbeddingIdentity.model_validate_json(sys.argv[3]))
snapshot=store.pin(sys.argv[2])
reader=SQLiteVecIndexStore(path=snapshot.index_path,embedding_identity=store.embedding,generation_snapshot=snapshot)
expected=reader.count()
go_read,go_write=os.pipe()
result_read,result_write=os.pipe()
pid=os.fork()
if pid==0:
 os.close(go_write);os.close(result_read)
 os.write(result_write,b'r')
 os.read(go_read,1)
 try:
  assert snapshot.index_path.exists() and reader.count()==expected
  reader.close()
  os.write(result_write,b'y')
 except BaseException:
  os.write(result_write,b'n')
  os._exit(2)
 os._exit(0)
os.close(go_read);os.close(result_write)
assert os.read(result_read,1)==b'r'
reader.close()
try:
 retained=sys.argv[2] in store.prune(keep_recent=0).kept
finally:
 os.write(go_write,b'g')
 child_read=os.read(result_read,1)==b'y'
 _,status=os.waitpid(pid,0)
 os.close(go_write);os.close(result_read)
assert retained and child_read and os.waitstatus_to_exitcode(status)==0
assert sys.argv[2] in store.prune(keep_recent=0).removed
print(json.dumps({'inherited_reader_retained':True,'released_object_pruned':True}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(store.root), first, identity.model_dump_json()],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["inherited_reader_retained"]


@pytest.mark.parametrize("replacement_time", ["before_open", "during_open"])
def test_pinned_search_rejects_observed_index_replacement_before_provider_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, replacement_time: str
) -> None:
    """Per-open identity checks reject a changed pinned index without returning mixed evidence."""
    import os
    import sqlite3

    from test_index_identity import SpyEmbedder
    from test_preflight_service import MockReranker

    import policynim.services.search as search_module
    from policynim.errors import GenerationError
    from policynim.services.index_store import pinned_generation
    from policynim.storage import sqlite_vec as adapter
    from policynim.types import SearchRequest

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    embedder = SpyEmbedder()
    monkeypatch.setattr(
        search_module, "_create_default_search_components", lambda _: (embedder, MockReranker())
    )
    with search_module.create_search_service(settings) as service:
        snapshot = pinned_generation(service._index_store)
        assert snapshot is not None and settings.corpus_dir is not None
        member = next(settings.corpus_dir.rglob("*.md"))
        member.write_text(member.read_text() + "\nNext generation content.\n")
        second = store.build(settings, MockEmbedder()).generation_id
        replacement = tmp_path / "replacement.sqlite3"
        data = (store.root / "objects" / second / "index.sqlite3").read_bytes()
        replacement.write_bytes(data)
        original = adapter._connect
        opened: list[sqlite3.Connection] = []

        def connect(path: Path, **kwargs):
            """Replace only this pinned index immediately before its actual SQLite open."""
            if path == snapshot.index_path:
                if replacement_time == "during_open" and replacement.exists():
                    os.replace(replacement, path)
                connection = original(path, **kwargs)
                opened.append(connection)
                return connection
            return original(path, **kwargs)

        monkeypatch.setattr(adapter, "_connect", connect)
        if replacement_time == "before_open":
            os.replace(replacement, snapshot.index_path)
        with pytest.raises(GenerationError):
            service.search(SearchRequest(query="deployment"))
        assert embedder.calls == 0
        assert snapshot.index_path.read_bytes() == data
        if replacement_time == "before_open":
            assert not opened
        else:
            assert opened
            with pytest.raises(sqlite3.ProgrammingError):
                opened[0].execute("SELECT 1")


def test_cache_rejects_same_size_tamper_with_restored_mtime(tmp_path: Path) -> None:
    """Native metadata-change stamps invalidate reuse even when size and mtime are restored."""
    import os

    from policynim.errors import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    with store.pin() as snapshot:
        path = snapshot.rules_path
    before = path.stat()
    contents = path.read_bytes()
    changed = contents.replace(b'"schema_version": 1', b'"schema_version": 9')
    assert changed != contents and len(changed) == len(contents)
    path.write_bytes(changed)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    with pytest.raises(GenerationError):
        store.pin()
    assert path.read_bytes() == changed
    assert not list((store.root / "leases").rglob("*.lock"))


def test_missing_change_stamp_forces_full_reader_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Filesystems lacking a trustworthy change stamp can't authorize cached acceptance."""
    import policynim.services.generations as generation_module

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    with store.pin():
        pass
    monkeypatch.setattr(generation_module, "_file_change_stamp", lambda *args: None)
    original = store._inspect
    inspected: list[Path] = []

    def record(directory: Path, **kwargs):
        """Observe complete validation after cache eligibility becomes unavailable."""
        inspected.append(directory)
        return original(directory, **kwargs)

    monkeypatch.setattr(store, "_inspect", record)
    for _ in range(2):
        with store.pin():
            pass
    assert len(inspected) == 2


def test_cold_reader_validation_doesnt_block_publication_and_keeps_its_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A paused cold inspector holds a lease while later publishers and retention finish."""
    from threading import Event, Thread

    settings, identity, index, rules = seed_legacy(tmp_path)
    publisher = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = publisher.migrate(settings, index_path=index, rules_path=rules).generation_id
    reader_store = GenerationStore(root=publisher.root, embedding=identity)
    inspecting, release = Event(), Event()
    original = reader_store._inspect
    errors: list[BaseException] = []
    observed: list[str] = []

    def pause(directory: Path, **kwargs):
        """Pause after lease registration but before complete source/index validation."""
        inspecting.set()
        assert release.wait(10)
        return original(directory, **kwargs)

    def read() -> None:
        """Keep the selected ID through validation even after active state changes."""
        try:
            with reader_store.pin() as snapshot:
                observed.append(snapshot.generation_id)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(reader_store, "_inspect", pause)
    reader = Thread(target=read)
    reader.start()
    try:
        assert inspecting.wait(3)
        assert settings.corpus_dir is not None
        member = next(settings.corpus_dir.rglob("*.md"))
        for revision in range(3):
            member.write_text(member.read_text() + f"\nLater revision {revision}.\n")
            publisher.build(settings, MockEmbedder())
        assert first in publisher.prune(keep_recent=0).kept
        assert (publisher.root / "objects" / first).is_dir()
    finally:
        release.set()
        reader.join(5)
    assert not reader.is_alive() and not errors
    assert observed == [first]
    assert first in publisher.prune(keep_recent=0).removed


def test_concurrent_cold_readers_share_validation_and_deadline_releases_waiter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Waiting for a shared inspection respects the request budget and releases only its lease."""
    from threading import Event, Thread

    from policynim.errors import ProviderError
    from policynim.operations import operation_scope

    settings, identity, index, rules = seed_legacy(tmp_path)
    first = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first.migrate(settings, index_path=index, rules_path=rules)
    second = GenerationStore(root=first.root, embedding=identity)
    inspecting, release = Event(), Event()
    original = first._inspect
    errors: list[BaseException] = []

    def pause(directory: Path, **kwargs):
        """Hold one cold inspection outside publication serialization."""
        inspecting.set()
        assert release.wait(5)
        return original(directory, **kwargs)

    def read() -> None:
        """Finish the cache population after a separate waiter exhausts its deadline."""
        try:
            with first.pin():
                pass
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(first, "_inspect", pause)
    reader = Thread(target=read)
    reader.start()
    try:
        assert inspecting.wait(3)
        with pytest.raises(ProviderError) as error, operation_scope(seconds=0.1):
            second.pin()
        assert error.value.failure_class == "deadline_exhausted"
        assert len(list((first.root / "leases").rglob("*.lock"))) == 1
    finally:
        release.set()
        reader.join(5)
    assert not reader.is_alive() and not errors
    assert not list((first.root / "leases").rglob("*.lock"))

    def unexpected_inspection(*args, **kwargs):
        """The next reader must reuse the other store's successful full validation."""
        pytest.fail("Concurrent successful validation wasn't reused")

    monkeypatch.setattr(second, "_inspect", unexpected_inspection)
    with second.pin():
        pass


def test_publisher_inspection_never_holds_reader_admission_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every full publication/retention inspection allows independent pointer readers."""
    from threading import Event, Thread

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    original = store._inspect
    admissions: list[bool] = []

    def inspect(directory: Path, **kwargs):
        """Try actual reader admission while each full inspector has control."""
        admitted = Event()

        def read_pointer() -> None:
            """Take the actual publication mutex from a separate thread."""
            store.read_activation()
            admitted.set()

        reader = Thread(target=read_pointer)
        reader.start()
        admissions.append(admitted.wait(1))
        reader.join(0.1)
        return original(directory, **kwargs)

    monkeypatch.setattr(store, "_inspect", inspect)
    store.build(settings, MockEmbedder())
    assert admissions and all(admissions)


@pytest.mark.parametrize("producer", ["build", "migrate", "import"])
def test_producers_reject_sources_inside_an_owned_store(tmp_path: Path, producer: str) -> None:
    """Nested inputs can't mix source files with managed object retention."""
    from policynim.errors import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    original = settings.corpus_dir
    assert original is not None
    source = original.rename(tmp_path / "objects")
    index, rules = tmp_path / "renamed.sqlite3", tmp_path / "renamed-rules.json"
    settings = settings.model_copy(update={"corpus_dir": source})
    with IngestService(
        embedder=MockEmbedder(),
        index_store=SQLiteVecIndexStore(path=index, embedding_identity=identity),
        corpus_root=source,
        embedding_model=identity.model,
        runtime_rules_artifact_path=rules,
    ) as service:
        service.run()
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    bundle = tmp_path / "bundle.zip"
    checksum = store.export_bundle(bundle)
    nested = write_pack(store.root / "objects", "test.public", ["A", "B"])
    selected = settings.model_copy(update={"corpus_dir": nested})
    before = {
        p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()
    }
    calls: list[str] = []

    def factory():
        calls.append("provider")
        return MockEmbedder()

    with pytest.raises(GenerationError, match="outside policy sources"):
        if producer == "build":
            store.build(selected, embedder_factory=factory)
        elif producer == "migrate":
            store.migrate(selected, index_path=index, rules_path=rules)
        else:
            store.import_bundle(bundle, expected_digest=checksum, source_settings=selected)
    assert not calls
    assert before == {
        p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()
    }


def test_build_rejects_profile_inside_an_owned_store(tmp_path: Path) -> None:
    """An editable profile can't become generation-root bookkeeping."""
    from policynim.errors import GenerationError
    from policynim.services.profiles import initialize_profile

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    profile = store.root / "profile.toml"
    assert settings.corpus_dir is not None
    initialize_profile(profile, [settings.corpus_dir], profile_id="nested")
    selected = settings.model_copy(update={"profile": profile, "corpus_dir": None})
    before = profile.read_bytes(), (store.root / "active.json").read_bytes()
    calls: list[str] = []

    def factory():
        calls.append("provider")
        return MockEmbedder()

    with pytest.raises(GenerationError, match="outside policy sources"):
        store.build(selected, embedder_factory=factory)
    assert not calls
    assert before == (profile.read_bytes(), (store.root / "active.json").read_bytes())


def test_autocrlf_checkout_imports_public_bundle_with_source_matching(tmp_path: Path) -> None:
    """Exercise Git's checkout conversion against the actual reviewed public bundle."""
    import os
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("Git isn't installed; it's only needed for this checkout regression.")
    root = Path(__file__).resolve().parents[1]
    repository = tmp_path / "repository"
    repository.mkdir()
    shutil.copytree(root / "policies", repository / "policies")
    shutil.copytree(root / "artifacts" / "public-generation", repository / "bundle")
    attributes = root / ".gitattributes"
    if attributes.exists():
        shutil.copy2(attributes, repository / attributes.name)
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for arguments in (
        ["init", "--quiet"],
        ["-c", "core.autocrlf=true", "add", "."],
        [
            "-c",
            "core.autocrlf=true",
            "checkout-index",
            "--all",
            "--force",
            f"--prefix={checkout.as_posix()}/",
        ],
    ):
        subprocess.run(
            [git, "-C", str(repository), *arguments],
            env=environment,
            check=True,
            capture_output=True,
        )
    settings = Settings(**NO_ENV, corpus_dir=checkout / "policies")
    store = GenerationStore(
        root=tmp_path / "generations",
        embedding=EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        ),
    )
    result = store.import_bundle(
        checkout / "bundle" / "generation.zip",
        expected_digest=(checkout / "bundle" / "generation.sha256").read_text().strip(),
        source_settings=settings,
    )
    assert result.manifest.document_count == 15
    for path in (checkout / "policies").rglob("*"):
        if path.is_file() and path.suffix in {".md", ".toml", ".txt"}:
            assert b"\r\n" not in path.read_bytes()


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_failed_pin_leaves_no_new_lease_directory(tmp_path: Path, failure: str) -> None:
    """Repeated rejected opens leave no markers or empty per-ID lease folders."""
    from policynim.errors import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    active = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    selected = "0" * 64 if failure == "missing" else active
    artifact = store.root / "objects" / active / "runtime_rules.json"
    if failure == "corrupt":
        artifact.write_bytes(artifact.read_bytes() + b"damaged")
    before = artifact.read_bytes(), (store.root / "active.json").read_bytes()
    for _ in range(2):
        with pytest.raises(GenerationError):
            store.pin(selected)
        assert not (store.root / "leases" / selected).exists()
    assert before == (artifact.read_bytes(), (store.root / "active.json").read_bytes())


def test_failed_pin_preserves_unexpected_lease_directory_contents(tmp_path: Path) -> None:
    """Failure cleanup removes only owned empty directories and owned marker files."""
    from policynim.errors import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    active = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    note = store.root / "leases" / active / "operator-note"

    def fault(step: str) -> None:
        if step == "inspect":
            note.write_bytes(b"preserve this recovery note")
            raise GenerationError("inspect", "Injected rejection.")

    rejected = GenerationStore(root=store.root, embedding=identity, fault=fault)
    with pytest.raises(GenerationError, match="Injected rejection"):
        rejected.pin()
    assert list(note.parent.iterdir()) == [note]
    assert note.read_bytes() == b"preserve this recovery note"


def test_http_health_reuses_validation_outside_publication_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real public health probes admit pointer reads during cold validation and reuse warm data."""
    from threading import Event, Thread

    from starlette.testclient import TestClient

    import policynim.services.generations as generation_module
    from policynim.interfaces.mcp import _build_streamable_http_app

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    active = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    original_inspect, original_read = GenerationStore._inspect, generation_module._read_regular
    reads: list[str] = []
    admissions: list[bool] = []
    workers: list[Thread] = []

    def inspect(self, *args, **kwargs):
        admitted = Event()

        def read_pointer():
            store.read_activation()
            admitted.set()

        worker = Thread(target=read_pointer, daemon=True)
        workers.append(worker)
        worker.start()
        admissions.append(admitted.wait(3))
        return original_inspect(self, *args, **kwargs)

    def read(path: Path, **kwargs):
        if path.parent.name == active:
            reads.append(path.name)
        return original_read(path, **kwargs)

    monkeypatch.setattr(GenerationStore, "_inspect", inspect)
    monkeypatch.setattr(generation_module, "_read_regular", read)
    with TestClient(_build_streamable_http_app(settings), base_url="http://localhost") as client:
        first = client.get("/healthz")
        count = len(reads)
        second = client.get("/healthz")
    for worker in workers:
        worker.join(timeout=3)
    assert first.status_code == second.status_code == 200
    assert first.json()["generation_id"] == second.json()["generation_id"] == active
    assert count > 0
    assert len(reads) == count
    assert admissions and all(admissions)


@pytest.mark.parametrize("failure", ["deadline", "cancelled"])
def test_failed_pin_cleans_created_directory_after_budget_rejection(
    tmp_path: Path, failure: str
) -> None:
    """An expired request still closes its lease and removes its own empty directory."""
    from policynim.errors import ProviderError
    from policynim.operations import OperationBudget, check_budget, operation_scope

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    active = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    now = [0.0]
    budget = OperationBudget(seconds=1, clock=lambda: now[0])

    def fault(step: str) -> None:
        if step == "inspect":
            if failure == "deadline":
                now[0] = 2.0
            else:
                budget.cancelled.set()
            check_budget()

    rejected = GenerationStore(root=store.root, embedding=identity, fault=fault)
    with pytest.raises(ProviderError), operation_scope(budget):
        rejected.pin()
    assert not (store.root / "leases" / active).exists()
    with store.pin() as recovered:
        assert recovered.generation_id == active


@pytest.mark.parametrize("tamper", ["data_and_owner", "manifest_and_owner", "missing_manifest"])
def test_resumed_trash_cleanup_rejects_self_consistent_owner_tampering(
    tmp_path: Path, tamper: str
) -> None:
    """A mutable owner record can't authenticate replacement recovery bytes by itself."""
    from policynim.services.generation_sources import digest_bytes

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    assert settings.corpus_dir is not None
    policy = settings.corpus_dir / "A.md"
    for suffix in ("second", "third"):
        policy.write_text(policy.read_text() + f"\n{suffix}\n")
        store.build(settings, MockEmbedder())

    def fault(step: str) -> None:
        if step == "trash_remove_catalog.json":
            raise OSError("Injected first-unlink interruption.")

    interrupted = GenerationStore(root=store.root, embedding=identity, fault=fault)
    assert interrupted.prune(keep_recent=0).warnings
    trash = next((store.root / "trash").iterdir())
    assert trash.name.startswith(first)
    artifact = trash / ("catalog.json" if tamper == "data_and_owner" else "manifest.json")
    artifact.write_bytes(artifact.read_bytes() + b" ")
    marker = trash / ".owner"
    record = json.loads(marker.read_bytes())
    record["artifacts"][artifact.name] = digest_bytes(artifact.read_bytes())
    marker.write_text(json.dumps(record))
    if tamper == "missing_manifest":
        artifact.unlink()
    before = {p.name: p.read_bytes() for p in trash.iterdir()}
    assert store.prune(keep_recent=0).warnings
    assert before == {p.name: p.read_bytes() for p in trash.iterdir()}


@pytest.mark.parametrize(
    "boundary", ["trash_remove_runtime_rules.json", "trash_remove_manifest.json", "owner_unlink"]
)
def test_interrupted_trash_retains_manifest_until_last_data_removal(
    tmp_path: Path, boundary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every partial deletion keeps an authenticated manifest and retries to completion."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    assert settings.corpus_dir is not None
    policy = settings.corpus_dir / "A.md"
    for suffix in ("second", "third"):
        policy.write_text(policy.read_text() + f"\n{suffix}\n")
        store.build(settings, MockEmbedder())

    def fault(step: str) -> None:
        if step == boundary:
            raise OSError("Injected partial-unlink interruption.")

    original_unlink = Path.unlink
    interrupted_owner = [False]

    def unlink(path: Path, *args, **kwargs):
        if (
            boundary == "owner_unlink"
            and path.name == ".owner"
            and path.parent.parent == store.root / "trash"
            and not interrupted_owner[0]
        ):
            interrupted_owner[0] = True
            raise OSError("Injected final-owner interruption.")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    interrupted = GenerationStore(root=store.root, embedding=identity, fault=fault)
    assert interrupted.prune(keep_recent=0).warnings
    trash = next((store.root / "trash").iterdir())
    if boundary == "owner_unlink":
        assert {p.name for p in trash.iterdir()} == {".owner"}
    else:
        assert (trash / "manifest.json").is_file()
    assert not store.prune(keep_recent=0).warnings
    assert not list((store.root / "trash").iterdir())


@pytest.mark.parametrize("boundary", ["before_open", "after_open"])
def test_closing_a_store_waits_for_its_inflight_sqlite_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """Real close and prune can't retire a generation while an admitted SQLite read uses it."""
    from threading import Event, Thread, current_thread

    from policynim.services.index_store import create_index_store
    from policynim.storage import sqlite_vec as adapter

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    generations = GenerationStore(root=settings.generations_dir, embedding=identity)
    first = generations.migrate(settings, index_path=index, rules_path=rules).generation_id
    held = create_index_store(settings)
    expected = held.list_chunks()
    entered, release, close_started, closed = Event(), Event(), Event(), Event()
    connect = adapter._connect
    results: list[object] = []
    failures: list[BaseException] = []

    def paused_connect(path: Path, **kwargs):
        """Pause only this actual reader, leaving publishers and inspectors independent."""
        if path != held.path or current_thread() is not reader:
            return connect(path, **kwargs)
        connection = connect(path, **kwargs) if boundary == "after_open" else None
        entered.set()
        try:
            assert release.wait(15)
        except BaseException:
            if connection is not None:
                connection.close()
            raise
        return connection if connection is not None else connect(path, **kwargs)

    def read() -> None:
        """Capture read completion after independent publication and retention."""
        try:
            results.extend(held.list_chunks())
        except BaseException as error:
            failures.append(error)

    def close() -> None:
        """Signal when concurrent cleanup actually releases the adapter's lease."""
        close_started.set()
        try:
            held.close()
            closed.set()
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(adapter, "_connect", paused_connect)
    reader, closer = Thread(target=read), Thread(target=close)
    reader.start()
    try:
        assert entered.wait(3)
        closer.start()
        assert close_started.wait(3)
        assert settings.corpus_dir is not None
        policy = settings.corpus_dir / "A.md"
        for suffix in ("second", "third"):
            policy.write_text(policy.read_text() + f"\n{suffix}\n")
            generations.build(settings, MockEmbedder())
        retained = generations.prune(keep_recent=0)
        assert first in retained.kept
        assert (generations.root / "objects" / first).is_dir()
        assert not closed.is_set()
    finally:
        release.set()
        reader.join(timeout=5)
        if closer.ident is not None:
            closer.join(timeout=5)
        held.close()
    assert not reader.is_alive() and not closer.is_alive()
    assert closed.is_set() and not failures
    assert results == expected
    assert first in generations.prune(keep_recent=0).removed
    assert not (generations.root / "objects" / first).exists()


@pytest.mark.parametrize("location", ["staging", "trash"])
def test_empty_cleanup_directory_recovers_after_the_last_marker_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    """A real interruption at final rmdir leaves no permanent empty recovery folder."""
    from policynim.errors import GenerationError

    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    assert settings.corpus_dir is not None
    policy = settings.corpus_dir / "A.md"
    if location == "trash":
        for suffix in ("second", "third"):
            policy.write_text(policy.read_text() + f"\n{suffix}\n")
            store.build(settings, MockEmbedder())
    before = (store.root / "active.json").read_bytes()
    original_rmdir = Path.rmdir
    interrupted: list[Path] = []

    def rmdir(path: Path) -> None:
        """Fail only the first final empty-directory removal in the selected namespace."""
        if path.parent == store.root / location and not interrupted:
            assert not list(path.iterdir())
            interrupted.append(path)
            raise OSError("Injected final directory removal interruption.")
        original_rmdir(path)

    def fail_prepare(step: str) -> None:
        """Enter real precommit stage cleanup after its known artifacts were created."""
        if step == "index_prepare":
            raise GenerationError("prepare", "Injected preparation failure.")

    monkeypatch.setattr(Path, "rmdir", rmdir)
    if location == "staging":
        failed = GenerationStore(root=store.root, embedding=identity, fault=fail_prepare)
        with pytest.raises(GenerationError):
            failed.build(settings, MockEmbedder())
    else:
        assert store.prune(keep_recent=0).warnings
    assert len(interrupted) == 1
    assert interrupted[0].is_dir() and not list(interrupted[0].iterdir())
    assert not store.prune(keep_recent=0).warnings
    assert not interrupted[0].exists()
    assert (store.root / "active.json").read_bytes() == before


def test_unknown_empty_cleanup_directories_are_preserved(tmp_path: Path) -> None:
    """Empty operator folders don't become owned artifacts just because cleanup runs."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    unknown = [store.root / location / "operator-notes" for location in ("staging", "trash")]
    for directory in unknown:
        directory.mkdir()
    result = store.prune(keep_recent=0)
    assert len(result.warnings) == 2
    assert all(path.is_dir() for path in unknown)


def test_dangling_staging_link_is_preserved_with_a_cleanup_warning(tmp_path: Path) -> None:
    """A dangling entry remains available for inspection and gets a managed-path warning."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.migrate(settings, index_path=index, rules_path=rules)
    link = store.root / "staging" / "dangling"
    try:
        link.symlink_to(tmp_path / "missing-target")
    except (OSError, NotImplementedError):
        pytest.skip("This platform doesn't allow creating the symlink fixture.")
    before = (store.root / "active.json").read_bytes()
    result = store.prune(keep_recent=0)
    assert any(
        "staging/dangling" in item and "Unexpected staging entry" in item
        for item in result.warnings
    )
    assert link.is_symlink()
    assert (store.root / "active.json").read_bytes() == before
