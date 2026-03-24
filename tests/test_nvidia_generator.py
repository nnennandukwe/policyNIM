"""Tests for the NVIDIA grounded generator adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from policynim.errors import ConfigurationError, ProviderError
from policynim.providers.nvidia import NVIDIAGenerator
from policynim.types import PolicyMetadata, PreflightRequest, ScoredChunk


class MockChatCompletions:
    """Deterministic chat client stub."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
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


def test_generator_parses_json_and_keeps_chunk_ids_only() -> None:
    client = MockOpenAIClient(
        """
        {
          "summary": "Use explicit request ids in logs.",
          "applicable_policies": [
            {
              "policy_id": "BACKEND-LOG-001",
              "title": "Logging",
              "rationale": "The chunk says to log with context.",
              "citation_ids": ["BACKEND-1"]
            }
          ],
          "implementation_guidance": ["Thread request ids through the job."],
          "review_flags": ["Avoid unstructured logging."],
          "tests_required": ["Add a logging regression test."],
          "citation_ids": ["BACKEND-1"],
          "insufficient_context": false
        }
        """
    )
    generator = NVIDIAGenerator(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=client,  # type: ignore[arg-type]
    )

    result = generator.generate_preflight(
        PreflightRequest(task="add request ids to backend logs", top_k=3),
        [make_chunk()],
    )

    assert result.summary == "Use explicit request ids in logs."
    assert result.applicable_policies[0].citation_ids == ["BACKEND-1"]
    assert result.citation_ids == ["BACKEND-1"]
    assert not result.insufficient_context


def test_generator_rejects_invalid_json() -> None:
    generator = NVIDIAGenerator(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=MockOpenAIClient("not json"),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderError, match="invalid JSON"):
        generator.generate_preflight(PreflightRequest(task="task"), [make_chunk()])


def test_generator_extracts_json_from_reasoning_wrappers() -> None:
    generator = NVIDIAGenerator(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=MockOpenAIClient(
            '<think>reasoning</think>{"summary":"ok","citation_ids":["BACKEND-1"]}'
        ),  # type: ignore[arg-type]
    )

    result = generator.generate_preflight(PreflightRequest(task="task"), [make_chunk()])

    assert result.summary == "ok"
    assert result.citation_ids == ["BACKEND-1"]


def test_generator_rejects_json_missing_required_summary() -> None:
    generator = NVIDIAGenerator(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=MockOpenAIClient('{"citation_ids":["BACKEND-1"]}'),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderError, match="malformed JSON"):
        generator.generate_preflight(PreflightRequest(task="task"), [make_chunk()])


def test_generator_rejects_json_with_invalid_citation_shape() -> None:
    generator = NVIDIAGenerator(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=MockOpenAIClient(
            '{"summary":"ok","citation_ids":"BACKEND-1","applicable_policies":[]}'
        ),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderError, match="malformed JSON"):
        generator.generate_preflight(PreflightRequest(task="task"), [make_chunk()])


def test_generator_requires_api_key() -> None:
    with pytest.raises(ConfigurationError, match="NVIDIA_API_KEY"):
        NVIDIAGenerator(
            api_key="   ",
            model="mock-model",
            base_url="https://example.invalid/v1",
            timeout_seconds=1,
            max_retries=0,
        )


def make_chunk() -> ScoredChunk:
    return ScoredChunk(
        chunk_id="BACKEND-1",
        path="policies/backend/logging.md",
        section="Logging > Rules",
        lines="5-8",
        text="Use request ids in backend logs.",
        policy=PolicyMetadata(
            policy_id="BACKEND-LOG-001",
            title="Logging",
            doc_type="guidance",
            domain="backend",
        ),
        score=0.99,
    )
