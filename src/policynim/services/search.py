"""Search service for PolicyNIM retrieval."""

from __future__ import annotations

from pathlib import Path

from policynim.contracts import Embedder, IndexStore
from policynim.errors import MissingIndexError
from policynim.providers import NVIDIAEmbedder
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import SearchRequest, SearchResult


class SearchService:
    """Embed queries and search the local PolicyNIM index."""

    def __init__(self, *, embedder: Embedder, index_store: IndexStore) -> None:
        self._embedder = embedder
        self._index_store = index_store

    def search(self, request: SearchRequest) -> SearchResult:
        """Run dense retrieval against the local index."""
        if not self._index_store.exists() or self._index_store.count() == 0:
            raise MissingIndexError("Run `policynim ingest` before searching the policy corpus.")
        query_embedding = self._embedder.embed_query(request.query)
        hits = self._index_store.search(
            query_embedding,
            top_k=request.top_k,
            domain=request.domain,
        )

        return SearchResult(
            query=request.query,
            domain=request.domain,
            top_k=request.top_k,
            hits=hits,
            insufficient_context=not hits,
        )


def create_search_service(settings: Settings | None = None) -> SearchService:
    """Build the default search service from application settings."""
    active_settings = settings or get_settings()
    repo_root = _repo_root()
    return SearchService(
        embedder=NVIDIAEmbedder.from_settings(active_settings),
        index_store=LanceDBIndexStore(
            uri=_resolve_repo_path(active_settings.lancedb_uri, repo_root),
            table_name=active_settings.lancedb_table,
        ),
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_repo_path(path: Path, repo_root: Path) -> Path:
    return path if path.is_absolute() else repo_root / path
