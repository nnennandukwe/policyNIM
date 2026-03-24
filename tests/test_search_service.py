"""Tests for the Day 4 reranked search service."""

from __future__ import annotations

import pytest

from policynim.errors import MissingIndexError
from policynim.services.search import SearchService
from policynim.types import PolicyMetadata, ScoredChunk, SearchRequest


class FakeEmbedder:
    """Returns deterministic query embeddings."""

    def embed_query(self, text: str) -> list[float]:
        mapping = {
            "backend logs": [1.0, 0.0],
            "security only": [0.0, 1.0],
            "no match": [-1.0, -1.0],
        }
        return mapping[text]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FakeIndexStore:
    """Returns deterministic dense candidates and records query parameters."""

    def __init__(self, chunks: list[ScoredChunk], *, exists: bool = True) -> None:
        self._chunks = list(chunks)
        self._exists = exists
        self.last_query_embedding: list[float] | None = None
        self.last_top_k: int | None = None
        self.last_domain: str | None = None

    def exists(self) -> bool:
        return self._exists

    def count(self) -> int:
        return len(self._chunks) if self._exists else 0

    def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        domain: str | None = None,
    ) -> list[ScoredChunk]:
        self.last_query_embedding = list(query_embedding)
        self.last_top_k = top_k
        self.last_domain = domain
        candidates = [
            chunk for chunk in self._chunks if domain is None or chunk.policy.domain == domain
        ]
        return candidates[:top_k]


class FakeReranker:
    """Reorders dense candidates deterministically for assertions."""

    def __init__(
        self, *, order: list[str] | None = None, empty_queries: set[str] | None = None
    ) -> None:
        self._order = order or []
        self._empty_queries = empty_queries or set()
        self.last_query: str | None = None
        self.last_candidates: list[ScoredChunk] = []
        self.last_top_k: int | None = None
        self.closed = False

    def rerank(
        self,
        query: str,
        candidates: list[ScoredChunk],
        *,
        top_k: int,
    ) -> list[ScoredChunk]:
        self.last_query = query
        self.last_candidates = list(candidates)
        self.last_top_k = top_k
        if query in self._empty_queries:
            return []

        ordering = {chunk_id: index for index, chunk_id in enumerate(self._order)}
        ranked = sorted(
            candidates,
            key=lambda chunk: ordering.get(chunk.chunk_id, len(ordering)),
        )
        return ranked[:top_k]

    def close(self) -> None:
        self.closed = True


def test_search_service_reranks_dense_candidates_before_returning() -> None:
    store = FakeIndexStore(
        [
            make_chunk(
                chunk_id="BACKEND-2",
                domain="backend",
                score=0.71,
                text="Use request ids in backend logs.",
            ),
            make_chunk(
                chunk_id="BACKEND-1",
                domain="backend",
                score=0.95,
                text="Log request ids before writing events.",
            ),
        ]
    )
    reranker = FakeReranker(order=["BACKEND-1", "BACKEND-2"])
    service = SearchService(embedder=FakeEmbedder(), index_store=store, reranker=reranker)

    result = service.search(SearchRequest(query="backend logs", top_k=2))

    assert store.last_top_k == 15
    assert reranker.last_top_k == 2
    assert [hit.chunk_id for hit in result.hits] == ["BACKEND-1", "BACKEND-2"]
    assert not result.insufficient_context


def test_search_service_filters_by_domain_before_reranking() -> None:
    store = FakeIndexStore(
        [
            make_chunk(
                chunk_id="BACKEND-1",
                domain="backend",
                text="Use request ids in backend logs.",
            ),
            make_chunk(
                chunk_id="SECURITY-1",
                domain="security",
                text="Rotate session tokens safely.",
            ),
        ]
    )
    reranker = FakeReranker(order=["SECURITY-1", "BACKEND-1"])
    service = SearchService(embedder=FakeEmbedder(), index_store=store, reranker=reranker)

    result = service.search(SearchRequest(query="security only", top_k=1, domain="security"))

    assert store.last_domain == "security"
    assert [chunk.chunk_id for chunk in reranker.last_candidates] == ["SECURITY-1"]
    assert [hit.chunk_id for hit in result.hits] == ["SECURITY-1"]


def test_search_service_marks_insufficient_context_after_reranking() -> None:
    store = FakeIndexStore(
        [
            make_chunk(
                chunk_id="BACKEND-1",
                domain="backend",
                text="Use request ids in backend logs.",
            )
        ]
    )
    reranker = FakeReranker(empty_queries={"no match"})
    service = SearchService(embedder=FakeEmbedder(), index_store=store, reranker=reranker)

    result = service.search(SearchRequest(query="no match", top_k=1))

    assert result.hits == []
    assert result.insufficient_context


def test_search_service_sets_insufficient_context_when_index_has_no_matches() -> None:
    store = FakeIndexStore([make_chunk(chunk_id="BACKEND-1", domain="backend")])
    service = SearchService(embedder=FakeEmbedder(), index_store=store, reranker=FakeReranker())

    result = service.search(SearchRequest(query="no match", top_k=1, domain="security"))

    assert result.hits == []
    assert result.insufficient_context


def test_search_service_requires_existing_index() -> None:
    store = FakeIndexStore([], exists=False)
    service = SearchService(embedder=FakeEmbedder(), index_store=store, reranker=FakeReranker())

    with pytest.raises(MissingIndexError):
        service.search(SearchRequest(query="backend logs", top_k=1))


def test_search_service_close_closes_reranker() -> None:
    reranker = FakeReranker()
    service = SearchService(
        embedder=FakeEmbedder(),
        index_store=FakeIndexStore([make_chunk(chunk_id="BACKEND-1", domain="backend")]),
        reranker=reranker,
    )

    service.close()

    assert reranker.closed is True


def make_chunk(
    *,
    chunk_id: str,
    domain: str,
    score: float | None = None,
    text: str = "Sample policy text.",
) -> ScoredChunk:
    metadata = PolicyMetadata(
        policy_id=f"{domain.upper()}-001",
        title=f"{domain.title()} Policy",
        doc_type="guidance",
        domain=domain,
    )
    return ScoredChunk(
        chunk_id=chunk_id,
        path=f"policies/{domain}/doc.md",
        section="Rules",
        lines="1-4",
        text=text,
        policy=metadata,
        score=score,
    )
