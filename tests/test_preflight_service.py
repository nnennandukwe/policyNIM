"""Tests for the Day 4 grounded preflight service."""

from __future__ import annotations

import pytest

from policynim.errors import MissingIndexError
from policynim.services.preflight import (
    DraftPolicyGuidance,
    GeneratedPreflightDraft,
    PreflightService,
)
from policynim.types import (
    Citation,
    PolicyGuidance,
    PolicyMetadata,
    PreflightRequest,
    ScoredChunk,
)


class FakeEmbedder:
    """Returns deterministic task embeddings."""

    def embed_query(self, text: str) -> list[float]:
        mapping = {
            "refresh token cleanup": [1.0, 0.0],
            "backend guidance": [0.0, 1.0],
            "missing citations": [-1.0, -1.0],
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
    """Preserves candidate order unless a custom ranking is provided."""

    def __init__(
        self, *, order: list[str] | None = None, empty_queries: set[str] | None = None
    ) -> None:
        self._order = order or []
        self._empty_queries = empty_queries or set()
        self.last_query: str | None = None
        self.last_candidates: list[ScoredChunk] = []
        self.last_top_k: int | None = None

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


class FakeGenerator:
    """Returns a deterministic grounded draft and records its context."""

    def __init__(self, draft: GeneratedPreflightDraft) -> None:
        self._draft = draft
        self.last_request: PreflightRequest | None = None
        self.last_context: list[ScoredChunk] = []

    def generate_preflight(
        self, request: PreflightRequest, context: list[ScoredChunk]
    ) -> GeneratedPreflightDraft:
        self.last_request = request
        self.last_context = list(context)
        return self._draft


def test_preflight_service_returns_grounded_result_and_maps_citations() -> None:
    backend = make_chunk(
        chunk_id="BACKEND-1",
        policy_id="BACKEND-LOG-001",
        domain="backend",
        score=0.98,
        filename="logging.md",
        text="Log request ids for every write path.",
    )
    security = make_chunk(
        chunk_id="SECURITY-1",
        policy_id="SECURITY-TOKEN-001",
        domain="security",
        score=0.91,
        filename="tokens.md",
        text="Never log token values.",
    )
    store = FakeIndexStore([backend, security])
    reranker = FakeReranker(order=["BACKEND-1", "SECURITY-1"])
    draft = GeneratedPreflightDraft(
        summary="Use request ids and avoid token leakage.",
        applicable_policies=[
            DraftPolicyGuidance(
                policy_id="BACKEND-LOG-001",
                title="Logging",
                rationale="Logging should include stable request context.",
                citation_ids=["BACKEND-1"],
            ),
            DraftPolicyGuidance(
                policy_id="SECURITY-TOKEN-001",
                title="Token handling",
                rationale="Sensitive tokens must not reach logs.",
                citation_ids=["SECURITY-1"],
            ),
        ],
        implementation_guidance=["Log request ids.", "Redact secret material."],
        review_flags=["Check for accidental token logging."],
        tests_required=["Add unit tests for token redaction."],
        citation_ids=["BACKEND-1", "SECURITY-1"],
    )
    generator = FakeGenerator(draft)
    service = PreflightService(
        embedder=FakeEmbedder(),
        index_store=store,
        reranker=reranker,
        generator=generator,
    )

    result = service.preflight(PreflightRequest(task="refresh token cleanup", top_k=2))

    assert store.last_top_k == 15
    assert reranker.last_top_k == 15
    assert generator.last_request is not None
    assert generator.last_request.top_k == 2
    assert result.summary == "Use request ids and avoid token leakage."
    assert result.applicable_policies == [
        PolicyGuidance(
            policy_id="BACKEND-LOG-001",
            title="Logging",
            rationale="Logging should include stable request context.",
            citation_ids=["BACKEND-1"],
        ),
        PolicyGuidance(
            policy_id="SECURITY-TOKEN-001",
            title="Token handling",
            rationale="Sensitive tokens must not reach logs.",
            citation_ids=["SECURITY-1"],
        ),
    ]
    assert result.citations == [
        Citation(
            policy_id="BACKEND-LOG-001",
            title="Logging",
            path="policies/backend/logging.md",
            section="Rules",
            lines="1-4",
            chunk_id="BACKEND-1",
        ),
        Citation(
            policy_id="SECURITY-TOKEN-001",
            title="Token handling",
            path="policies/security/tokens.md",
            section="Rules",
            lines="1-4",
            chunk_id="SECURITY-1",
        ),
    ]
    assert not result.insufficient_context


def test_preflight_service_caps_retained_chunks_per_policy() -> None:
    store = FakeIndexStore(
        [
            make_chunk(
                chunk_id="BACKEND-1",
                policy_id="BACKEND-LOG-001",
                domain="backend",
                score=0.99,
            ),
            make_chunk(
                chunk_id="BACKEND-2",
                policy_id="BACKEND-LOG-001",
                domain="backend",
                score=0.97,
            ),
            make_chunk(
                chunk_id="BACKEND-3",
                policy_id="BACKEND-LOG-001",
                domain="backend",
                score=0.95,
            ),
            make_chunk(
                chunk_id="SECURITY-1",
                policy_id="SECURITY-TOKEN-001",
                domain="security",
                score=0.90,
            ),
        ]
    )
    reranker = FakeReranker(order=["BACKEND-1", "BACKEND-2", "BACKEND-3", "SECURITY-1"])
    draft = GeneratedPreflightDraft(
        summary="Keep the top ranked policy evidence.",
        applicable_policies=[
            DraftPolicyGuidance(
                policy_id="BACKEND-LOG-001",
                title="Logging",
                rationale="Use the strongest backend guidance.",
                citation_ids=["BACKEND-1", "BACKEND-2"],
            ),
            DraftPolicyGuidance(
                policy_id="SECURITY-TOKEN-001",
                title="Token handling",
                rationale="Retain one security policy.",
                citation_ids=["SECURITY-1"],
            ),
        ],
        citation_ids=["BACKEND-1", "BACKEND-2", "SECURITY-1"],
    )
    generator = FakeGenerator(draft)
    service = PreflightService(
        embedder=FakeEmbedder(),
        index_store=store,
        reranker=reranker,
        generator=generator,
    )

    result = service.preflight(PreflightRequest(task="backend guidance", top_k=4))

    assert [chunk.chunk_id for chunk in generator.last_context] == [
        "BACKEND-1",
        "BACKEND-2",
        "SECURITY-1",
    ]
    assert sum(chunk.policy.policy_id == "BACKEND-LOG-001" for chunk in generator.last_context) == 2
    assert [citation.chunk_id for citation in result.citations] == [
        "BACKEND-1",
        "BACKEND-2",
        "SECURITY-1",
    ]


def test_preflight_service_marks_insufficient_context_for_unknown_chunk_ids() -> None:
    store = FakeIndexStore(
        [
            make_chunk(
                chunk_id="BACKEND-1",
                policy_id="BACKEND-LOG-001",
                domain="backend",
                score=0.99,
            )
        ]
    )
    generator = FakeGenerator(
        GeneratedPreflightDraft(
            summary="Unknown citations should fail closed.",
            citation_ids=["UNKNOWN"],
        )
    )
    service = PreflightService(
        embedder=FakeEmbedder(),
        index_store=store,
        reranker=FakeReranker(),
        generator=generator,
    )

    result = service.preflight(PreflightRequest(task="backend guidance", top_k=1))

    assert result.insufficient_context
    assert result.citations == []
    assert result.applicable_policies == []


def test_preflight_service_marks_insufficient_context_when_generator_cites_nothing() -> None:
    store = FakeIndexStore(
        [
            make_chunk(
                chunk_id="BACKEND-1",
                policy_id="BACKEND-LOG-001",
                domain="backend",
                score=0.99,
            )
        ]
    )
    generator = FakeGenerator(
        GeneratedPreflightDraft(
            summary="No surviving citations should fail closed.",
            applicable_policies=[
                DraftPolicyGuidance(
                    policy_id="BACKEND-LOG-001",
                    title="Logging",
                    rationale="Reasonable guidance without citations must not pass.",
                    citation_ids=[],
                )
            ],
            implementation_guidance=["Do the thing."],
            citation_ids=[],
        )
    )
    service = PreflightService(
        embedder=FakeEmbedder(),
        index_store=store,
        reranker=FakeReranker(),
        generator=generator,
    )

    result = service.preflight(PreflightRequest(task="backend guidance", top_k=1))

    assert result.insufficient_context
    assert result.citations == []
    assert result.applicable_policies == []


def test_preflight_service_requires_existing_index() -> None:
    store = FakeIndexStore([], exists=False)
    service = PreflightService(
        embedder=FakeEmbedder(),
        index_store=store,
        reranker=FakeReranker(),
        generator=FakeGenerator(GeneratedPreflightDraft(summary="unused")),
    )

    with pytest.raises(MissingIndexError):
        service.preflight(PreflightRequest(task="backend guidance", top_k=1))


def make_chunk(
    *,
    chunk_id: str,
    policy_id: str,
    domain: str,
    score: float,
    filename: str = "policy.md",
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
        path=f"policies/{domain}/{filename}",
        section="Rules",
        lines="1-4",
        text=text,
        policy=metadata,
        score=score,
    )
