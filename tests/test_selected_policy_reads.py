"""Bounded selected-policy reads through the actual SQLite index API."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_sqlite_vec import make_chunk

from policynim.storage import sqlite_vec as adapter
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import EmbeddingIdentity, PolicyChunk


@pytest.mark.parametrize("legacy_index", [False, True])
def test_selected_policy_read_doesnt_materialize_unselected_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_index: bool
) -> None:
    """A large unrelated policy can't consume the selected policy's payload budget."""
    store = SQLiteVecIndexStore(
        path=tmp_path / "index.sqlite3",
        embedding_identity=EmbeddingIdentity(model="test", endpoint="https://example.invalid/v1"),
    )
    selected = make_chunk("SELECTED", domain="backend", vector=[1.0, 0.0])
    unselected = make_chunk("UNSELECTED", domain="security", vector=[0.0, 1.0]).model_copy(
        update={"text": "x" * 2_000_000}
    )
    store.replace([selected, unselected])
    if legacy_index:
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP INDEX idx_policy_chunks_policy_id")
    before = store.path.read_bytes()
    decoded: list[str] = []
    original = adapter._policy_chunk_from_row

    def record(row: sqlite3.Row) -> PolicyChunk:
        """Observe decoded payloads while keeping the actual SQLite conversion unchanged."""
        decoded.append(str(row["chunk_id"]))
        return original(row)

    monkeypatch.setattr(adapter, "_policy_chunk_from_row", record)
    chunks = store.read_policy_chunks(
        [selected.policy.policy_id], max_chunks=80, max_source_bytes=1024
    )
    assert [chunk.chunk_id for chunk in chunks] == [selected.chunk_id]
    assert decoded == [selected.chunk_id]
    assert store.count() == 2
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("limit", ["rows", "bytes"])
def test_selected_policy_read_refuses_before_materializing_oversized_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    """Whole-context refusal happens before Python decodes any selected source payload."""
    from policynim.errors import IndexReadLimitError

    store = SQLiteVecIndexStore(
        path=tmp_path / "index.sqlite3",
        embedding_identity=EmbeddingIdentity(model="test", endpoint="https://example.invalid/v1"),
    )
    chunks = [
        make_chunk(f"SELECTED-{index}", vector=[1.0, 0.0]).model_copy(
            update={"text": "x" * (2000 if limit == "bytes" else 1)}
        )
        for index in range(2 if limit == "rows" else 1)
    ]
    store.replace(chunks)

    def forbid_decode(row: sqlite3.Row) -> PolicyChunk:
        """Reject a premature payload allocation before the database budget decision."""
        pytest.fail("An oversized selected context was materialized")

    monkeypatch.setattr(adapter, "_policy_chunk_from_row", forbid_decode)
    with pytest.raises(IndexReadLimitError):
        store.read_policy_chunks([chunks[0].policy.policy_id], max_chunks=1, max_source_bytes=1024)
    assert store.count() == len(chunks)


@pytest.mark.parametrize("multibyte", [False, True])
def test_selected_policy_read_counts_utf8_bytes(tmp_path: Path, multibyte: bool) -> None:
    """Equal character counts need different byte budgets before source materialization."""
    from policynim.errors import IndexReadLimitError

    store = SQLiteVecIndexStore(
        path=tmp_path / "index.sqlite3",
        embedding_identity=EmbeddingIdentity(model="test", endpoint="https://example.invalid/v1"),
    )
    # Non-English test data verifies the UTF-8 byte boundary.
    text = ("\u00e9" if multibyte else "a") * 400
    chunk = make_chunk("SELECTED", vector=[1.0, 0.0]).model_copy(update={"text": text})
    store.replace([chunk])
    if multibyte:
        with pytest.raises(IndexReadLimitError):
            store.read_policy_chunks([chunk.policy.policy_id], max_chunks=1, max_source_bytes=700)
    else:
        assert (
            store.read_policy_chunks([chunk.policy.policy_id], max_chunks=1, max_source_bytes=700)[
                0
            ].text
            == text
        )


def test_public_router_hydration_reads_only_selected_policy_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observe actual SQLite payloads after ranking through the public routing service."""
    from collections.abc import Sequence

    from test_router_service import MockEmbedder

    from policynim.services.router import PolicyRouterService
    from policynim.types import RouteRequest, ScoredChunk

    store = SQLiteVecIndexStore(
        path=tmp_path / "index.sqlite3",
        embedding_identity=EmbeddingIdentity(model="test", endpoint="https://example.invalid/v1"),
    )
    selected = make_chunk("SELECTED", domain="backend", vector=[1.0, 0.0])
    store.replace(
        [
            selected,
            *[
                make_chunk(f"UNSELECTED-{index}", domain="security", vector=[0.0, 1.0])
                for index in range(100)
            ],
        ]
    )
    hydration_started = [False]
    decoded: list[str] = []
    original = adapter._policy_chunk_from_row

    def record(row: sqlite3.Row) -> PolicyChunk:
        """Record only post-ranking source materialization, preserving real SQL decoding."""
        if hydration_started[0]:
            decoded.append(str(row["policy_id"]))
        return original(row)

    class Reranker:
        """Select one real dense hit and mark the public post-ranking boundary."""

        def rerank(
            self, query: str, candidates: Sequence[ScoredChunk], *, top_k: int
        ) -> list[ScoredChunk]:
            """Leave source identity unchanged while selecting the known policy."""
            hydration_started[0] = True
            return [c for c in candidates if c.policy.policy_id == selected.policy.policy_id]

    monkeypatch.setattr(adapter, "_policy_chunk_from_row", record)
    service = PolicyRouterService(embedder=MockEmbedder(), index_store=store, reranker=Reranker())
    result = service.route(RouteRequest(task="Implement cleanup job", top_k=1))
    assert result.retained_context
    assert decoded == [selected.policy.policy_id]
