"""Generation, refusal, and recovery proofs shared by external vector backends."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import ValidationError
from test_ingest_service import MockEmbedder
from test_policy_profiles import write_pack

from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store
from policynim.settings import Settings
from policynim.storage.vectors import (
    VectorRecord,
    VectorStorageError,
    float32_vector,
    validate_records,
)
from policynim.types import EmbeddingIdentity

NO_ENV: dict[str, Any] = {"_env_file": None}


class RecordingVectors:
    """Keep complete replicas and inject failure at the external publication boundary."""

    def __init__(self) -> None:
        """Start with no external namespaces or transport failures."""
        self.indexes: dict[str, tuple[str, list[VectorRecord]]] = {}
        self.fail_prepare = False
        self.fail_probe = False
        self.bad_keys = False

    def probe(self) -> None:
        """Model a database authentication failure before paid embedding work."""
        if self.fail_probe:
            raise VectorStorageError()

    def prepare(self, name: str, generation: str, records: Sequence[VectorRecord]) -> None:
        """Retain a partial replica on failure, matching Qdrant's recovery boundary."""
        if self.fail_prepare:
            self.indexes[name] = (generation, list(records[:1]))
            raise VectorStorageError()
        if name not in self.indexes:
            self.indexes[name] = (generation, list(records))
        self.validate(name, generation, records)

    def validate(self, name: str, generation: str, records: Sequence[VectorRecord]) -> None:
        """Validate every vector against the sealed owner instead of trusting counts."""
        if name not in self.indexes or self.indexes[name][0] != generation:
            raise VectorStorageError()
        validate_records(self.indexes[name][1], records)

    def search(
        self, name: str, generation: str, vector: Sequence[float], top_k: int, domain: str | None
    ) -> list[VectorRecord]:
        """Rank canonical test vectors and support an out-of-scope transport response."""
        if self.bad_keys:
            return [
                VectorRecord(key="unowned-key", domain=domain or "backend", vector=list(vector))
            ]
        records = self.indexes[name][1]
        selected = [record for record in records if domain is None or record.domain == domain]
        selected.sort(
            key=lambda record: sum(
                (left - right) ** 2 for left, right in zip(vector, record.vector, strict=True)
            )
        )
        return selected[:top_k]

    def check(self, name: str, generation: str, dimension: int, count: int) -> None:
        """Model compact schema/count inspection independently from complete comparison."""
        if name not in self.indexes:
            raise VectorStorageError()
        owner, records = self.indexes[name]
        if (
            owner != generation
            or len(records) != count
            or any(len(record.vector) != dimension for record in records)
        ):
            raise VectorStorageError()

    def close(self) -> None:
        """The in-memory fixture owns no transport resources."""


def configured(tmp_path: Path) -> tuple[Settings, GenerationStore]:
    """Build settings from explicit sources without personal environment configuration."""
    sources = write_pack(tmp_path / "policies", "test.vector", ["A", "B"])
    settings = Settings(
        **NO_ENV,
        corpus_dir=sources,
        generations_dir=tmp_path / "generations",
        nvidia_embed_model="test-embed",
        nvidia_base_url="https://example.invalid/v1",
        vector_backend="qdrant",
        vector_url="http://127.0.0.1:6333",
        index_db_path=tmp_path / "unused.sqlite3",
        runtime_rules_artifact_path=tmp_path / "unused-rules.json",
    )
    return settings, GenerationStore(
        root=settings.generations_dir,
        embedding=EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        ),
    )


@pytest.mark.parametrize(
    "backend,url",
    [
        ("qdrant", "https://vectors.example.test"),
        ("qdrant", "http://127.0.0.1:6333"),
        ("pgvector", "postgresql://app@127.0.0.1/policy?sslmode=disable"),
        ("pgvector", "postgresql://app@db.example.test/policy?sslmode=verify-full"),
    ],
)
def test_valid_external_settings(backend: Literal["qdrant", "pgvector"], url: str) -> None:
    """Direct settings construction supports explicit backend-specific endpoints."""
    settings = Settings(**NO_ENV, vector_backend=backend, vector_url=url)
    assert settings.vector_url == url


@pytest.mark.parametrize(
    "values",
    [
        {"vector_backend": "unknown"},
        {"vector_backend": "qdrant"},
        {"vector_url": "https://example.test"},
        {"vector_namespace": "Bad/Path"},
        {"vector_namespace": "x" * 17},
        {"vector_timeout_seconds": 0},
        {"vector_backend": "qdrant", "vector_url": "http://example.test"},
        {"vector_backend": "qdrant", "vector_url": "https://user:secret@example.test"},
        {"vector_backend": "qdrant", "vector_url": "https://example.test?api-key=secret"},
        {"vector_backend": "pgvector", "vector_url": "postgresql://app@db.test/p?sslmode=disable"},
        {"vector_backend": "pgvector", "vector_url": "postgresql://app@db.test/p?host=evil"},
    ],
)
def test_invalid_settings_refuse_secret_or_unsafe_endpoints(values: dict) -> None:
    """Misconfiguration fails before database connections or paid provider work."""
    with pytest.raises(ValidationError):
        Settings(**NO_ENV, **values)


def test_external_publish_reader_restart_and_source_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real generation publication and readers use a validated derived replica."""
    from policynim.services import vector_storage

    backend = RecordingVectors()
    monkeypatch.setattr(vector_storage, "create_vector_backend", lambda settings: backend)
    settings, generations = configured(tmp_path)
    receipt = generations.build(settings, MockEmbedder(), sync_vectors=True)
    store = create_index_store(settings)
    try:
        store.validate_identity()
        hits = store.search([1, 5], top_k=2)
        assert hits and all(hit.chunk_id in {r.key for r in store.vector_records()} for hit in hits)
        assert store.generation_id == receipt.generation_id
        assert store.read_policy_chunks(["A"], max_chunks=80, max_source_bytes=262144)
    finally:
        store.close()
    with pytest.raises(Exception, match="closed"):
        store.search([1, 5], top_k=1)
    restarted = create_index_store(settings)
    try:
        restarted.validate_identity()
        assert restarted.generation_id == receipt.generation_id
    finally:
        restarted.close()


def test_failed_external_upload_preserves_prior_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after local sealing can't expose mismatched new sources or rules."""
    from policynim.services import vector_storage

    backend = RecordingVectors()
    monkeypatch.setattr(vector_storage, "create_vector_backend", lambda settings: backend)
    settings, generations = configured(tmp_path)
    first = generations.build(settings, MockEmbedder(), sync_vectors=True)
    before = (generations.root / "active.json").read_bytes()
    assert settings.corpus_dir is not None
    policy = settings.corpus_dir / "A.md"
    policy.write_text(policy.read_text() + "\nReviewed addition.\n")
    backend.fail_prepare = True
    with pytest.raises(VectorStorageError):
        generations.build(settings, MockEmbedder(), sync_vectors=True)
    assert (generations.root / "active.json").read_bytes() == before
    with generations.pin() as snapshot:
        assert snapshot.generation_id == first.generation_id
    assert len(backend.indexes) == 2


def test_probe_failure_precedes_embedding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid database access stops before constructing an embedding provider."""
    from policynim.services import vector_storage

    backend = RecordingVectors()
    backend.fail_probe = True
    monkeypatch.setattr(vector_storage, "create_vector_backend", lambda settings: backend)
    settings, generations = configured(tmp_path)

    def forbidden_factory():
        """Fail if a refused database connection still incurs provider initialization."""
        pytest.fail("Embedding provider mustn't be constructed")

    with pytest.raises(VectorStorageError):
        generations.build(settings, embedder_factory=forbidden_factory, sync_vectors=True)
    assert not (generations.root / "active.json").exists()


def test_tampering_and_unknown_remote_ids_refuse_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote payloads can't replace canonical policy text or source ownership."""
    from policynim.services import vector_storage

    backend = RecordingVectors()
    monkeypatch.setattr(vector_storage, "create_vector_backend", lambda settings: backend)
    settings, generations = configured(tmp_path)
    generations.build(settings, MockEmbedder(), sync_vectors=True)
    store = create_index_store(settings)
    try:
        backend.bad_keys = True
        with pytest.raises(VectorStorageError, match="out-of-scope"):
            store.search([1, 5], top_k=1)
        name = next(iter(backend.indexes))
        generation, records = backend.indexes[name]
        backend.indexes[name] = (generation, records[:-1])
        assert store.inspect_readiness().state == "external_unavailable"
    finally:
        store.close()


@pytest.mark.parametrize("values", [[float("nan"), 1], [float("inf"), 1], [1], [1e100, 1]])
def test_invalid_vectors_are_rejected(values: list[float]) -> None:
    """Dimension and numeric errors don't cross the remote search boundary."""
    with pytest.raises(VectorStorageError):
        float32_vector(values, 2)


def test_external_ingest_requires_explicit_publication_flag(tmp_path: Path) -> None:
    """Merely selecting an external backend doesn't approve database mutation or provider work."""
    from policynim.errors import ConfigurationError

    settings, generations = configured(tmp_path)
    with pytest.raises(ConfigurationError, match="ingest --sync-vectors"):
        generations.build(settings, MockEmbedder())
    assert not generations.root.exists()


def test_health_refuses_missing_external_replica(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settings-backed production health path must agree with the retrieval consumer."""
    from policynim.services import health as health_module
    from policynim.services import vector_storage
    from policynim.services.health import create_runtime_health_service

    backend = RecordingVectors()
    monkeypatch.setattr(vector_storage, "create_vector_backend", lambda settings: backend)
    settings, generations = configured(tmp_path)
    generations.build(settings, MockEmbedder(), sync_vectors=True)
    health = create_runtime_health_service(settings)
    clock = [100.0]
    monkeypatch.setattr(health_module, "monotonic", lambda: clock[0])
    assert health.check().ready
    backend.indexes.clear()
    clock[0] += 2
    result = health.check()
    assert not result.ready
    assert result.reason is not None and "vector sync" in result.reason


def test_malformed_vector_read_becomes_a_controlled_index_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a truncated blob at the real SQLite read/decode boundary, then inspect its error."""
    import struct

    from test_policy_generations import seed_legacy

    from policynim.errors import IndexCompatibilityError
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    _, identity, index, _ = seed_legacy(tmp_path)
    original = struct.unpack

    def truncated_blob(format_string, blob):
        """Model a malformed vector read without changing the retained policy sources."""
        return original(format_string, blob[:-1])

    monkeypatch.setattr(struct, "unpack", truncated_blob)
    with pytest.raises(IndexCompatibilityError):
        SQLiteVecIndexStore(path=index, embedding_identity=identity).vector_records()


def test_runtime_queries_dont_materialize_complete_replicas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual readiness/search reads bounded metadata and only matched canonical vectors."""
    from policynim.services import vector_storage
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    backend = RecordingVectors()
    monkeypatch.setattr(vector_storage, "create_vector_backend", lambda settings: backend)
    settings, generations = configured(tmp_path)
    generations.build(settings, MockEmbedder(), sync_vectors=True)
    original = SQLiteVecIndexStore.vector_records

    def bounded_records(self, keys=None):
        """Reject the complete-vector read that made every query scale with replica size."""
        assert keys is not None, "runtime requested every canonical vector"
        return original(self, keys)

    def no_full_comparison(*args):
        """Explicit full inspection belongs outside the routine query path."""
        pytest.fail("runtime downloaded the full external replica")

    monkeypatch.setattr(SQLiteVecIndexStore, "vector_records", bounded_records)
    monkeypatch.setattr(backend, "validate", no_full_comparison)
    with closing(create_index_store(settings)) as store:
        assert store.inspect_readiness().state == "ready"
        assert store.search([1, 5], top_k=1)


def test_external_health_is_single_flight_cached_and_generation_aware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent public probes share one check; activation and expiry force a fresh result."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from policynim.services import health, vector_storage

    backend = RecordingVectors()
    monkeypatch.setattr(vector_storage, "create_vector_backend", lambda settings: backend)
    settings, generations = configured(tmp_path)
    first_generation = generations.build(settings, MockEmbedder(), sync_vectors=True)
    clock = [100.0]
    monkeypatch.setattr(health, "monotonic", lambda: clock[0], raising=False)
    entered, release = Event(), Event()
    calls: list[str] = []
    original = backend.check

    def wait_for_database(name, generation, dimension, count):
        """Hold one real external-check seam while simultaneous callers arrive."""
        calls.append(generation)
        entered.set()
        assert release.wait(5)
        return original(name, generation, dimension, count)

    monkeypatch.setattr(backend, "check", wait_for_database)
    service = health.create_runtime_health_service(settings)
    with ThreadPoolExecutor(max_workers=12) as executor:
        first = executor.submit(service.check)
        try:
            assert entered.wait(1)
            probes = [executor.submit(service.check) for _ in range(10)]
            with pytest.raises(TimeoutError):
                probes[0].result(timeout=0.1)
            assert calls == [first_generation.generation_id]
        finally:
            release.set()
        assert first.result(timeout=1).ready
        assert all(probe.result(timeout=1).ready for probe in probes)
    assert service.check().ready and len(calls) == 1
    assert settings.corpus_dir is not None
    source = settings.corpus_dir / "A.md"
    source.write_text(source.read_text() + "\nReviewed activation.\n")
    second_generation = generations.build(settings, MockEmbedder(), sync_vectors=True)
    assert service.check().generation_id == second_generation.generation_id
    assert len(calls) == 2
    backend.indexes.clear()
    clock[0] += 2
    assert not service.check().ready
    assert len(calls) == 3


def test_explicit_vector_inspection_still_compares_all_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real CLI rejects same-count replica corruption that metadata checks can't prove."""
    import json

    from typer.testing import CliRunner

    from policynim.interfaces import vector_cli
    from policynim.interfaces.cli import app
    from policynim.services import vector_storage

    backend = RecordingVectors()
    monkeypatch.setattr(vector_storage, "create_vector_backend", lambda settings: backend)
    settings, generations = configured(tmp_path)
    generations.build(settings, MockEmbedder(), sync_vectors=True)
    name = next(iter(backend.indexes))
    owner, records = backend.indexes[name]
    records[0] = records[0].model_copy(update={"vector": [999.0] * len(records[0].vector)})
    backend.indexes[name] = (owner, records)
    with monkeypatch.context() as patch:
        patch.setattr(backend, "search", lambda *args: [records[0]])
        with closing(create_index_store(settings)) as store:
            with pytest.raises(VectorStorageError):
                store.search([1, 5], top_k=1)
    monkeypatch.setattr(vector_cli, "get_settings", lambda: settings)
    result = CliRunner().invoke(app, ["vector", "inspect", "--format", "json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "error"


@pytest.mark.parametrize("path", ["/policy/", "/policy/other", "//policy"])
def test_postgres_rejects_ambiguous_database_path_segments(path: str) -> None:
    """Invalid database URLs fail before attempting an unintended database connection."""
    with pytest.raises(ValidationError):
        Settings(
            **NO_ENV, vector_backend="pgvector", vector_url="postgresql://user@localhost" + path
        )


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_external_health_preserves_generation_recovery_without_error_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    """Actual absent/corrupt generations retain their safe repair action on external health."""
    import logging

    from policynim.services import vector_storage
    from policynim.services.health import create_runtime_health_service

    backend = RecordingVectors()
    monkeypatch.setattr(vector_storage, "create_vector_backend", lambda settings: backend)
    settings, generations = configured(tmp_path)
    if failure == "corrupt":
        receipt = generations.build(settings, MockEmbedder(), sync_vectors=True)
        (generations.root / "objects" / receipt.generation_id / "manifest.json").write_bytes(
            b"damaged"
        )
    caplog.clear()
    service = create_runtime_health_service(settings)
    result = service.check()
    assert not result.ready and result.reason is not None
    recovery = "policynim ingest" if failure == "missing" else "generation inspect"
    assert recovery in result.reason
    assert not [entry for entry in caplog.records if entry.levelno >= logging.ERROR]
    if failure == "missing":
        generations.build(settings, MockEmbedder(), sync_vectors=True)
        assert service.check().ready
