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
    """Return one policy passage for provider request and citation assertions."""
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
    operation: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    model: str | None = None,
    strict_response: bool = False,
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
            _strict_response_validation=strict_response,
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
    """Verify provider failure contract."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the mocked HTTP request and return the response required by this case."""
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
    """Verify embedding wire contract and response order."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the mocked HTTP request and return the response required by this case."""
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
    """Verify embedding rejects malformed response without retry."""
    calls = []

    def handler(request):
        """Capture the mocked HTTP request and return the response required by this case."""
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
    """Verify replacement reranker wire contract."""
    calls = []

    def handler(request):
        """Capture the mocked HTTP request and return the response required by this case."""
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


@pytest.mark.parametrize("body", ["private-not-json", '"private-string"', "null", "{}"])
def test_embedding_rejects_invalid_response_envelopes_without_retry(body):
    """Classify SDK decoding and missing response envelopes without exposing the body."""
    calls = []

    def handler(request):
        """Return a malformed successful response through the real SDK transport."""
        calls.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    with pytest.raises(ProviderError) as caught:
        invoke("query", handler)
    assert caught.value.failure_class == "invalid_response"
    assert "private" not in str(caught.value)
    assert len(calls) == 1


def test_sdk_response_validation_failure_is_sanitized_and_not_retried():
    """Preserve invalid-response classification when the SDK rejects a response itself."""
    from openai import APIResponseValidationError

    calls = []

    def handler(request):
        """Return invalid embedding data to a strict real OpenAI client."""
        calls.append(request)
        return httpx.Response(200, json={"data": "private-response-sentinel"})

    with pytest.raises(ProviderError) as caught:
        invoke("query", handler, strict_response=True)
    assert isinstance(caught.value.__cause__, APIResponseValidationError)
    assert caught.value.failure_class == "invalid_response"
    assert "sentinel" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("model", [CHAT_MODEL, "custom/chat-model"])
def test_chat_options_are_specific_to_replacement_model(model):
    """Verify chat options are specific to replacement model."""
    calls = []

    def handler(request):
        """Capture the mocked HTTP request and return the response required by this case."""
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
    system_prompt, user_prompt = [message["content"] for message in calls[0]["messages"]]
    assert "A policy_id is not a citation" in system_prompt
    allowed_ids = user_prompt.split("Allowed citation_ids (copy exactly):\n", 1)[1].split("\n", 1)[
        0
    ]
    assert json.loads(allowed_ids) == [candidate().chunk_id]
    if model == CHAT_MODEL:
        assert calls[0]["temperature"] == 1
        assert calls[0]["top_p"] == 0.95
        assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    else:
        assert calls[0]["temperature"] == 0
        assert calls[0]["top_p"] == 1
        assert "chat_template_kwargs" not in calls[0]


def test_default_models_and_environment_overrides(monkeypatch):
    """Verify default models and environment overrides."""
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
    """Verify model overrides are normalized consistently."""
    options: dict[str, Any] = {**NO_ENV, field: " custom/model "}
    assert getattr(Settings(**options), field) == "custom/model"
    options[field] = "custom/model\ninvalid"
    with pytest.raises(ValueError):
        Settings(**options)


@pytest.mark.parametrize(
    "adapter_name",
    [
        "NVIDIAEmbedder",
        "NVIDIAReranker",
        "NVIDIAGenerator",
        "NVIDIAPolicyCompiler",
        "NVIDIAPolicyConformanceEvaluator",
    ],
)
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.invalid/v1",
        "https://exa mple.invalid/v1",
        "https://exa%20mple.invalid/v1",
        "https://exam\nple.invalid/v1",
        "https://example.invalid\\other/v1",
    ],
)
def test_invalid_endpoints_fail_before_client_construction(monkeypatch, adapter_name, endpoint):
    """Verify credentialed http endpoints fail before client construction."""
    import policynim.providers.nvidia as module

    calls = []

    def client_must_not_be_created(**kwargs):
        """Fail if invalid endpoint configuration reaches client construction."""
        calls.append(True)
        raise AssertionError("client construction reached")

    monkeypatch.setattr(module, "OpenAI", client_must_not_be_created)
    monkeypatch.setattr(module.httpx, "Client", client_must_not_be_created)
    kwargs = dict(
        api_key="credential-sentinel",
        model="custom/model",
        base_url=endpoint,
        timeout_seconds=1,
        max_retries=0,
    )
    if adapter_name == "NVIDIAEmbedder":
        kwargs["batch_size"] = 1
    with pytest.raises(ConfigurationError, match="HTTPS") as caught:
        getattr(module, adapter_name)(**kwargs)
    assert not calls
    assert "sentinel" not in str(caught.value)


@pytest.mark.parametrize("model", [RERANK_MODEL, "custom/reranker"])
def test_replacement_reranker_converts_documented_logits_only_for_selected_model(model):
    """Translate a zero logit to its documented probability without changing custom scores."""

    def handler(request):
        """Return one zero logit through the real HTTP response parser."""
        return httpx.Response(200, json={"rankings": [{"index": 0, "logit": 0.0}]})

    score = invoke("reranking", handler, model=model)[0].score
    assert score == (0.5 if model == RERANK_MODEL else 0.0)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -float("inf")])
def test_reranker_rejects_nonfinite_scores_without_retry(score):
    """Do not convert malformed scores into confident-looking ranking results."""
    calls = []

    def handler(request):
        """Return deliberately invalid JSON numeric values without a network call."""
        calls.append(request)
        return httpx.Response(200, content=json.dumps({"rankings": [{"index": 0, "logit": score}]}))

    with pytest.raises(ProviderError) as caught:
        invoke("reranking", handler)
    assert caught.value.failure_class == "invalid_response"
    assert len(calls) == 1


def test_negative_nvidia_logits_reach_policy_compilation_routing(tmp_path):
    """Exercise the observed hosted failure through real SQLite and mocked NVIDIA HTTP."""
    from policynim.services.router import PolicyRouterService
    from policynim.storage import create_index_store
    from policynim.types import EmbeddedChunk, RouteRequest

    class QueryEmbedder:
        """Provide the same deterministic vector space as the real test index."""

        def embed_query(self, text):
            """Return the known vector for the relevant logging passage."""
            return [1.0, 0.0]

        def embed_documents(self, texts):
            """Return vectors in the same test space for protocol compatibility."""
            return [[1.0, 0.0] for _ in texts]

        def close(self):
            """Release the resource-free test embedder."""

    store = create_index_store(Settings(**NO_ENV, index_db_path=tmp_path / "index.sqlite3"))
    store.replace([EmbeddedChunk(**candidate().model_dump(exclude={"score"}), vector=[1.0, 0.0])])

    def handler(request):
        """Replay a negative logit, as the documented model and hosted probe returned."""
        return httpx.Response(200, json={"rankings": [{"index": 0, "logit": -2.783203125}]})

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://example.invalid/v1"
    ) as http:
        reranker = NVIDIAReranker(
            api_key="test",
            model=RERANK_MODEL,
            base_url="https://example.invalid/v1",
            timeout_seconds=1,
            max_retries=0,
            client=http,
        )
        with PolicyRouterService(
            embedder=QueryEmbedder(), index_store=store, reranker=reranker
        ) as router:
            routed = router.route(RouteRequest(task="Add request IDs to backend logs.", top_k=1))
    assert not routed.packet.insufficient_context
    assert [chunk.chunk_id for chunk in routed.retained_context] == [candidate().chunk_id]
    retained_score = routed.retained_context[0].score
    assert retained_score is not None and 0 < retained_score < 0.5


@pytest.mark.parametrize("logits", [(900.0, 1000.0), (-1000.0, -900.0)])
def test_reranker_preserves_logit_order_when_probabilities_saturate(logits):
    """Keep ranking deterministic at both floating-point saturation boundaries."""

    def handler(request):
        """Return distinct finite logits whose sigmoid probabilities coincide."""
        return httpx.Response(
            200,
            json={
                "rankings": [{"index": index, "logit": score} for index, score in enumerate(logits)]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://example.invalid/v1"
    ) as http:
        reranker = NVIDIAReranker(
            api_key="test",
            model=RERANK_MODEL,
            base_url="https://example.invalid/v1",
            timeout_seconds=1,
            max_retries=0,
            client=http,
        )
        chunks = [candidate().model_copy(update={"chunk_id": name}) for name in ("lower", "higher")]
        ranked = reranker.rerank("request IDs", chunks, top_k=2)
    assert [chunk.chunk_id for chunk in ranked] == ["higher", "lower"]
    assert all(chunk.score is not None and 0 <= chunk.score <= 1 for chunk in ranked)


@pytest.mark.parametrize("field", ["nvidia_base_url", "nvidia_retrieval_base_url"])
@pytest.mark.parametrize(
    "endpoint",
    [
        "https://exa mple.invalid/v1",
        "https://exa%20mple.invalid/v1",
        "https://exam\nple.invalid/v1",
        "https://example.invalid\\other/v1",
    ],
)
def test_settings_and_identity_reject_malformed_provider_endpoints(field, endpoint):
    """Reject malformed provider hosts consistently before persisting or using them."""
    from policynim.types import EmbeddingIdentity

    with pytest.raises(ValueError):
        Settings(**{**NO_ENV, field: endpoint})
    with pytest.raises(ValueError):
        EmbeddingIdentity(model="test/model", endpoint=endpoint)


@pytest.mark.parametrize(
    ("endpoint", "canonical"),
    [
        ("https://EXAMPLE.invalid:443/v1/", "https://example.invalid/v1"),
        ("https://example.invalid:8443/v1", "https://example.invalid:8443/v1"),
        ("https://[2001:db8::1]:8443/v1/", "https://[2001:db8::1]:8443/v1"),
    ],
)
def test_valid_custom_endpoint_identity_matches_settings(endpoint, canonical):
    """Preserve custom TLS hosts and ports, including IPv6, across both consumers."""
    from policynim.types import EmbeddingIdentity

    assert Settings(**NO_ENV, nvidia_base_url=endpoint).nvidia_base_url == canonical
    assert EmbeddingIdentity(model="custom/model", endpoint=endpoint).endpoint == canonical
