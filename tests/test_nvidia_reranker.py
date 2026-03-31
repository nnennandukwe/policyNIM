"""Tests for the NVIDIA reranker adapter."""

from __future__ import annotations

import httpx
import pytest

from policynim.errors import ProviderError
from policynim.providers.nvidia import NVIDIAReranker
from policynim.types import PolicyMetadata, ScoredChunk


class SpyResponse:
    """Response stub with the small surface used by the reranker."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class SpyRerankClient:
    """Client stub that records the requested endpoint."""

    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.calls: list[dict[str, object]] = []
        self.closed = False

    def post(self, endpoint: str, json: dict[str, object]) -> SpyResponse:
        self.calls.append({"endpoint": endpoint, "json": json})
        return SpyResponse(self._payload)

    def close(self) -> None:
        self.closed = True


class OwnedClientSpy:
    """Tracks lifecycle of internally created clients."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.closed = False

    def close(self) -> None:
        self.closed = True


class RateLimitedRerankClient:
    """Client stub that always raises HTTP 429 for reranking."""

    def post(self, endpoint: str, json: dict[str, object]) -> SpyResponse:
        request = httpx.Request("POST", f"https://example.invalid/{endpoint}")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    def close(self) -> None:
        return None


def test_reranker_posts_to_relative_endpoint() -> None:
    client = SpyRerankClient({"scores": [0.2, 0.9]})
    reranker = NVIDIAReranker(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1/retrieval",
        timeout_seconds=1,
        max_retries=0,
        client=client,  # type: ignore[arg-type]
    )

    reranked = reranker.rerank("request ids", [make_chunk("A"), make_chunk("B")], top_k=2)

    assert client.calls == [
        {
            "endpoint": "mock-model/reranking",
            "json": {
                "model": "mock-model",
                "query": {"text": "request ids"},
                "passages": [
                    {"text": "Use explicit request ids in logs."},
                    {"text": "Rotate session tokens promptly."},
                ],
                "truncate": "END",
            },
        }
    ]
    assert [chunk.chunk_id for chunk in reranked] == ["B", "A"]


def test_reranker_preserves_retrieval_base_url_path() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"scores": [0.5]})

    client = httpx.Client(
        base_url="https://example.invalid/v1/retrieval",
        transport=httpx.MockTransport(handler),
    )
    reranker = NVIDIAReranker(
        api_key="test-key",
        model="mock-model",
        base_url="https://unused.invalid",
        timeout_seconds=1,
        max_retries=0,
        client=client,
    )

    try:
        reranker.rerank("request ids", [make_chunk("A")], top_k=1)
    finally:
        client.close()

    assert seen_urls == ["https://example.invalid/v1/retrieval/mock-model/reranking"]


def test_reranker_close_does_not_close_injected_client() -> None:
    client = SpyRerankClient({"scores": [0.5]})
    reranker = NVIDIAReranker(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1/retrieval",
        timeout_seconds=1,
        max_retries=0,
        client=client,  # type: ignore[arg-type]
    )

    reranker.close()

    assert not client.closed


def test_reranker_close_closes_owned_client(monkeypatch) -> None:
    created_clients: list[OwnedClientSpy] = []

    def build_client(**kwargs: object) -> OwnedClientSpy:
        client = OwnedClientSpy(**kwargs)
        created_clients.append(client)
        return client

    monkeypatch.setattr("policynim.providers.nvidia.httpx.Client", build_client)

    reranker = NVIDIAReranker(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1/retrieval",
        timeout_seconds=1,
        max_retries=0,
    )

    reranker.close()

    assert len(created_clients) == 1
    assert created_clients[0].closed
    assert created_clients[0].kwargs["base_url"] == "https://example.invalid/v1/retrieval"


def test_reranker_context_manager_closes_owned_client(monkeypatch) -> None:
    created_clients: list[OwnedClientSpy] = []

    def build_client(**kwargs: object) -> OwnedClientSpy:
        client = OwnedClientSpy(**kwargs)
        created_clients.append(client)
        return client

    monkeypatch.setattr("policynim.providers.nvidia.httpx.Client", build_client)

    with NVIDIAReranker(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1/retrieval",
        timeout_seconds=1,
        max_retries=0,
    ) as reranker:
        assert reranker is not None

    assert len(created_clients) == 1
    assert created_clients[0].closed


def test_reranker_rejects_score_count_mismatch() -> None:
    reranker = NVIDIAReranker(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1/retrieval",
        timeout_seconds=1,
        max_retries=0,
        client=SpyRerankClient({"scores": [0.5]}),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderError, match="response count did not match") as excinfo:
        reranker.rerank("request ids", [make_chunk("A"), make_chunk("B")], top_k=2)

    assert excinfo.value.failure_class == "invalid_response"


def test_reranker_rejects_rows_without_numeric_scores() -> None:
    reranker = NVIDIAReranker(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1/retrieval",
        timeout_seconds=1,
        max_retries=0,
        client=SpyRerankClient({"results": [{"index": 0, "label": "bad"}]}),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderError, match="numeric score") as excinfo:
        reranker.rerank("request ids", [make_chunk("A")], top_k=1)

    assert excinfo.value.failure_class == "invalid_response"


def test_reranker_classifies_http_429_as_rate_limit() -> None:
    reranker = NVIDIAReranker(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1/retrieval",
        timeout_seconds=1,
        max_retries=0,
        client=RateLimitedRerankClient(),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderError, match="failed after retries") as excinfo:
        reranker.rerank("request ids", [make_chunk("A")], top_k=1)

    assert excinfo.value.failure_class == "rate_limit"


def make_chunk(chunk_id: str) -> ScoredChunk:
    text_by_id = {
        "A": "Use explicit request ids in logs.",
        "B": "Rotate session tokens promptly.",
    }
    return ScoredChunk(
        chunk_id=chunk_id,
        path=f"policies/example/{chunk_id.lower()}.md",
        section="Rules",
        lines="1-2",
        text=text_by_id[chunk_id],
        policy=PolicyMetadata(
            policy_id=f"{chunk_id}-1",
            title="Logging" if chunk_id == "A" else "Tokens",
            doc_type="guidance",
            domain="backend" if chunk_id == "A" else "security",
        ),
        score=0.1,
    )
