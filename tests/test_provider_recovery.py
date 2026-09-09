"""Offline wire contracts for replacement NVIDIA endpoints and recovery errors."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from openai import OpenAI

from policynim.errors import ConfigurationError, ProviderError
from policynim.providers.nvidia import NVIDIAEmbedder, NVIDIAGenerator, NVIDIAReranker
from policynim.settings import Settings
from policynim.types import PolicyMetadata, PreflightRequest, ScoredChunk

NO_ENV: dict[str, Any] = {"_env_file": None}

EMBED_MODEL = "nvidia/nemotron-3-embed-1b"
RERANK_MODEL = "nvidia/llama-nemotron-rerank-vl-1b-v2"
CHAT_MODEL = "nvidia/nemotron-3-super-120b-a12b"


def candidate() -> ScoredChunk:
    return ScoredChunk(
        chunk_id="BE-LOG-001:rules",
        path="policies/backend/backend-logging-standard.md",
        section="Rules",
        lines="1-3",
        text="Include request identifiers in logs.",
        policy=PolicyMetadata(
            policy_id="BE-LOG-001", title="Logging", doc_type="guidance", domain="backend"
        ),
    )


def invoke(
    operation: str, handler: Callable[[httpx.Request], httpx.Response], *, model: str | None = None
) -> Any:
    """Exercise real SDK serialization and HTTP exception mapping without network access."""
    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://ai.api.nvidia.com/v1/retrieval",
    ) as http:
        if operation == "reranking":
            adapter = NVIDIAReranker(
                api_key="sentinel-key",
                model=model or RERANK_MODEL,
                base_url="https://ai.api.nvidia.com/v1/retrieval",
                timeout_seconds=1,
                max_retries=2,
                client=http,
            )
            return adapter.rerank("request identifiers", [candidate()], top_k=1)
        with OpenAI(
            api_key="sentinel-key",
            base_url="https://integrate.api.nvidia.com/v1",
            max_retries=0,
            http_client=http,
        ) as client:
            if operation == "generation":
                generator = NVIDIAGenerator(
                    api_key="sentinel-key",
                    model=model or CHAT_MODEL,
                    base_url="https://integrate.api.nvidia.com/v1",
                    timeout_seconds=1,
                    max_retries=2,
                    client=client,
                )
                return generator.generate_preflight(
                    PreflightRequest(task="add request identifiers to logs"), [candidate()]
                )
            embedder = NVIDIAEmbedder(
                api_key="sentinel-key",
                model=model or EMBED_MODEL,
                base_url="https://integrate.api.nvidia.com/v1",
                batch_size=32,
                timeout_seconds=1,
                max_retries=2,
                client=client,
            )
            if operation == "query":
                return embedder.embed_query("request identifiers")
            return embedder.embed_documents(["first passage", "second passage"])


@pytest.mark.parametrize("operation", ["embeddings", "reranking", "generation"])
@pytest.mark.parametrize(
    ("status", "failure_class", "attempts"),
    [
        (410, "endpoint_unavailable", 1),
        (400, "bad_request", 1),
        (401, "auth", 1),
        (403, "auth", 1),
        (429, "rate_limit", 3),
        (503, "http_status", 3),
    ],
)
def test_provider_failure_contract(operation: str, status: int, failure_class: str, attempts: int):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "sentinel-private-response"}})

    with pytest.raises((ProviderError, ConfigurationError)) as caught:
        invoke(operation, handler)

    assert len(calls) == attempts
    expected_class = "http_status" if operation == "reranking" and status == 400 else failure_class
    assert caught.value.failure_class == expected_class
    assert "sentinel" not in str(caught.value)
    if status == 410:
        assert operation in str(caught.value)
        setting = {"embeddings": "EMBED", "reranking": "RERANK", "generation": "CHAT"}[operation]
        assert f"POLICYNIM_NVIDIA_{setting}_MODEL" in str(caught.value)
        assert "retry" in str(caught.value).lower()


@pytest.mark.parametrize("operation", ["embeddings", "query"])
def test_embedding_wire_contract_and_response_order(operation: str):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), json.loads(request.content)))
        count = len(calls[-1][1]["input"])
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": EMBED_MODEL,
                "data": [
                    {"object": "embedding", "index": i, "embedding": [float(i), 1.0]}
                    for i in reversed(range(count))
                ],
            },
        )

    result = invoke(operation, handler)
    assert calls == [
        (
            "https://integrate.api.nvidia.com/v1/embeddings",
            {
                "model": EMBED_MODEL,
                "input": ["request identifiers"]
                if operation == "query"
                else ["first passage", "second passage"],
                "input_type": "query" if operation == "query" else "passage",
                "encoding_format": "float",
                "truncate": "NONE",
            },
        )
    ]
    assert result == ([0.0, 1.0] if operation == "query" else [[0.0, 1.0], [1.0, 1.0]])


@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 0, "embedding": []}],
        [{"index": 1, "embedding": [1.0]}],
        [{"index": 0, "embedding": ["invalid"]}],
        [{"index": 0, "embedding": [1e309]}],
    ],
)
def test_embedding_rejects_malformed_response_without_retry(data):
    calls = []

    def handler(request):
        calls.append(request)
        # Raw JSON allows the non-finite sentinel to reach the adapter's validator.
        return httpx.Response(
            200,
            content=json.dumps({"data": data, "model": EMBED_MODEL}),
            headers={"content-type": "application/json"},
        )

    with pytest.raises(ProviderError) as caught:
        invoke("query", handler)
    assert caught.value.failure_class == "invalid_response"
    assert len(calls) == 1


def test_replacement_reranker_wire_contract():
    calls = []

    def handler(request):
        calls.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"rankings": [{"index": 0, "logit": 0.8}]})

    assert invoke("reranking", handler)[0].chunk_id == candidate().chunk_id
    assert calls == [
        (
            f"https://ai.api.nvidia.com/v1/retrieval/{RERANK_MODEL}/reranking",
            {
                "model": RERANK_MODEL,
                "query": {"text": "request identifiers"},
                "passages": [{"text": candidate().text}],
                "truncate": "END",
            },
        )
    ]


@pytest.mark.parametrize("model", [CHAT_MODEL, "custom/chat-model"])
def test_chat_options_are_specific_to_replacement_model(model):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "summary": "Add request identifiers.",
                                    "citation_ids": [candidate().chunk_id],
                                }
                            ),
                        },
                    }
                ],
            },
        )

    assert invoke("generation", handler, model=model).citation_ids == [candidate().chunk_id]
    assert calls[0]["model"] == model
    if model == CHAT_MODEL:
        assert calls[0]["temperature"] == 1
        assert calls[0]["top_p"] == 0.95
        assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    else:
        assert calls[0]["temperature"] == 0
        assert calls[0]["top_p"] == 1
        assert "chat_template_kwargs" not in calls[0]


def test_default_models_and_environment_overrides(monkeypatch):
    for suffix in ["CHAT", "EMBED", "RERANK"]:
        monkeypatch.delenv(f"POLICYNIM_NVIDIA_{suffix}_MODEL", raising=False)
    settings = Settings(**NO_ENV)
    assert (
        settings.nvidia_embed_model,
        settings.nvidia_rerank_model,
        settings.nvidia_chat_model,
    ) == (
        EMBED_MODEL,
        RERANK_MODEL,
        CHAT_MODEL,
    )
    monkeypatch.setenv("POLICYNIM_NVIDIA_EMBED_MODEL", "custom/embedding")
    assert Settings(**NO_ENV).nvidia_embed_model == "custom/embedding"
    assert (
        Settings(**NO_ENV, nvidia_embed_model="explicit/model").nvidia_embed_model
        == "explicit/model"
    )


@pytest.mark.parametrize(
    "field", ["nvidia_embed_model", "nvidia_rerank_model", "nvidia_chat_model"]
)
def test_model_overrides_are_normalized_consistently(field):
    options: dict[str, Any] = {**NO_ENV, field: " custom/model "}
    assert getattr(Settings(**options), field) == "custom/model"
    options[field] = "custom/model\ninvalid"
    with pytest.raises(ValueError):
        Settings(**options)
