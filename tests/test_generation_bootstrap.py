"""Conditional hosted seeding through the real generation publication boundary."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from test_ingest_service import MockEmbedder
from test_policy_generations import seed_legacy

from policynim.errors import GenerationError
from policynim.services.generations import GenerationStore


def bundles(tmp_path: Path):
    """Export two independently validated generations without provider requests."""
    settings, identity, index, rules = seed_legacy(tmp_path)
    source = GenerationStore(root=tmp_path / "producer", embedding=identity)
    first = source.migrate(settings, index_path=index, rules_path=rules)
    first_bundle = tmp_path / "first.zip"
    first_digest = source.export_bundle(first_bundle)
    second = source.build(settings, MockEmbedder())
    second_bundle = tmp_path / "second.zip"
    second_digest = source.export_bundle(second_bundle)
    assert first.generation_id != second.generation_id
    target = GenerationStore(root=tmp_path / "hosted" / "generations", embedding=identity)
    return (
        settings,
        target,
        (first_bundle, first_digest, first),
        (second_bundle, second_digest, second),
    )


def test_bootstrap_seeds_absent_root_then_preserves_explicit_selection(tmp_path: Path) -> None:
    """Restart can't replace a valid selected generation or prune its historical neighbors."""
    settings, store, first, second = bundles(tmp_path)
    result = store.bootstrap_bundle(first[0], expected_digest=first[1], source_settings=settings)
    assert result.generation_id == first[2].generation_id
    store.import_bundle(second[0], expected_digest=second[1])
    store.activate(first[2].generation_id, expected_active=second[2].generation_id)
    pointer = (store.root / "active.json").read_bytes()
    retained = {
        path.relative_to(store.root): path.read_bytes()
        for path in (store.root / "objects").rglob("*")
        if path.is_file()
    }
    # A new image's missing or changed seed/sources don't control an existing selection.
    changed_settings = settings.model_copy(update={"corpus_dir": tmp_path / "missing-sources"})
    restarted = store.bootstrap_bundle(
        tmp_path / "missing-seed.zip", expected_digest="0" * 64, source_settings=changed_settings
    )
    assert restarted.generation_id == first[2].generation_id
    assert (store.root / "active.json").read_bytes() == pointer
    assert retained == {path: (store.root / path).read_bytes() for path in retained}
    with store.pin(second[2].generation_id) as snapshot:
        assert snapshot.manifest.chunk_count > 0


@pytest.mark.parametrize("failure", ["checksum", "source", "malformed"])
def test_invalid_fresh_seed_never_creates_target(tmp_path: Path, failure: str) -> None:
    """Seed trust and packaged source agreement precede any root mutation."""
    settings, store, first, _ = bundles(tmp_path)
    if failure == "malformed":
        import hashlib

        first[0].write_bytes(b"not an archive")
        first = (first[0], hashlib.sha256(first[0].read_bytes()).hexdigest(), first[2])
    if failure == "source":
        assert settings.corpus_dir is not None
        member = settings.corpus_dir / "A.md"
        member.write_text(member.read_text() + "\nReviewed changed source.\n")
    with pytest.raises(
        GenerationError, match="Bundle sources don't match" if failure == "source" else None
    ):
        store.bootstrap_bundle(
            first[0],
            expected_digest="0" * 64 if failure == "checksum" else first[1],
            source_settings=settings,
        )
    assert not store.root.exists()


@pytest.mark.parametrize("failure", ["manifest", "digest", "rules", "index"])
def test_invalid_seed_artifacts_leave_no_root_and_allow_corrected_seed(
    tmp_path: Path, failure: str
) -> None:
    """Full seed validation precedes ownership, including rehashed invalid payloads."""
    _, store, first, _ = bundles(tmp_path)
    with zipfile.ZipFile(first[0]) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(contents["manifest.json"])
    if failure == "manifest":
        manifest["index_schema_version"] = "unsupported"
    else:
        member = "index.sqlite3" if failure == "index" else "runtime_rules.json"
        contents[member] = b"invalid artifact"
        if failure != "digest":
            manifest["artifacts"][member] = "sha256:" + hashlib.sha256(contents[member]).hexdigest()
    contents["manifest.json"] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    invalid = tmp_path / "invalid.zip"
    invalid.write_bytes(output.getvalue())
    with pytest.raises(GenerationError, match="couldn't be validated"):
        store.bootstrap_bundle(
            invalid, expected_digest="sha256:" + hashlib.sha256(invalid.read_bytes()).hexdigest()
        )
    assert not store.root.exists()
    assert (
        store.bootstrap_bundle(first[0], expected_digest=first[1]).generation_id
        == first[2].generation_id
    )


def test_seed_validation_uses_target_storage_when_system_temp_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A usable target volume doesn't depend on separate system temporary storage."""
    import tempfile

    _, store, first, _ = bundles(tmp_path)
    blocked = tmp_path / "system-temp-is-a-file"
    blocked.write_bytes(b"preserve")
    monkeypatch.setattr(tempfile, "tempdir", str(blocked))
    result = store.bootstrap_bundle(first[0], expected_digest=first[1])
    assert result.generation_id == first[2].generation_id
    assert blocked.read_bytes() == b"preserve"
    assert not list(tmp_path.glob("policynim-seed-validation-*"))


@pytest.mark.parametrize(
    "kind", ["empty", "unowned", "incomplete", "damaged", "artifact", "embedding", "symlink"]
)
def test_bootstrap_refuses_existing_recovery_state(tmp_path: Path, kind: str) -> None:
    """Startup doesn't adopt, repair, or replace existing operator recovery data."""
    _, store, first, _ = bundles(tmp_path)
    store.root.parent.mkdir(parents=True)
    if kind == "symlink":
        neighbor = tmp_path / "neighbor"
        neighbor.mkdir()
        try:
            store.root.symlink_to(neighbor, target_is_directory=True)
        except OSError:
            pytest.skip("Directory symlinks aren't available to this Windows account.")
    elif kind in {"incomplete", "damaged", "artifact", "embedding"}:
        store.import_bundle(first[0], expected_digest=first[1])
        if kind == "incomplete":
            (store.root / "active.json").unlink()
        elif kind == "artifact":
            artifact = store.root / "objects" / first[2].generation_id / "runtime_rules.json"
            artifact.write_bytes(artifact.read_bytes() + b"damaged")
        elif kind == "embedding":
            store = GenerationStore(
                root=store.root, embedding=store.embedding.model_copy(update={"model": "other"})
            )
        else:
            (store.root / "active.json").write_bytes(b"damaged-pointer")
    else:
        store.root.mkdir()
        if kind == "unowned":
            (store.root / "operator-note").write_bytes(b"preserve")
    before = {
        path.relative_to(store.root): path.read_bytes()
        for path in store.root.rglob("*")
        if path.is_file()
    }
    with pytest.raises(GenerationError):
        store.bootstrap_bundle(first[0], expected_digest=first[1])
    assert before == {path: (store.root / path).read_bytes() for path in before}
    assert store.root.is_symlink() == (kind == "symlink")


def test_bootstrap_preserves_concurrent_supported_publisher(tmp_path: Path) -> None:
    """A publisher that wins before writer admission retains its validated selection."""
    _, target, first, second = bundles(tmp_path)
    published = []

    def fault(step: str) -> None:
        """Commit an actual supported publication before bootstrap obtains writer ownership."""
        if step == "bootstrap_before_writer":
            other = GenerationStore(root=target.root, embedding=target.embedding)
            published.append(other.import_bundle(second[0], expected_digest=second[1]))

    store = GenerationStore(root=target.root, embedding=target.embedding, fault=fault)
    result = store.bootstrap_bundle(first[0], expected_digest=first[1])
    assert len(published) == 1
    assert result.generation_id == second[2].generation_id
    assert store.inspect().generation_id == second[2].generation_id


def test_bootstrap_rejects_replaced_fresh_directory_before_ownership(tmp_path: Path) -> None:
    """The fresh-root claim can't authorize adopting a replacement directory."""
    _, target, first, _ = bundles(tmp_path)
    preserved = tmp_path / "claimed-original"

    def fault(step: str) -> None:
        """Replace the just-created root with unrelated recovery data."""
        if step == "bootstrap_before_writer":
            target.root.rename(preserved)
            target.root.mkdir()
            (target.root / "operator-note").write_bytes(b"preserve")

    store = GenerationStore(root=target.root, embedding=target.embedding, fault=fault)
    with pytest.raises(GenerationError, match="identity changed"):
        store.bootstrap_bundle(first[0], expected_digest=first[1])
    assert {path.name for path in target.root.iterdir()} == {"operator-note"}
    assert preserved.is_dir()


@pytest.mark.parametrize("boundary", ["before_activate", "after_activate"])
def test_bootstrap_preserves_pre_and_post_commit_recovery(tmp_path: Path, boundary: str) -> None:
    """An interrupted first seed never turns a later startup into an implicit retry."""
    _, target, first, second = bundles(tmp_path)

    def fault(step: str) -> None:
        """Interrupt only the named real publication boundary."""
        if step == boundary:
            raise RuntimeError("injected bootstrap interruption")

    store = GenerationStore(root=target.root, embedding=target.embedding, fault=fault)
    if boundary == "before_activate":
        with pytest.raises(GenerationError):
            store.bootstrap_bundle(first[0], expected_digest=first[1])
        with pytest.raises(GenerationError, match="no active"):
            target.bootstrap_bundle(second[0], expected_digest=second[1])
        assert not (target.root / "active.json").exists()
    else:
        result = store.bootstrap_bundle(first[0], expected_digest=first[1])
        assert result.warnings
        pointer = (target.root / "active.json").read_bytes()
        restarted = target.bootstrap_bundle(second[0], expected_digest=second[1])
        assert restarted.generation_id == first[2].generation_id
        assert (target.root / "active.json").read_bytes() == pointer


@pytest.mark.parametrize(
    "boundary", ["bootstrap_root_created", "owner_write", "bundle_snapshot.json_write"]
)
def test_failed_fresh_claim_requires_explicit_recovery(tmp_path: Path, boundary: str) -> None:
    """A failed first seed leaves recovery state that another startup can't silently resume."""
    _, target, first, _ = bundles(tmp_path)

    def fault(step: str) -> None:
        """Stop the actual initializer or artifact writer before its first activation."""
        if step == boundary:
            raise RuntimeError("injected fresh-claim interruption")

    store = GenerationStore(root=target.root, embedding=target.embedding, fault=fault)
    with pytest.raises(GenerationError):
        store.bootstrap_bundle(first[0], expected_digest=first[1])
    assert target.root.exists()
    assert not (target.root / "active.json").exists()
    with pytest.raises(GenerationError):
        target.bootstrap_bundle(first[0], expected_digest=first[1])


@pytest.mark.parametrize("matches", [False, True])
def test_fresh_configured_seed_identity_is_checked_before_root_creation(
    tmp_path: Path, matches: bool
) -> None:
    """A fresh configured selection can't activate a different seed before startup refuses."""
    settings, store, first, second = bundles(tmp_path)
    expected = first[2].generation_id if matches else second[2].generation_id
    if not matches:
        with pytest.raises(GenerationError, match="Seed generation doesn't match"):
            store.bootstrap_bundle(
                first[0],
                expected_digest=first[1],
                source_settings=settings,
                expected_seed_id=expected,
            )
        assert not store.root.exists()
    else:
        result = store.bootstrap_bundle(
            first[0],
            expected_digest=first[1],
            source_settings=settings,
            expected_seed_id=expected,
        )
        assert result.generation_id == expected
        pointer = (store.root / "active.json").read_bytes()
        preserved = store.bootstrap_bundle(
            second[0], expected_digest=second[1], expected_seed_id=second[2].generation_id
        )
        assert preserved.generation_id == first[2].generation_id
        assert (store.root / "active.json").read_bytes() == pointer


def seed_child(store, first, tmp_path: Path, *, boundary: str, name: str, pause: bool = True):
    """Pause a real independent bootstrap process at an observable publication boundary."""
    import subprocess
    import sys
    import time

    ready = tmp_path / (name + "-ready")
    release = tmp_path / (name + "-release")
    script = '''
import sys, time
from pathlib import Path
from policynim.services.generations import GenerationStore
from policynim.types import EmbeddingIdentity
root, bundle, digest, identity, boundary, ready, release, pause = sys.argv[1:]
def fault(step):
    """Hold the real store operation until the parent releases or kills this process."""
    if step == boundary:
        Path(ready).write_text("ready")
        if pause == "no":
            return
        deadline = time.monotonic() + 30
        while not Path(release).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("parent didn't release inspection")
            time.sleep(.01)
store = GenerationStore(
    root=Path(root), embedding=EmbeddingIdentity.model_validate_json(identity), fault=fault
)
store.bootstrap_bundle(Path(bundle), expected_digest=digest)
'''
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(store.root),
            str(first[0]),
            first[1],
            store.embedding.model_dump_json(),
            boundary,
            str(ready),
            str(release),
            "yes" if pause else "no",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    if not ready.exists():
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
        pytest.fail(f"Bootstrap child didn't reach its pause: {stdout} {stderr}")
    return process, release


@pytest.mark.parametrize("owned", [False, True])
def test_killed_seed_inspection_reclaims_only_owned_scratch(tmp_path: Path, owned: bool) -> None:
    """Hard termination can't grow scratch repeatedly or authorize deleting unknown residue."""
    _, store, first, _ = bundles(tmp_path)
    process, _ = seed_child(
        store,
        first,
        tmp_path,
        boundary="bootstrap_seed_inspect" if owned else "bootstrap_seed_created",
        name="killed",
    )
    process.kill()
    process.communicate(timeout=5)
    scratch = [p for p in store.root.parent.glob(".policynim-seed-*") if p.is_dir()]
    assert len(scratch) == 1
    assert not store.root.exists()
    before = {path.name: path.read_bytes() for path in scratch[0].iterdir()}
    if owned:
        result = store.bootstrap_bundle(first[0], expected_digest=first[1])
        assert result.generation_id == first[2].generation_id
        assert not scratch[0].exists()
    else:
        for _ in range(2):
            with pytest.raises(GenerationError):
                store.bootstrap_bundle(first[0], expected_digest=first[1])
            assert not store.root.exists()
            assert before == {path.name: path.read_bytes() for path in scratch[0].iterdir()}
        assert len([p for p in store.root.parent.glob(".policynim-seed-*") if p.is_dir()]) == 1


def test_concurrent_seed_inspectors_preserve_live_scratch_and_selection(tmp_path: Path) -> None:
    """A second real inspector waits for its target lock and preserves the committed winner."""
    import time

    _, store, first, _ = bundles(tmp_path)
    first_process, release = seed_child(
        store, first, tmp_path, boundary="bootstrap_seed_inspect", name="live"
    )
    second, _ = seed_child(
        store, first, tmp_path, boundary="bootstrap_seed_lock", name="second", pause=False
    )
    try:
        scratch = [p for p in store.root.parent.glob(".policynim-seed-*") if p.is_dir()]
        before = {path.name: path.read_bytes() for path in scratch[0].iterdir()}
        time.sleep(0.15)
        assert second.poll() is None
        assert before == {path.name: path.read_bytes() for path in scratch[0].iterdir()}
        assert not store.root.exists()
        release.write_text("continue")
        first_output = first_process.communicate(timeout=15)
        second_output = second.communicate(timeout=15)
        assert first_process.returncode == 0, first_output
        assert second.returncode == 0, second_output
        assert store.inspect().generation_id == first[2].generation_id
        assert not scratch[0].exists()
    finally:
        for process in (first_process, second):
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_retry_syncs_preexisting_parent_entries_before_claiming_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory created before an interrupted sync still needs the next durability barrier."""
    import policynim.services.generations as module

    _, target, first, _ = bundles(tmp_path)
    sync = module._sync_directory
    required = target.root.parent.parent

    def interrupted(path: Path) -> None:
        """Fail the first ancestor-entry sync after the missing parents were created."""
        if path == required:
            raise OSError("injected ancestor sync failure")
        sync(path)

    monkeypatch.setattr(module, "_sync_directory", interrupted)
    with pytest.raises(GenerationError):
        target.bootstrap_bundle(first[0], expected_digest=first[1])
    assert target.root.parent.is_dir()
    assert not target.root.exists()
    observed = []

    def recorded(path: Path) -> None:
        """Retain actual directory sync calls during a supported bootstrap retry."""
        observed.append(path)
        sync(path)

    def fault(step: str) -> None:
        """Require ancestor durability before the retry claims its new generation root."""
        if step == "bootstrap_root_created":
            assert required in observed

    monkeypatch.setattr(module, "_sync_directory", recorded)
    retry = GenerationStore(root=target.root, embedding=target.embedding, fault=fault)
    assert (
        retry.bootstrap_bundle(first[0], expected_digest=first[1]).generation_id
        == first[2].generation_id
    )
