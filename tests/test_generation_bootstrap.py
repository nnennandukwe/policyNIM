"""Conditional hosted seeding through the real generation publication boundary."""

from __future__ import annotations

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
        settings = settings.model_copy(update={"corpus_dir": tmp_path / "missing-sources"})
    with pytest.raises(GenerationError):
        store.bootstrap_bundle(
            first[0],
            expected_digest="0" * 64 if failure == "checksum" else first[1],
            source_settings=settings,
        )
    assert not store.root.exists()


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
