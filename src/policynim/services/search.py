"""Search service for PolicyNIM retrieval."""

from __future__ import annotations

from types import TracebackType

from policynim.contracts import Embedder, IndexStore, Reranker
from policynim.errors import MissingIndexError
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import SearchRequest, SearchResult

_DEFAULT_RERANK_CANDIDATE_POOL = 15


class SearchService:
    """Embed queries, rerank candidates, and search the local PolicyNIM index."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        index_store: IndexStore,
        reranker: Reranker | None = None,
    ) -> None:
        self._embedder = embedder
        self._index_store = index_store
        self._reranker = reranker

    def __enter__(self) -> SearchService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release owned provider resources held by this service."""
        _close_component(self._reranker)

    def search(self, request: SearchRequest) -> SearchResult:
        """Run dense retrieval followed by reranking against the local index."""
        _ensure_index_ready(self._index_store)

        query_embedding = self._embedder.embed_query(request.query)
        dense_candidates = self._index_store.search(
            query_embedding,
            top_k=max(request.top_k, _DEFAULT_RERANK_CANDIDATE_POOL),
            domain=request.domain,
        )
        if not dense_candidates:
            return SearchResult(
                query=request.query,
                domain=request.domain,
                top_k=request.top_k,
                hits=[],
                insufficient_context=True,
            )

        hits = dense_candidates[: request.top_k]
        if self._reranker is not None:
            hits = self._reranker.rerank(request.query, dense_candidates, top_k=request.top_k)

        return SearchResult(
            query=request.query,
            domain=request.domain,
            top_k=request.top_k,
            hits=hits[: request.top_k],
            insufficient_context=not hits,
        )


def create_search_service(settings: Settings | None = None) -> SearchService:
    """Build the default search service from application settings."""
    active_settings = settings or get_settings()
    embedder, reranker = _create_default_search_components(active_settings)
    return SearchService(
        embedder=embedder,
        index_store=LanceDBIndexStore(
            uri=resolve_runtime_path(active_settings.lancedb_uri),
            table_name=active_settings.lancedb_table,
        ),
        reranker=reranker,
    )


def _create_default_search_components(settings: Settings) -> tuple[Embedder, Reranker]:
    from policynim.providers import NVIDIAEmbedder, NVIDIAReranker

    return (
        NVIDIAEmbedder.from_settings(settings),
        NVIDIAReranker.from_settings(settings),
    )


def _ensure_index_ready(index_store: IndexStore) -> None:
    if not index_store.exists() or index_store.count() == 0:
        raise MissingIndexError("Run `policynim ingest` before searching the policy corpus.")


def _close_component(component: object | None) -> None:
    close = getattr(component, "close", None)
    if callable(close):
        close()
