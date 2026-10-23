"""Opt-in real Qdrant/Postgres lifecycle checks using synthetic vectors and no model calls."""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from test_ingest_service import MockEmbedder
from test_vector_storage import configured

from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store
from policynim.services.vector_storage import create_vector_backend
from policynim.settings import Settings
from policynim.storage.vectors import VectorRecord, VectorStorageError, vector_namespace

pytestmark = pytest.mark.docker_live


@pytest.fixture(params=["qdrant", "pgvector"])
def live_settings(request, tmp_path: Path) -> tuple[Settings, GenerationStore]:
    """Require explicit dedicated test endpoints before any external write."""
    if os.environ.get("POLICYNIM_RUN_VECTOR_LIVE") != "1":
        pytest.skip("Set POLICYNIM_RUN_VECTOR_LIVE=1 and dedicated backend test URLs.")
    backend: Literal["qdrant", "pgvector"] = request.param
    url = os.environ.get("POLICYNIM_VECTOR_TEST_" + backend.upper() + "_URL")
    if not url:
        pytest.skip("No dedicated test URL for " + backend)
    base, generations = configured(tmp_path)
    values = base.model_dump()
    values.update(
        {
            "vector_backend": backend,
            "vector_url": url,
            "vector_namespace": "fixture",
            "vector_password": os.environ.get("POLICYNIM_VECTOR_TEST_PASSWORD", ""),
        }
    )
    settings = Settings.model_validate(values)
    return settings, generations


def test_real_publication_restart_filtering_collision_and_rollback(live_settings) -> None:
    """Real stores preserve canonical source identity and retained-generation recovery."""
    settings, generations = live_settings
    assert settings.corpus_dir is not None
    source = settings.corpus_dir / "B.md"
    source.write_text(source.read_text().replace("domain: backend", "domain: data"))
    first = generations.build(settings, MockEmbedder(), sync_vectors=True)
    with closing(create_index_store(settings)) as store:
        store.validate_identity()
        records = store.vector_records()
        query = records[0].vector
        hits = store.search(query, top_k=5)
        assert hits[0].chunk_id == records[0].key
        filtered = store.search(query, top_k=5, domain="data")
        assert filtered and all(hit.policy.domain == "data" for hit in filtered)
        assert store.search(query, top_k=5, domain="absent") == []
        assert store.read_policy_chunks(["A"], max_chunks=80, max_source_bytes=262144)
    from test_extractive_guidance import ExactCompiler, PreserveRanking, SourceGenerator

    from policynim.services.compiler import PolicyCompilerService
    from policynim.services.preflight import PreflightService
    from policynim.services.router import PolicyRouterService
    from policynim.services.runtime_decision import create_runtime_decision_service
    from policynim.types import PreflightRequest, ShellCommandActionRequest

    with PreflightService(
        compiler_service=PolicyCompilerService(
            router=PolicyRouterService(
                embedder=MockEmbedder(),
                index_store=create_index_store(settings),
                reranker=PreserveRanking(),
            ),
            compiler=ExactCompiler(),
        ),
        generator=SourceGenerator(),
    ) as guidance:
        answer = guidance.preflight(PreflightRequest(task="Review deployment", top_k=2))
        assert not answer.insufficient_context and answer.citations
        assert answer.generation_id == first.generation_id
        assert answer.implementation_guidance == []
    with create_runtime_decision_service(settings) as runtime:
        decision = runtime.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Review fixture action",
                command=["A"],
                cwd=settings.corpus_dir,
            )
        )
        assert decision.decision == "block" and decision.citations
        assert decision.generation_id == first.generation_id
    name = vector_namespace(settings.vector_namespace, first.generation_id)
    with closing(create_vector_backend(settings)) as backend:
        backend.prepare(name, first.generation_id, records)
        poisoned = list(records)
        poisoned[0] = VectorRecord(key=records[0].key, domain="wrong", vector=query)
        with pytest.raises(VectorStorageError):
            backend.prepare(name, first.generation_id, poisoned)
        backend.validate(name, first.generation_id, records)
    source.write_text(source.read_text() + "\nA reviewed new rule.\n")
    second = generations.build(settings, MockEmbedder(), sync_vectors=True)
    assert second.generation_id != first.generation_id
    generations.rollback(expected_active=second.generation_id)
    with closing(create_index_store(settings)) as restarted:
        restarted.validate_identity()
        assert restarted.generation_id == first.generation_id
        assert restarted.search(query, top_k=1)[0].chunk_id == records[0].key
    bundle = settings.generations_dir.parent / "recovery.zip"
    checksum = generations.export_bundle(bundle)
    recovered_root = settings.generations_dir.parent / "restored-generations"
    recovered = GenerationStore(root=recovered_root, embedding=generations.embedding)
    recovered.import_bundle(bundle, expected_digest=checksum)
    with closing(
        create_index_store(settings.model_copy(update={"generations_dir": recovered_root}))
    ) as restored:
        restored.validate_identity()
        assert restored.generation_id == first.generation_id


def test_real_qdrant_interrupted_upload_preserves_prior_pointer(
    live_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty collection after transport interruption stays unadopted and inspectable."""
    settings, generations = live_settings
    if settings.vector_backend != "qdrant":
        pytest.skip("Qdrant-specific nontransactional upload boundary")
    from policynim.storage.qdrant_vectors import QdrantVectors

    generations.build(settings, MockEmbedder(), sync_vectors=True)
    before = (generations.root / "active.json").read_bytes()
    assert settings.corpus_dir is not None
    source = settings.corpus_dir / "A.md"
    source.write_text(source.read_text() + "\nSecond revision.\n")
    original = QdrantVectors._request

    def interrupt(self, method, path, payload=None):
        """Fail after namespace creation and before its first vector batch."""
        if method == "PUT" and "/points" in path:
            raise VectorStorageError("Fixture upload interrupted.")
        return original(self, method, path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(QdrantVectors, "_request", interrupt)
        with pytest.raises(VectorStorageError):
            generations.build(settings, MockEmbedder(), sync_vectors=True)
    assert (generations.root / "active.json").read_bytes() == before
    with closing(create_index_store(settings)) as prior:
        prior.validate_identity()


def test_real_postgres_failed_validation_rolls_back_table(
    live_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validation failure after inserts rolls back its fresh table and preserves activation."""
    settings, generations = live_settings
    if settings.vector_backend != "pgvector":
        pytest.skip("Postgres-specific transactional publication boundary")
    from policynim.storage.pgvector_vectors import PGVectorVectors

    generations.build(settings, MockEmbedder(), sync_vectors=True)
    before = (generations.root / "active.json").read_bytes()
    assert settings.corpus_dir is not None
    source = settings.corpus_dir / "A.md"
    source.write_text(source.read_text() + "\nSecond revision.\n")
    created: list[str] = []

    def fail_validation(self, connection, name, generation, records):
        """Abort within the transaction after the producer has inserted all records."""
        created.append(name)
        raise VectorStorageError("Fixture validation failed.")

    with monkeypatch.context() as patch:
        patch.setattr(PGVectorVectors, "_validate", fail_validation)
        with pytest.raises(VectorStorageError):
            generations.build(settings, MockEmbedder(), sync_vectors=True)
    assert (generations.root / "active.json").read_bytes() == before
    import importlib

    driver = importlib.import_module("psycopg")
    with closing(create_vector_backend(settings)) as backend:
        assert isinstance(backend, PGVectorVectors)
        with driver.connect(**backend._options) as connection:
            for name in created:
                assert (
                    connection.execute("SELECT to_regclass(%s)", ("public." + name,)).fetchone()[0]
                    is None
                )
