"""Real SQLite tests for model compatibility and source-preserving migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from policynim.errors import MissingIndexError
from policynim.services.health import create_runtime_health_service, ensure_hosted_runtime_ready
from policynim.services.ingest import IngestService
from policynim.services.search import SearchService
from policynim.settings import Settings
from policynim.storage import create_index_store
from policynim.types import EmbeddedChunk, PolicyMetadata, SearchRequest

NO_ENV: dict[str, Any] = {"_env_file": None}


def settings_for(path: Path, model: str = "test/model-a", **kwargs) -> Settings:
    return Settings(
        **NO_ENV,
        index_db_path=path,
        nvidia_embed_model=model,
        nvidia_base_url="https://example.invalid/v1",
        **kwargs,
    )


def chunk() -> EmbeddedChunk:
    return EmbeddedChunk(
        chunk_id="P:rules",
        path="policies/p.md",
        section="Rules",
        lines="1-3",
        text="Include request identifiers.",
        vector=[1.0, 0.0],
        policy=PolicyMetadata(
            policy_id="P", title="Logging", doc_type="guidance", domain="backend"
        ),
    )


class SpyEmbedder:
    def __init__(self):
        self.calls = 0
        self.closed = False

    def embed_query(self, text):
        self.calls += 1
        return [1.0, 0.0]

    def embed_documents(self, texts):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]

    def close(self):
        self.closed = True


def metadata(path):
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT key, value FROM index_metadata"))


def test_index_persists_model_identity_with_vectors(tmp_path):
    settings = settings_for(tmp_path / "index.sqlite3")
    create_index_store(settings).replace([chunk()])
    stored = metadata(settings.index_db_path)
    assert stored["schema_version"] == "2"
    assert stored["embedding_model"] == "test/model-a"
    assert stored["embedding_provider"] == "nvidia"
    assert stored["embedding_endpoint"] == "https://example.invalid/v1"
    assert stored["embedding_dimension"] == "2"
    assert [c.chunk_id for c in create_index_store(settings).search([1.0, 0.0], top_k=1)] == [
        "P:rules"
    ]


@pytest.mark.parametrize(
    "mutation",
    ["same_dimension_model", "endpoint", "legacy", "missing_model", "dimension", "incomplete"],
)
def test_incompatible_index_blocks_query_and_readiness_without_embedding(tmp_path, mutation):
    path = tmp_path / "index.sqlite3"
    settings = settings_for(path)
    create_index_store(settings).replace([chunk()])
    if mutation == "same_dimension_model":
        settings = settings_for(path, "test/model-b")
    elif mutation == "endpoint":
        settings = settings.model_copy(update={"nvidia_base_url": "https://different.invalid/v1"})
    else:
        with sqlite3.connect(path) as conn:
            if mutation == "missing_model":
                conn.execute("DELETE FROM index_metadata WHERE key='embedding_model'")
            else:
                key, value = {
                    "legacy": ("schema_version", "1"),
                    "dimension": ("embedding_dimension", "3"),
                    "incomplete": ("ingest_complete", "false"),
                }[mutation]
                conn.execute("INSERT OR REPLACE INTO index_metadata VALUES (?, ?)", (key, value))
    before = path.read_bytes()
    embedder = SpyEmbedder()
    service = SearchService(embedder=embedder, index_store=create_index_store(settings))
    with pytest.raises(MissingIndexError):
        service.search(SearchRequest(query="request identifiers"))
    assert embedder.calls == 0
    assert create_runtime_health_service(settings).check().ready is False
    assert path.read_bytes() == before


def test_legacy_index_remains_inspectable_but_cannot_be_reembedded_in_place(tmp_path):
    settings = settings_for(tmp_path / "index.sqlite3")
    store = create_index_store(settings)
    store.replace([chunk()])
    with sqlite3.connect(settings.index_db_path) as conn:
        conn.execute("UPDATE index_metadata SET value='1' WHERE key='schema_version'")
    before = settings.index_db_path.read_bytes()
    assert [c.chunk_id for c in store.list_chunks()] == ["P:rules"]
    with pytest.raises(MissingIndexError, match="separate"):
        store.replace([chunk()])
    assert settings.index_db_path.read_bytes() == before


def test_search_rechecks_identity_after_provider_returns(tmp_path):
    path = tmp_path / "index.sqlite3"
    settings = settings_for(path)
    create_index_store(settings).replace([chunk()])
    other = tmp_path / "other.sqlite3"
    create_index_store(settings_for(other, "test/model-b")).replace([chunk()])

    class ReplacingEmbedder(SpyEmbedder):
        def embed_query(self, text):
            other.replace(path)
            return super().embed_query(text)

    with pytest.raises(MissingIndexError):
        SearchService(
            embedder=ReplacingEmbedder(), index_store=create_index_store(settings)
        ).search(SearchRequest(query="request identifiers"))


def make_ingest(tmp_path, *, model="test/model-a", filename="index.sqlite3"):
    corpus = tmp_path / "policies"
    corpus.mkdir(exist_ok=True)
    (corpus / "logging.md").write_text("# Logging\n\n## Rules\n\nInclude request identifiers.\n")
    settings = settings_for(tmp_path / filename, model)
    embedder = SpyEmbedder()
    service = IngestService(
        embedder=embedder,
        index_store=create_index_store(settings),
        corpus_root=corpus,
        embedding_model=model,
        runtime_rules_artifact_path=tmp_path / f"{filename}.rules.json",
    )
    return settings, service, embedder


def test_model_change_rejected_before_provider_call(tmp_path):
    settings, service, _ = make_ingest(tmp_path)
    service.run()
    before = settings.index_db_path.read_bytes()
    _, changed, embedder = make_ingest(tmp_path, model="test/model-b")
    with pytest.raises(MissingIndexError, match="separate"):
        changed.run()
    assert embedder.calls == 0
    assert settings.index_db_path.read_bytes() == before


def test_rules_finalization_failure_leaves_candidate_unready(tmp_path, monkeypatch):
    settings, service, _ = make_ingest(tmp_path)

    def fail(*args):
        raise OSError("injected finalize failure")

    monkeypatch.setattr("policynim.services.ingest._finalize_runtime_rules_artifact", fail)
    with pytest.raises(OSError, match="injected"):
        service.run()
    assert settings.index_db_path.exists()
    assert create_runtime_health_service(settings).check().ready is False
    with pytest.raises(MissingIndexError):
        create_index_store(settings).search([1.0, 0.0], top_k=1)


def test_fresh_migration_cannot_overwrite_existing_rules(tmp_path):
    settings, service, embedder = make_ingest(tmp_path)
    rules = tmp_path / "index.sqlite3.rules.json"
    rules.write_text("preserved prior rules")
    with pytest.raises(MissingIndexError, match="separate"):
        service.run()
    assert embedder.calls == 0
    assert rules.read_text() == "preserved prior rules"
    assert not settings.index_db_path.exists()


def test_startup_never_rebuilds_model_mismatch(tmp_path, monkeypatch):
    path = tmp_path / "index.sqlite3"
    create_index_store(settings_for(path)).replace([chunk()])
    calls = []
    monkeypatch.setattr(
        "policynim.services.health.create_ingest_service", lambda *_: calls.append(1)
    )
    with pytest.raises(Exception):
        ensure_hosted_runtime_ready(settings_for(path, "test/model-b"), rebuild_if_missing=True)
    assert calls == []
