"""Tests for the NVIDIA policy conformance evaluator adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openai import RateLimitError

from policynim.errors import ConfigurationError, ProviderError
from policynim.providers.nvidia import NVIDIAPolicyConformanceEvaluator
from policynim.types import (
    Citation,
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    PolicyConformanceRequest,
    PolicyConformanceTraceStep,
    PolicyGuidance,
    PreflightResult,
)


class MockChatCompletions:
    """Deterministic chat client stub."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content),
                )
            ]
        )


class MockOpenAIClient:
    """OpenAI client stub with the minimum chat surface."""

    def __init__(self, content: str) -> None:
        self.chat = SimpleNamespace(completions=MockChatCompletions(content))
        self.closed = False

    def close(self) -> None:
        self.closed = True


class MockRateLimitError(RateLimitError):
    """Minimal rate-limit error subclass for provider classification tests."""

    def __init__(self) -> None:
        Exception.__init__(self, "too many requests")
        self.status_code = 429


class RaisingChatCompletions:
    """Chat completions stub that always raises the supplied exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def create(self, **kwargs):  # noqa: ANN003
        raise self._exc


class RaisingOpenAIClient:
    """OpenAI client stub that always fails during chat completion."""

    def __init__(self, exc: Exception) -> None:
        self.chat = SimpleNamespace(completions=RaisingChatCompletions(exc))


def test_policy_conformance_evaluator_parses_grounded_json() -> None:
    client = MockOpenAIClient(
        """
        {
          "final_adherence_score": 0.93,
          "final_adherence_rationale": "The output follows the compiled constraints.",
          "trajectory_adherence_score": 0.88,
          "trajectory_adherence_rationale": "The trace preserved compile and generation steps.",
          "constraint_ids": ["required_steps:0"],
          "chunk_ids": ["BACKEND-1"],
          "failure_reasons": []
        }
        """
    )
    evaluator = make_evaluator(client)

    result = evaluator.evaluate_policy_conformance(make_request())

    assert result.final_adherence_score == 0.93
    assert result.trajectory_adherence_score == 0.88
    assert result.constraint_ids == ["required_steps:0"]
    assert result.chunk_ids == ["BACKEND-1"]


def test_policy_conformance_prompt_includes_allowed_ids() -> None:
    client = MockOpenAIClient(
        '{"final_adherence_score":1,"constraint_ids":["required_steps:0"],"chunk_ids":["BACKEND-1"]}'
    )
    evaluator = make_evaluator(client)

    evaluator.evaluate_policy_conformance(make_request())

    messages = client.chat.completions.calls[0]["messages"]
    prompt_text = "\n".join(str(message["content"]) for message in messages)  # type: ignore[index]
    assert "required_steps:0" in prompt_text
    assert "BACKEND-1" in prompt_text
    assert "Thread request ids through log context." in prompt_text


def test_policy_conformance_extracts_json_from_reasoning_wrappers() -> None:
    evaluator = make_evaluator(
        MockOpenAIClient(
            '<think>reasoning</think>{"final_adherence_score":1,'
            '"constraint_ids":["required_steps:0"],"chunk_ids":["BACKEND-1"]}'
        )
    )

    result = evaluator.evaluate_policy_conformance(make_request())

    assert result.final_adherence_score == 1.0


def test_policy_conformance_rejects_invalid_json_and_preserves_cause() -> None:
    evaluator = make_evaluator(MockOpenAIClient("not json"))

    with pytest.raises(ProviderError, match="invalid JSON") as excinfo:
        evaluator.evaluate_policy_conformance(make_request())

    assert excinfo.value.failure_class == "invalid_response"
    assert excinfo.value.__cause__ is not None


def test_policy_conformance_rejects_invalid_draft_shape() -> None:
    evaluator = make_evaluator(MockOpenAIClient('{"final_adherence_score":"high"}'))

    with pytest.raises(ProviderError, match="malformed JSON") as excinfo:
        evaluator.evaluate_policy_conformance(make_request())

    assert excinfo.value.failure_class == "invalid_response"


def test_policy_conformance_rejects_unsupported_constraint_ids() -> None:
    evaluator = make_evaluator(
        MockOpenAIClient(
            '{"final_adherence_score":1,"constraint_ids":["unknown:0"],"chunk_ids":["BACKEND-1"]}'
        )
    )

    with pytest.raises(ProviderError, match="unsupported constraint ids") as excinfo:
        evaluator.evaluate_policy_conformance(make_request())

    assert excinfo.value.failure_class == "invalid_response"


def test_policy_conformance_rejects_unsupported_chunk_ids() -> None:
    evaluator = make_evaluator(
        MockOpenAIClient(
            '{"final_adherence_score":1,"constraint_ids":["required_steps:0"],'
            '"chunk_ids":["UNKNOWN"]}'
        )
    )

    with pytest.raises(ProviderError, match="unsupported chunk ids") as excinfo:
        evaluator.evaluate_policy_conformance(make_request())

    assert excinfo.value.failure_class == "invalid_response"


def test_policy_conformance_classifies_upstream_rate_limits() -> None:
    evaluator = make_evaluator(RaisingOpenAIClient(MockRateLimitError()))

    with pytest.raises(ProviderError, match="failed after retries") as excinfo:
        evaluator.evaluate_policy_conformance(make_request())

    assert excinfo.value.failure_class == "rate_limit"


def test_policy_conformance_requires_api_key() -> None:
    with pytest.raises(ConfigurationError, match="NVIDIA_API_KEY"):
        NVIDIAPolicyConformanceEvaluator(
            api_key="   ",
            model="mock-model",
            base_url="https://example.invalid/v1",
            timeout_seconds=1,
            max_retries=0,
        )


def test_policy_conformance_close_leaves_injected_client_open() -> None:
    client = MockOpenAIClient('{"final_adherence_score":1}')
    evaluator = make_evaluator(client)

    evaluator.close()

    assert client.closed is False


def make_evaluator(client) -> NVIDIAPolicyConformanceEvaluator:
    return NVIDIAPolicyConformanceEvaluator(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=client,  # type: ignore[arg-type]
    )


def make_request() -> PolicyConformanceRequest:
    return PolicyConformanceRequest(
        task="fix backend logging",
        result=PreflightResult(
            task="fix backend logging",
            summary="Use request ids.",
            applicable_policies=[
                PolicyGuidance(
                    policy_id="BACKEND-LOG-001",
                    title="Backend Logging",
                    rationale="Request ids keep logs traceable.",
                    citation_ids=["BACKEND-1"],
                )
            ],
            plan_steps=["Thread request ids through log context."],
            citations=[make_citation("BACKEND-1")],
        ),
        compiled_packet=CompiledPolicyPacket(
            task="fix backend logging",
            top_k=1,
            task_type="bug_fix",
            required_steps=[
                CompiledPolicyConstraint(
                    statement="Thread request ids through log context.",
                    citation_ids=["BACKEND-1"],
                    source_policy_ids=["BACKEND-LOG-001"],
                )
            ],
            citations=[make_citation("BACKEND-1")],
        ),
        trace_steps=[
            PolicyConformanceTraceStep(
                step_id="compile",
                kind="policy_compilation",
                summary="Compiled policy constraints.",
                citation_ids=["BACKEND-1"],
            )
        ],
    )


def make_citation(chunk_id: str) -> Citation:
    return Citation(
        policy_id="BACKEND-LOG-001",
        title="Backend Logging",
        path="policies/backend/logging.md",
        section="Rules",
        lines="1-4",
        chunk_id=chunk_id,
    )
