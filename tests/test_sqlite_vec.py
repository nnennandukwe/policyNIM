"""Contract tests for the planned sqlite-vec local index backend."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from textwrap import dedent

import pytest

from policynim.errors import MissingIndexError
from policynim.types import EmbeddedChunk, PolicyMetadata, SearchRequest


def test_sqlite_vec_store_replaces_and_searches_chunks(tmp_path: Path) -> None:
    """Store, count, list, and search embedded chunks from one SQLite file."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    store = SQLiteVecIndexStore(path=tmp_path / "index.sqlite3")
    chunks = [
        make_chunk("BACKEND-1", domain="backend", vector=[1.0, 0.0]),
        make_chunk("SECURITY-1", domain="security", vector=[0.0, 1.0]),
    ]

    store.replace(chunks)

    assert store.path == tmp_path / "index.sqlite3"
    assert store.exists() is True
    assert store.count() == 2
    assert [chunk.chunk_id for chunk in store.list_chunks()] == ["BACKEND-1", "SECURITY-1"]
    assert [chunk.chunk_id for chunk in store.search([1.0, 0.0], top_k=1)] == ["BACKEND-1"]


def test_sqlite_vec_store_filters_search_by_domain(tmp_path: Path) -> None:
    """Keep the existing optional domain filter behavior."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    store = SQLiteVecIndexStore(path=tmp_path / "index.sqlite3")
    store.replace(
        [
            make_chunk("BACKEND-1", domain="backend", vector=[1.0, 0.0]),
            make_chunk("SECURITY-1", domain="security", vector=[0.9, 0.1]),
        ]
    )

    results = store.search([1.0, 0.0], top_k=2, domain="security")

    assert [chunk.chunk_id for chunk in results] == ["SECURITY-1"]


def test_sqlite_vec_store_domain_search_fills_top_k_after_nearer_other_domains(
    tmp_path: Path,
) -> None:
    """Treat domain as a deterministic filter, not a limited-candidate heuristic."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    store = SQLiteVecIndexStore(path=tmp_path / "index.sqlite3")
    store.replace(
        [
            *[
                make_chunk(f"BACKEND-{index}", domain="backend", vector=[1.0, 0.0])
                for index in range(25)
            ],
            make_chunk("SECURITY-1", domain="security", vector=[0.0, 1.0]),
            make_chunk("SECURITY-2", domain="security", vector=[0.0, 0.9]),
            make_chunk("SECURITY-3", domain="security", vector=[0.0, 0.8]),
        ]
    )

    results = store.search([1.0, 0.0], top_k=3, domain="security")

    assert [chunk.chunk_id for chunk in results] == [
        "SECURITY-3",
        "SECURITY-2",
        "SECURITY-1",
    ]


def test_sqlite_vec_store_round_trips_json_metadata(tmp_path: Path) -> None:
    """Preserve list metadata through SQLite JSON serialization."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    store = SQLiteVecIndexStore(path=tmp_path / "index.sqlite3")
    store.replace(
        [
            make_chunk(
                "BACKEND-1",
                domain="backend",
                vector=[1.0, 0.0],
                tags=["observability", "backend"],
                grounded_in=["https://example.com/policy"],
            )
        ]
    )

    [chunk] = store.list_chunks()

    assert chunk.policy.tags == ["observability", "backend"]
    assert chunk.policy.grounded_in == ["https://example.com/policy"]


def test_sqlite_vec_store_rejects_empty_or_inconsistent_vectors(tmp_path: Path) -> None:
    """Fail closed before creating a usable index for malformed embeddings."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    store = SQLiteVecIndexStore(path=tmp_path / "index.sqlite3")

    with pytest.raises(MissingIndexError):
        store.replace([])
    with pytest.raises(MissingIndexError):
        store.replace([make_chunk("BACKEND-1", vector=[])])
    with pytest.raises(MissingIndexError):
        store.replace(
            [
                make_chunk("BACKEND-1", vector=[1.0, 0.0]),
                make_chunk("BACKEND-2", vector=[1.0, 0.0, 0.0]),
            ]
        )
    assert store.exists() is False


def test_sqlite_vec_store_failed_replace_preserves_existing_index(tmp_path: Path) -> None:
    """A failed rebuild must not destroy the previous usable index."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    store = SQLiteVecIndexStore(path=tmp_path / "index.sqlite3")
    store.replace([make_chunk("BACKEND-1", vector=[1.0, 0.0])])

    with pytest.raises(MissingIndexError):
        store.replace([make_chunk("BROKEN-1", vector=[])])

    assert store.exists() is True
    assert store.count() == 1
    assert [chunk.chunk_id for chunk in store.list_chunks()] == ["BACKEND-1"]


def test_sqlite_vec_store_reports_missing_index_and_resets_sidecars(tmp_path: Path) -> None:
    """Missing and reset indexes must fail closed without stale SQLite sidecars."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    store = SQLiteVecIndexStore(path=tmp_path / "index.sqlite3")

    assert store.exists() is False
    assert store.count() == 0
    with pytest.raises(MissingIndexError):
        store.list_chunks()
    with pytest.raises(MissingIndexError):
        store.search([1.0, 0.0], top_k=1)

    store.replace([make_chunk("BACKEND-1", vector=[1.0, 0.0])])
    store.reset_for_tests()

    assert store.exists() is False
    assert store.count() == 0
    assert not store.path.exists()
    assert not store.path.with_name(f"{store.path.name}-wal").exists()
    assert not store.path.with_name(f"{store.path.name}-shm").exists()


def test_sqlite_vec_store_rejects_directory_path_without_partial_index(tmp_path: Path) -> None:
    """A directory path is not a valid SQLite database target."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    index_path = tmp_path / "index.sqlite3"
    index_path.mkdir()
    store = SQLiteVecIndexStore(path=index_path)

    with pytest.raises(MissingIndexError, match="must not be a directory"):
        store.replace([make_chunk("BACKEND-1", vector=[1.0, 0.0])])

    assert store.exists() is False


def test_sqlite_vec_store_inspect_readiness_reports_invalid_database_file(tmp_path: Path) -> None:
    """Classify placeholder files as invalid SQLite indexes instead of missing ones."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    index_path = tmp_path / "index.sqlite3"
    index_path.write_text("not a sqlite database", encoding="utf-8")
    store = SQLiteVecIndexStore(path=index_path)

    readiness = store.inspect_readiness()

    assert readiness.state == "invalid"
    assert readiness.row_count == 0
    assert readiness.error is not None
    assert store.exists() is False
    assert store.count() == 0


def test_sqlite_vec_store_supports_injected_ingest_and_search_round_trip(
    tmp_path: Path,
) -> None:
    """Prove Day 2 compatibility without switching default service factories."""
    from policynim.services.ingest import IngestService
    from policynim.services.search import SearchService
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    policies_dir = tmp_path / "policies"
    write_policy(
        policies_dir / "backend" / "logging.md",
        """
        ---
        policy_id: BACKEND-LOG-001
        title: Logging
        domain: backend
        ---
        # Logging

        ## Rules

        Backend services should log request ids before writing events.
        """,
    )
    write_policy(
        policies_dir / "security" / "tokens.md",
        """
        ---
        policy_id: SECURITY-TOKEN-001
        title: Tokens
        domain: security
        ---
        # Tokens

        ## Rules

        Security services should rotate session tokens.
        """,
    )
    store = SQLiteVecIndexStore(path=tmp_path / "index.sqlite3")

    ingest_result = IngestService(
        embedder=RoundTripEmbedder(),
        index_store=store,
        corpus_root=policies_dir,
        embedding_model="round-trip-embedder",
        runtime_rules_artifact_path=tmp_path / "runtime" / "runtime_rules.json",
    ).run()

    assert ingest_result.index_uri == store.path.as_posix()
    assert ingest_result.table_name == store.table_name
    assert ingest_result.chunk_count == store.count()
    assert store.exists() is True

    search_result = SearchService(
        embedder=RoundTripEmbedder(),
        index_store=store,
        reranker=None,
    ).search(SearchRequest(query="backend logs", top_k=1, domain="backend"))

    assert [hit.policy.domain for hit in search_result.hits] == ["backend"]
    assert search_result.insufficient_context is False


class RoundTripEmbedder:
    """Map policy text and queries into a deterministic two-dimensional space."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed policy documents for round-trip tests."""
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query for round-trip tests."""
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        """Return the deterministic vector for one text value."""
        lowered = text.lower()
        if "security" in lowered or "token" in lowered:
            return [0.0, 1.0]
        return [1.0, 0.0]


def make_chunk(
    chunk_id: str,
    *,
    domain: str = "backend",
    vector: Sequence[float],
    tags: Sequence[str] = ("observability",),
    grounded_in: Sequence[str] = ("https://example.com/policy",),
) -> EmbeddedChunk:
    """Build one embedded policy chunk for storage-contract tests."""
    return EmbeddedChunk(
        chunk_id=chunk_id,
        path=f"policies/{domain}/{chunk_id.lower()}.md",
        section="Rules",
        lines="1-4",
        text=f"{domain} guidance for {chunk_id}",
        policy=PolicyMetadata(
            policy_id=f"{domain.upper()}-POLICY-001",
            title=f"{domain.title()} Policy",
            doc_type="guidance",
            domain=domain,
            tags=list(tags),
            grounded_in=list(grounded_in),
        ),
        vector=[float(value) for value in vector],
    )


def write_policy(path: Path, content: str) -> None:
    """Write one temporary policy document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip(), encoding="utf-8")
