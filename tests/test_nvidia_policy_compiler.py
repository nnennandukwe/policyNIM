"""Tests for the NVIDIA policy compiler adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openai import RateLimitError

from policynim.errors import ConfigurationError, ProviderError
from policynim.providers.nvidia import NVIDIAPolicyCompiler
from policynim.types import (
    CompileRequest,
    PolicyMetadata,
    PolicySelectionPacket,
    ScoredChunk,
    SelectedPolicy,
    SelectedPolicyEvidence,
)


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


def test_policy_compiler_parses_grounded_constraint_json() -> None:
    client = MockOpenAIClient(
        """
        {
          "required_steps": [
            {
              "statement": "Thread request ids through backend log context.",
              "citation_ids": ["BACKEND-1"]
            }
          ],
          "forbidden_patterns": [
            {
              "statement": "Do not log token values.",
              "citation_ids": ["SECURITY-1"]
            }
          ],
          "architectural_expectations": [],
          "test_expectations": [],
          "style_constraints": [],
          "insufficient_context": false
        }
        """
    )
    compiler = make_compiler(client)

    result = compiler.compile_policy_packet(
        CompileRequest(task="fix backend logging bug", top_k=2),
        make_selection_packet(),
        make_context(),
    )

    assert result.required_steps[0].statement == "Thread request ids through backend log context."
    assert result.required_steps[0].citation_ids == ["BACKEND-1"]
    assert result.forbidden_patterns[0].citation_ids == ["SECURITY-1"]
    assert not result.insufficient_context


def test_policy_compiler_prompt_includes_allowed_chunk_ids() -> None:
    client = MockOpenAIClient(
        '{"required_steps":[{"statement":"Use request ids.","citation_ids":["BACKEND-1"]}]}'
    )
    compiler = make_compiler(client)

    compiler.compile_policy_packet(
        CompileRequest(task="fix backend logging bug", top_k=2),
        make_selection_packet(),
        make_context(),
    )

    messages = client.chat.completions.calls[0]["messages"]
    prompt_text = "\n".join(str(message["content"]) for message in messages)  # type: ignore[index]
    assert "BACKEND-1" in prompt_text
    assert "SECURITY-1" in prompt_text
    assert "policy_id" in prompt_text


def test_policy_compiler_extracts_json_from_reasoning_wrappers() -> None:
    compiler = make_compiler(
        MockOpenAIClient(
            '<think>reasoning</think>{"required_steps":[{"statement":"Use request ids.",'
            '"citation_ids":["BACKEND-1"]}]}'
        )
    )

    result = compiler.compile_policy_packet(
        CompileRequest(task="task", top_k=1),
        make_selection_packet(),
        make_context(),
    )

    assert result.required_steps[0].citation_ids == ["BACKEND-1"]


def test_policy_compiler_rejects_invalid_json_and_preserves_cause() -> None:
    compiler = make_compiler(MockOpenAIClient("not json"))

    with pytest.raises(ProviderError, match="invalid JSON") as excinfo:
        compiler.compile_policy_packet(
            CompileRequest(task="task", top_k=1),
            make_selection_packet(),
            make_context(),
        )

    assert excinfo.value.failure_class == "invalid_response"
    assert excinfo.value.__cause__ is not None


def test_policy_compiler_rejects_invalid_constraint_shape() -> None:
    compiler = make_compiler(
        MockOpenAIClient(
            '{"required_steps":[{"statement":"Use request ids.","citation_ids":"BACKEND-1"}]}'
        )
    )

    with pytest.raises(ProviderError, match="malformed JSON") as excinfo:
        compiler.compile_policy_packet(
            CompileRequest(task="task", top_k=1),
            make_selection_packet(),
            make_context(),
        )

    assert excinfo.value.failure_class == "invalid_response"


def test_policy_compiler_classifies_upstream_rate_limits() -> None:
    compiler = make_compiler(RaisingOpenAIClient(MockRateLimitError()))

    with pytest.raises(ProviderError, match="failed after retries") as excinfo:
        compiler.compile_policy_packet(
            CompileRequest(task="task", top_k=1),
            make_selection_packet(),
            make_context(),
        )

    assert excinfo.value.failure_class == "rate_limit"


def test_policy_compiler_preserves_empty_response_failure_class() -> None:
    compiler = make_compiler(MockOpenAIClient("   "))

    with pytest.raises(ProviderError, match="empty response") as excinfo:
        compiler.compile_policy_packet(
            CompileRequest(task="task", top_k=1),
            make_selection_packet(),
            make_context(),
        )

    assert excinfo.value.failure_class == "invalid_response"


def test_policy_compiler_requires_api_key() -> None:
    with pytest.raises(ConfigurationError, match="NVIDIA_API_KEY"):
        NVIDIAPolicyCompiler(
            api_key="   ",
            model="mock-model",
            base_url="https://example.invalid/v1",
            timeout_seconds=1,
            max_retries=0,
        )


def test_policy_compiler_close_leaves_injected_client_open() -> None:
    client = MockOpenAIClient('{"required_steps":[]}')
    compiler = make_compiler(client)

    compiler.close()

    assert client.closed is False


def make_compiler(client) -> NVIDIAPolicyCompiler:
    return NVIDIAPolicyCompiler(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=client,  # type: ignore[arg-type]
    )


def make_selection_packet() -> PolicySelectionPacket:
    return PolicySelectionPacket(
        task="fix backend logging bug",
        domain=None,
        top_k=2,
        task_type="bug_fix",
        profile_signals=["fix", "bug"],
        selected_policies=[make_selected_policy(chunk) for chunk in make_context()],
        insufficient_context=False,
    )


def make_selected_policy(chunk: ScoredChunk) -> SelectedPolicy:
    return SelectedPolicy(
        policy_id=chunk.policy.policy_id,
        title=chunk.policy.title,
        domain=chunk.policy.domain,
        reason="Selected for compiler tests.",
        evidence=[
            SelectedPolicyEvidence(
                chunk_id=chunk.chunk_id,
                path=chunk.path,
                section=chunk.section,
                lines=chunk.lines,
                text=chunk.text,
                score=chunk.score,
            )
        ],
    )


def make_context() -> list[ScoredChunk]:
    return [
        make_chunk(
            chunk_id="BACKEND-1",
            policy_id="BACKEND-LOG-001",
            title="Backend Logging",
            domain="backend",
            text="Use request ids in backend logs.",
        ),
        make_chunk(
            chunk_id="SECURITY-1",
            policy_id="SECURITY-TOKEN-001",
            title="Token Handling",
            domain="security",
            text="Never log token values.",
        ),
    ]


def make_chunk(
    *,
    chunk_id: str,
    policy_id: str,
    title: str,
    domain: str,
    text: str,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        path=f"policies/{domain}/policy.md",
        section="Rules",
        lines="1-4",
        text=text,
        policy=PolicyMetadata(
            policy_id=policy_id,
            title=title,
            doc_type="guidance",
            domain=domain,
        ),
        score=0.99,
    )
