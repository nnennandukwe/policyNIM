"""Search service for PolicyNIM retrieval."""

from __future__ import annotations

from policynim.contracts import Embedder, IndexStore
from policynim.errors import MissingIndexError
from policynim.providers import NVIDIAEmbedder
from policynim.runtime_paths import resolve_runtime_path
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
    return SearchService(
        embedder=NVIDIAEmbedder.from_settings(active_settings),
        index_store=LanceDBIndexStore(
            uri=resolve_runtime_path(active_settings.lancedb_uri),
            table_name=active_settings.lancedb_table,
        ),
    )
