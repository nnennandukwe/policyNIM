"""Tests for the NVIDIA reranker adapter."""

from __future__ import annotations

import httpx

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

    def post(self, endpoint: str, json: dict[str, object]) -> SpyResponse:
        self.calls.append({"endpoint": endpoint, "json": json})
        return SpyResponse(self._payload)


def test_reranker_posts_to_relative_endpoint() -> None:
    client = SpyRerankClient({"scores": [0.2, 0.9]})
    reranker = NVIDIAReranker(
        api_key="test-key",
        model="fake-model",
        base_url="https://example.invalid/v1/retrieval",
        timeout_seconds=1,
        max_retries=0,
        client=client,  # type: ignore[arg-type]
    )

    reranked = reranker.rerank("request ids", [make_chunk("A"), make_chunk("B")], top_k=2)

    assert client.calls == [
        {
            "endpoint": "fake-model/reranking",
            "json": {
                "model": "fake-model",
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
        model="fake-model",
        base_url="https://unused.invalid",
        timeout_seconds=1,
        max_retries=0,
        client=client,
    )

    try:
        reranker.rerank("request ids", [make_chunk("A")], top_k=1)
    finally:
        client.close()

    assert seen_urls == ["https://example.invalid/v1/retrieval/fake-model/reranking"]


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
