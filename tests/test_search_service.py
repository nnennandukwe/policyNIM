"""Tests for the Day 3 search service."""

from __future__ import annotations

from pathlib import Path

import pytest

from policynim.errors import MissingIndexError
from policynim.services.search import SearchService
from policynim.storage import LanceDBIndexStore
from policynim.types import EmbeddedChunk, PolicyMetadata, SearchRequest


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


def test_search_service_returns_hits_and_honors_domain_filter(tmp_path: Path) -> None:
    store = LanceDBIndexStore(uri=tmp_path / "index", table_name="policy_chunks")
    store.replace(
        [
            make_chunk(
                chunk_id="BACKEND-1",
                domain="backend",
                vector=[1.0, 0.0],
                text="Use request ids in backend logs.",
            ),
            make_chunk(
                chunk_id="SECURITY-1",
                domain="security",
                vector=[0.0, 1.0],
                text="Rotate session tokens safely.",
            ),
        ]
    )
    service = SearchService(embedder=FakeEmbedder(), index_store=store)

    result = service.search(SearchRequest(query="backend logs", top_k=2))
    security_only = service.search(
        SearchRequest(query="security only", top_k=2, domain="security")
    )

    assert result.hits
    assert result.hits[0].chunk_id == "BACKEND-1"
    assert not result.insufficient_context
    assert [hit.chunk_id for hit in security_only.hits] == ["SECURITY-1"]


def test_search_service_sets_insufficient_context_when_index_has_no_matches(tmp_path: Path) -> None:
    store = LanceDBIndexStore(uri=tmp_path / "index", table_name="policy_chunks")
    store.replace([make_chunk(chunk_id="BACKEND-1", domain="backend", vector=[1.0, 0.0])])
    service = SearchService(embedder=FakeEmbedder(), index_store=store)

    result = service.search(SearchRequest(query="no match", top_k=1, domain="security"))

    assert result.hits == []
    assert result.insufficient_context


def test_search_service_requires_existing_index(tmp_path: Path) -> None:
    store = LanceDBIndexStore(uri=tmp_path / "index", table_name="policy_chunks")
    service = SearchService(embedder=FakeEmbedder(), index_store=store)

    with pytest.raises(MissingIndexError):
        service.search(SearchRequest(query="backend logs", top_k=1))


def make_chunk(
    *,
    chunk_id: str,
    domain: str,
    vector: list[float],
    text: str = "Sample policy text.",
) -> EmbeddedChunk:
    metadata = PolicyMetadata(
        policy_id=f"{domain.upper()}-001",
        title=f"{domain.title()} Policy",
        doc_type="guidance",
        domain=domain,
    )
    return EmbeddedChunk(
        chunk_id=chunk_id,
        path=f"policies/{domain}/doc.md",
        section="Rules",
        lines="1-4",
        text=text,
        policy=metadata,
        vector=vector,
    )
