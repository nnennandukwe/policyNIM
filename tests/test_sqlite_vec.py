"""Contract tests for the planned sqlite-vec local index backend."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from policynim.errors import MissingIndexError
from policynim.types import EmbeddedChunk, PolicyMetadata

pytestmark = pytest.mark.xfail(
    strict=True,
    reason="Day 2 implements SQLiteVecIndexStore against this contract.",
)


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
