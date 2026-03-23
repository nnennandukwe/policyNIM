"""Core external seams for later PolicyNIM implementation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from policynim.types import (
    EmbeddedChunk,
    PolicyChunk,
    PreflightRequest,
    PreflightResult,
    ScoredChunk,
)


class Embedder(Protocol):
    """Embeds policy content and queries."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed policy document chunks."""

    def embed_query(self, text: str) -> list[float]:
        """Embed a query for retrieval."""


class Reranker(Protocol):
    """Reorders retrieved candidates based on query relevance."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[ScoredChunk],
        *,
        top_k: int,
    ) -> list[ScoredChunk]:
        """Return reranked candidates."""


class Generator(Protocol):
    """Generates grounded guidance from retrieved context."""

    def generate_preflight(
        self,
        request: PreflightRequest,
        context: Sequence[ScoredChunk],
    ) -> PreflightResult:
        """Generate a grounded preflight result."""


class IndexStore(Protocol):
    """Stores and searches policy chunks."""

    def replace(self, chunks: Sequence[EmbeddedChunk]) -> None:
        """Replace the local index contents with embedded chunks."""

    def exists(self) -> bool:
        """Return whether the local index exists."""

    def count(self) -> int:
        """Return the number of rows in the local index."""

    def list_chunks(self) -> list[PolicyChunk]:
        """Return all indexed chunks without embeddings."""

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        domain: str | None = None,
    ) -> list[ScoredChunk]:
        """Search the local index and return scored chunks."""
