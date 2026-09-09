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
    """Build explicit offline settings for a test embedding identity and index destination."""
    return Settings(
        **NO_ENV,
        index_db_path=path,
        nvidia_embed_model=model,
        nvidia_base_url="https://example.invalid/v1",
        **kwargs,
    )


def chunk() -> EmbeddedChunk:
    """Return one embedded policy chunk in a two-dimensional test space."""
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
        """Initialize the test double's state and wrapped resources."""
        self.calls = 0
        self.closed = False

    def embed_query(self, text):
        """Return a test query vector after performing the case's configured side effect."""
        self.calls += 1
        return [1.0, 0.0]

    def embed_documents(self, texts):
        """Count embedding calls and return fixed two-dimensional vectors for the corpus."""
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]

    def close(self):
        """Record or perform resource cleanup for lifecycle assertions."""
        self.closed = True


def metadata(path):
    """Read the persisted index metadata using an independent SQLite connection."""
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT key, value FROM index_metadata"))


def test_index_persists_model_identity_with_vectors(tmp_path):
    """Verify index persists model identity with vectors."""
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
    """Verify incompatible index blocks query and readiness without embedding."""
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
    """Verify legacy index remains inspectable but cannot be reembedded in place."""
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
    """Verify search rechecks identity after provider returns."""
    path = tmp_path / "index.sqlite3"
    settings = settings_for(path)
    create_index_store(settings).replace([chunk()])
    other = tmp_path / "other.sqlite3"
    create_index_store(settings_for(other, "test/model-b")).replace([chunk()])

    class ReplacingEmbedder(SpyEmbedder):
        def embed_query(self, text):
            """Return a test query vector after performing the case's configured side effect."""
            other.replace(path)
            return super().embed_query(text)

    with pytest.raises(MissingIndexError):
        SearchService(
            embedder=ReplacingEmbedder(), index_store=create_index_store(settings)
        ).search(SearchRequest(query="request identifiers"))


def make_ingest(tmp_path, *, model="test/model-a", filename="index.sqlite3"):
    """Create an isolated corpus, rules destination, and ingest service with a spy embedder."""
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
    """Verify model change rejected before provider call."""
    settings, service, _ = make_ingest(tmp_path)
    service.run()
    before = settings.index_db_path.read_bytes()
    _, changed, embedder = make_ingest(tmp_path, model="test/model-b")
    with pytest.raises(MissingIndexError, match="separate"):
        changed.run()
    assert embedder.calls == 0
    assert settings.index_db_path.read_bytes() == before


def test_rules_finalization_failure_leaves_candidate_unready(tmp_path, monkeypatch):
    """Verify rules finalization failure leaves candidate unready."""
    settings, service, _ = make_ingest(tmp_path)

    def fail(*args):
        """Interrupt the selected boundary to verify preservation and cleanup."""
        raise OSError("injected finalize failure")

    monkeypatch.setattr("policynim.services.ingest._finalize_runtime_rules_artifact", fail)
    with pytest.raises(OSError, match="injected"):
        service.run()
    assert settings.index_db_path.exists()
    assert create_runtime_health_service(settings).check().ready is False
    with pytest.raises(MissingIndexError):
        create_index_store(settings).search([1.0, 0.0], top_k=1)


def test_fresh_migration_cannot_overwrite_existing_rules(tmp_path):
    """Verify fresh migration cannot overwrite existing rules."""
    settings, service, embedder = make_ingest(tmp_path)
    rules = tmp_path / "index.sqlite3.rules.json"
    rules.write_text("preserved prior rules")
    with pytest.raises(MissingIndexError, match="separate"):
        service.run()
    assert embedder.calls == 0
    assert rules.read_text() == "preserved prior rules"
    assert not settings.index_db_path.exists()


def test_startup_never_rebuilds_model_mismatch(tmp_path, monkeypatch):
    """Verify startup never rebuilds model mismatch."""
    path = tmp_path / "index.sqlite3"
    create_index_store(settings_for(path)).replace([chunk()])
    calls = []
    monkeypatch.setattr(
        "policynim.services.health.create_ingest_service", lambda *_: calls.append(1)
    )
    with pytest.raises(Exception):
        ensure_hosted_runtime_ready(settings_for(path, "test/model-b"), rebuild_if_missing=True)
    assert calls == []


@pytest.mark.parametrize("mutation", ["legacy", "incomplete", "model", "endpoint"])
@pytest.mark.parametrize("matched", [False, True])
@pytest.mark.parametrize("consumer", ["decision", "execution"])
def test_runtime_actions_reject_incompatible_index(tmp_path, mutation, matched, consumer):
    """Verify runtime actions reject incompatible index."""
    from policynim.services.runtime_decision import RuntimeDecisionService
    from policynim.services.runtime_execution import RuntimeExecutionService
    from policynim.storage import RuntimeEvidenceStore
    from policynim.types import CompiledRuntimeRule, FileWriteActionRequest, RuntimeRulesArtifact

    settings, ingestion, _ = make_ingest(tmp_path)
    ingestion.run()
    store = create_index_store(settings)
    rules_path = tmp_path / "index.sqlite3.rules.json"
    output = tmp_path / "output.txt"
    if matched:
        indexed = store.list_chunks()[0]
        first_line = int(indexed.lines.split("-")[0])
        artifact = RuntimeRulesArtifact(
            rules=[
                CompiledRuntimeRule(
                    policy_id=indexed.policy.policy_id,
                    title=indexed.policy.title,
                    domain=indexed.policy.domain,
                    source_path=indexed.path,
                    start_line=first_line,
                    end_line=first_line,
                    action="file_write",
                    effect="confirm",
                    reason="Review writes.",
                    path_globs=[str(output)],
                )
            ]
        )
        rules_path.write_text(artifact.model_dump_json())
    if mutation in {"legacy", "incomplete"}:
        key, value = (
            ("schema_version", "1") if mutation == "legacy" else ("ingest_complete", "false")
        )
        with sqlite3.connect(settings.index_db_path) as conn:
            conn.execute("UPDATE index_metadata SET value=? WHERE key=?", (value, key))
    else:
        settings = settings.model_copy(
            update={
                "nvidia_embed_model" if mutation == "model" else "nvidia_base_url": "test/other"
                if mutation == "model"
                else "https://other.invalid/v1"
            }
        )
    decisions = RuntimeDecisionService(
        index_store=create_index_store(settings), runtime_rules_artifact_path=rules_path
    )
    request = FileWriteActionRequest(
        kind="file_write",
        task="Update logging",
        cwd=tmp_path,
        path=output,
        content="must not be written",
    )
    if consumer == "decision":
        with decisions, pytest.raises(MissingIndexError):
            decisions.decide(request)
    else:
        evidence = RuntimeEvidenceStore(path=tmp_path / "evidence.sqlite3")
        with RuntimeExecutionService(
            decision_service=decisions, evidence_store=evidence, confirmer=lambda _: True
        ) as executor:
            with pytest.raises(MissingIndexError):
                executor.execute(request)
    assert not output.exists()
