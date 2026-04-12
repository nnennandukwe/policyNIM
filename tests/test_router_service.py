"""Tests for the task-aware policy router."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from policynim.errors import MissingIndexError
from policynim.services.router import PolicyRouterService, profile_task
from policynim.types import EmbeddedChunk, PolicyChunk, PolicyMetadata, RouteRequest, ScoredChunk


class MockEmbedder:
    """Returns deterministic embeddings and records the embedded task text."""

    def __init__(self) -> None:
        self.last_text: str | None = None
        self.closed = False

    def embed_query(self, text: str) -> list[float]:
        self.last_text = text
        return [1.0, 0.0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def close(self) -> None:
        self.closed = True


class MockIndexStore:
    """Returns deterministic dense candidates and records query parameters."""

    def __init__(self, chunks: list[ScoredChunk], *, exists: bool = True) -> None:
        self._chunks = list(chunks)
        self._exists = exists
        self.last_top_k: int | None = None
        self.last_domain: str | None = None

    def exists(self) -> bool:
        return self._exists

    def count(self) -> int:
        return len(self._chunks) if self._exists else 0

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        domain: str | None = None,
    ) -> list[ScoredChunk]:
        self.last_top_k = top_k
        self.last_domain = domain
        candidates = [
            chunk for chunk in self._chunks if domain is None or chunk.policy.domain == domain
        ]
        return candidates[:top_k]

    def replace(self, chunks: Sequence[EmbeddedChunk]) -> None:
        self._chunks = [ScoredChunk(**chunk.model_dump(exclude={"vector"})) for chunk in chunks]

    def list_chunks(self) -> list[PolicyChunk]:
        return [PolicyChunk(**chunk.model_dump(exclude={"score"})) for chunk in self._chunks]


class MockReranker:
    """Reorders dense candidates and records the routing query."""

    def __init__(self, *, order: list[str] | None = None) -> None:
        self._order = order or []
        self.last_query: str | None = None
        self.last_candidates: list[ScoredChunk] = []
        self.last_top_k: int | None = None
        self.closed = False

    def rerank(
        self,
        query: str,
        candidates: Sequence[ScoredChunk],
        *,
        top_k: int,
    ) -> list[ScoredChunk]:
        self.last_query = query
        self.last_candidates = list(candidates)
        self.last_top_k = top_k
        ordering = {chunk_id: index for index, chunk_id in enumerate(self._order)}
        ranked = sorted(
            list(candidates),
            key=lambda chunk: ordering.get(chunk.chunk_id, len(ordering)),
        )
        return ranked[:top_k]

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("task", "expected_task_type"),
    [
        ("fix failing auth redirect", "bug_fix"),
        ("refactor auth token module", "refactor"),
        ("change api response contract", "api_change"),
        ("run database migration backfill", "migration"),
        ("add pytest coverage fixture", "test_change"),
        ("build policy export feature", "feature_work"),
    ],
)
def test_profile_task_infers_task_type(task: str, expected_task_type: str) -> None:
    profile = profile_task(task)

    assert profile.task_type == expected_task_type
    assert profile.explicit_task_type is None
    assert profile.signals


def test_profile_task_uses_explicit_task_type_override() -> None:
    profile = profile_task("fix cleanup behavior", explicit_task_type="refactor")

    assert profile.task_type == "refactor"
    assert profile.explicit_task_type == "refactor"
    assert profile.signals == ["explicit:refactor"]


def test_profile_task_keeps_unknown_for_unmatched_or_mixed_tasks() -> None:
    unmatched = profile_task("coordinate quarterly workspace notes")
    mixed = profile_task("fix test")

    assert unmatched.task_type == "unknown"
    assert unmatched.signals == []
    assert mixed.task_type == "unknown"
    assert set(mixed.signals) == {"fix", "test"}


def test_router_retrieves_reranks_and_groups_selected_policy_evidence() -> None:
    embedder = MockEmbedder()
    store = MockIndexStore(
        [
            make_chunk(
                chunk_id="BACKEND-2",
                policy_id="BACKEND-LOG-001",
                domain="backend",
                score=0.72,
            ),
            make_chunk(
                chunk_id="SECURITY-1",
                policy_id="SECURITY-TOKEN-001",
                domain="security",
                score=0.80,
            ),
            make_chunk(
                chunk_id="BACKEND-1",
                policy_id="BACKEND-LOG-001",
                domain="backend",
                score=0.98,
            ),
        ]
    )
    reranker = MockReranker(order=["BACKEND-1", "BACKEND-2", "SECURITY-1"])
    service = PolicyRouterService(embedder=embedder, index_store=store, reranker=reranker)

    result = service.route(RouteRequest(task="fix backend logging bug", top_k=3))

    assert embedder.last_text == "fix backend logging bug"
    assert store.last_top_k == 15
    assert reranker.last_top_k == 15
    assert reranker.last_query is not None
    assert "Task type: bug fix." in reranker.last_query
    assert not result.packet.insufficient_context
    assert result.packet.task_type == "bug_fix"
    assert [policy.policy_id for policy in result.packet.selected_policies] == [
        "BACKEND-LOG-001",
        "SECURITY-TOKEN-001",
    ]
    assert [evidence.chunk_id for evidence in result.packet.selected_policies[0].evidence] == [
        "BACKEND-1",
        "BACKEND-2",
    ]
    assert [chunk.chunk_id for chunk in result.retained_context] == [
        "BACKEND-1",
        "BACKEND-2",
        "SECURITY-1",
    ]


def test_router_applies_domain_filter_before_reranking() -> None:
    store = MockIndexStore(
        [
            make_chunk(
                chunk_id="BACKEND-1",
                policy_id="BACKEND-LOG-001",
                domain="backend",
                score=0.98,
            ),
            make_chunk(
                chunk_id="SECURITY-1",
                policy_id="SECURITY-TOKEN-001",
                domain="security",
                score=0.90,
            ),
        ]
    )
    reranker = MockReranker(order=["BACKEND-1", "SECURITY-1"])
    service = PolicyRouterService(
        embedder=MockEmbedder(),
        index_store=store,
        reranker=reranker,
    )

    result = service.route(RouteRequest(task="review api contract", domain="security", top_k=2))

    assert store.last_domain == "security"
    assert [chunk.chunk_id for chunk in reranker.last_candidates] == ["SECURITY-1"]
    assert [policy.policy_id for policy in result.packet.selected_policies] == [
        "SECURITY-TOKEN-001"
    ]
    assert result.packet.domain == "security"


def test_router_marks_insufficient_context_for_weak_evidence() -> None:
    service = PolicyRouterService(
        embedder=MockEmbedder(),
        index_store=MockIndexStore(
            [
                make_chunk(
                    chunk_id="BACKEND-1",
                    policy_id="BACKEND-LOG-001",
                    domain="backend",
                    score=0.0,
                )
            ]
        ),
        reranker=MockReranker(),
    )

    result = service.route(RouteRequest(task="fix backend logging bug", top_k=1))

    assert result.packet.insufficient_context
    assert result.packet.selected_policies == []
    assert result.retained_context == []


def test_router_requires_existing_index() -> None:
    service = PolicyRouterService(
        embedder=MockEmbedder(),
        index_store=MockIndexStore([], exists=False),
        reranker=MockReranker(),
    )

    with pytest.raises(MissingIndexError):
        service.route(RouteRequest(task="fix backend logging bug", top_k=1))


def test_router_close_closes_owned_components() -> None:
    embedder = MockEmbedder()
    reranker = MockReranker()
    service = PolicyRouterService(
        embedder=embedder,
        index_store=MockIndexStore(
            [make_chunk(chunk_id="BACKEND-1", policy_id="BACKEND-LOG-001", domain="backend")]
        ),
        reranker=reranker,
    )

    service.close()

    assert embedder.closed is True
    assert reranker.closed is True


def make_chunk(
    *,
    chunk_id: str,
    policy_id: str,
    domain: str,
    score: float | None = 0.9,
    text: str = "Sample policy text.",
) -> ScoredChunk:
    metadata = PolicyMetadata(
        policy_id=policy_id,
        title="Logging" if domain == "backend" else "Token handling",
        doc_type="guidance",
        domain=domain,
    )
    return ScoredChunk(
        chunk_id=chunk_id,
        path=f"policies/{domain}/policy.md",
        section="Rules",
        lines="1-4",
        text=text,
        policy=metadata,
        score=score,
    )
