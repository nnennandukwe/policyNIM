"""Generation, refusal, and recovery proofs shared by external vector backends."""

from __future__ import annotations

from collections.abc import Sequence
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
    ) -> list[str]:
        """Rank canonical test vectors and support an out-of-scope transport response."""
        if self.bad_keys:
            return ["unowned-key"]
        records = self.indexes[name][1]
        selected = [record for record in records if domain is None or record.domain == domain]
        selected.sort(
            key=lambda record: sum(
                (left - right) ** 2 for left, right in zip(vector, record.vector, strict=True)
            )
        )
        return [record.key for record in selected[:top_k]]

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
