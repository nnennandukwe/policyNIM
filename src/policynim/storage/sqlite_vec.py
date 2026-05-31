"""Planned sqlite-vec local vector storage for PolicyNIM."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from policynim.contracts import IndexStore
from policynim.types import EmbeddedChunk, PolicyChunk, ScoredChunk


class SQLiteVecIndexStore(IndexStore):
    """Day 1 skeleton for the future sqlite-vec-backed index store."""

    def __init__(self, *, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        """Return the configured SQLite index file path."""
        return self._path

    def replace(self, chunks: Sequence[EmbeddedChunk]) -> None:
        """Replace the local index contents with embedded chunks."""
        raise NotImplementedError("SQLiteVecIndexStore is implemented on Day 2.")

    def exists(self) -> bool:
        """Return whether the local index exists."""
        raise NotImplementedError("SQLiteVecIndexStore is implemented on Day 2.")

    def count(self) -> int:
        """Return the number of rows in the local index."""
        raise NotImplementedError("SQLiteVecIndexStore is implemented on Day 2.")

    def list_chunks(self) -> list[PolicyChunk]:
        """Return all indexed chunks without embeddings."""
        raise NotImplementedError("SQLiteVecIndexStore is implemented on Day 2.")

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        domain: str | None = None,
    ) -> list[ScoredChunk]:
        """Search the local index and return scored chunks."""
        raise NotImplementedError("SQLiteVecIndexStore is implemented on Day 2.")
