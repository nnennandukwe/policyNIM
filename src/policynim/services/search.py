"""Search service for PolicyNIM retrieval."""

from __future__ import annotations

from types import TracebackType

from policynim.contracts import Embedder, IndexStore, Reranker
from policynim.operations import bounded_stage, check_budget, close_components
from policynim.settings import Settings, get_settings
from policynim.storage import create_index_store
from policynim.storage.index_readiness import require_index_ready
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
        operation_timeout_seconds: float = 120.0,
    ) -> None:
        """Assign operation-local dependencies and the outer deadline default."""
        self._operation_timeout_seconds = operation_timeout_seconds
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
        close_components(self._embedder, self._reranker)

    @bounded_stage("search")
    def search(self, request: SearchRequest) -> SearchResult:
        """Run dense retrieval followed by reranking against the local index."""
        _ensure_index_ready(self._index_store)

        check_budget()
        query_embedding = self._embedder.embed_query(request.query)
        check_budget()
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
            check_budget()
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
    index_store = create_index_store(active_settings)
    embedder, reranker = _create_default_search_components(active_settings)
    return SearchService(
        embedder=embedder,
        index_store=index_store,
        reranker=reranker,
        operation_timeout_seconds=active_settings.operation_timeout_seconds,
    )


def _create_default_search_components(settings: Settings) -> tuple[Embedder, Reranker]:
    """Create owned adapters and clean up if the second constructor fails."""
    from policynim.providers import NVIDIAEmbedder, NVIDIAReranker

    embedder = NVIDIAEmbedder.from_settings(settings)
    try:
        return embedder, NVIDIAReranker.from_settings(settings)
    except BaseException:
        close_components(embedder)
        raise


def _ensure_index_ready(index_store: IndexStore) -> None:
    """Reject missing or incompatible vectors before contacting the embedding provider."""
    require_index_ready(
        index_store, missing_message="Run `policynim ingest` before searching the policy corpus."
    )
