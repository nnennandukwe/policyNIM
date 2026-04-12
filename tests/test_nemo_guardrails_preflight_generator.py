"""Tests for the optional NeMo Guardrails preflight generator wrapper."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace

import pytest

import policynim.providers.nvidia_guardrails as guardrails_module
from policynim.errors import ConfigurationError, ProviderError
from policynim.providers.nvidia_guardrails import (
    NeMoGuardrailsPreflightGenerator,
)
from policynim.settings import Settings
from policynim.types import (
    CompiledPolicyPacket,
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    PolicyMetadata,
    PreflightRequest,
    PreflightResult,
    RegenerationContext,
    ScoredChunk,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_provider_import_does_not_import_guardrails_package() -> None:
    script = f"""
import importlib.abc
import sys

sys.path.insert(0, {str(PROJECT_ROOT / "src")!r})

class BlockGuardrails(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "nemoguardrails" or fullname.startswith("nemoguardrails."):
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockGuardrails())

import policynim.providers
from policynim.providers import NeMoGuardrailsPreflightGenerator

assert NeMoGuardrailsPreflightGenerator.__name__ == "NeMoGuardrailsPreflightGenerator"
    """
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_guardrails_generator_requires_optional_package(monkeypatch) -> None:
    def missing_distribution(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    monkeypatch.setattr(guardrails_module, "installed_version", missing_distribution)

    with pytest.raises(ConfigurationError, match="nvidia-guardrails") as excinfo:
        NeMoGuardrailsPreflightGenerator(base_generator=FakeGenerator([make_draft()]))

    assert excinfo.value.failure_class == "missing_optional_dependency"


def test_guardrails_from_settings_checks_optional_package_before_base_generator(
    monkeypatch,
) -> None:
    constructed: list[bool] = []

    def missing_distribution(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    def fake_from_settings(settings: Settings) -> FakeGenerator:
        constructed.append(True)
        return FakeGenerator([make_draft()])

    monkeypatch.setattr(guardrails_module, "installed_version", missing_distribution)
    monkeypatch.setattr(
        guardrails_module.NVIDIAGenerator,
        "from_settings",
        fake_from_settings,
    )

    with pytest.raises(ConfigurationError, match="nvidia-guardrails"):
        NeMoGuardrailsPreflightGenerator.from_settings(Settings(nvidia_api_key="test-key"))

    assert constructed == []


def test_guardrails_assets_are_packaged_and_model_scoped() -> None:
    colang_content, yaml_content = guardrails_module._load_guardrails_assets(
        model="nvidia/test-model"
    )

    assert "refuse to respond" in colang_content
    assert "self check output" in yaml_content
    assert "nvidia/test-model" in yaml_content
    assert "POLICYNIM_NVIDIA_CHAT_MODEL" not in yaml_content


def test_guardrails_generator_returns_valid_checked_draft() -> None:
    checked_draft = make_draft(summary="Checked preflight.")
    rails = FakeRails([rail_result(status="passed", content=draft_json(checked_draft))])
    generator = NeMoGuardrailsPreflightGenerator(
        base_generator=FakeGenerator([make_draft()]),
        rails=rails,
        rail_type_output="output",
    )

    result = generator.generate_preflight(make_request(), [make_chunk()])

    assert result.summary == "Checked preflight."
    assert result.citation_ids == ["BACKEND-1"]
    assert rails.calls[0].messages[0]["role"] == "assistant"
    assert rails.calls[0].rail_types == ["output"]


def test_guardrails_generator_rejects_missing_required_fields() -> None:
    rails = FakeRails([rail_result(status="passed", content='{"citation_ids":["BACKEND-1"]}')])
    generator = NeMoGuardrailsPreflightGenerator(
        base_generator=FakeGenerator([make_draft()]),
        rails=rails,
    )

    with pytest.raises(ProviderError, match="malformed preflight draft") as excinfo:
        generator.generate_preflight(make_request(), [make_chunk()])

    assert excinfo.value.failure_class == "invalid_response"


def test_guardrails_generator_rejects_invalid_json() -> None:
    rails = FakeRails([rail_result(status="passed", content="not json")])
    generator = NeMoGuardrailsPreflightGenerator(
        base_generator=FakeGenerator([make_draft()]),
        rails=rails,
    )

    with pytest.raises(ProviderError, match="invalid JSON") as excinfo:
        generator.generate_preflight(make_request(), [make_chunk()])

    assert excinfo.value.failure_class == "invalid_response"


def test_guardrails_generator_rejects_unsupported_citations() -> None:
    rails = FakeRails(
        [rail_result(status="modified", content=draft_json(make_draft(citation_ids=["UNKNOWN-1"])))]
    )
    generator = NeMoGuardrailsPreflightGenerator(
        base_generator=FakeGenerator([make_draft()]),
        rails=rails,
    )

    with pytest.raises(ProviderError, match="unsupported citation ids") as excinfo:
        generator.generate_preflight(make_request(), [make_chunk()])

    assert excinfo.value.failure_class == "invalid_response"


def test_guardrails_generator_fails_closed_when_rails_block() -> None:
    rails = FakeRails([rail_result(status="blocked", content=draft_json(make_draft()))])
    base_generator = FakeGenerator([make_draft()])
    generator = NeMoGuardrailsPreflightGenerator(
        base_generator=base_generator,
        rails=rails,
    )

    with pytest.raises(ProviderError, match="blocked") as excinfo:
        generator.generate_preflight(make_request(), [make_chunk()])

    assert excinfo.value.failure_class == "guardrails_blocked"
    assert len(base_generator.calls) == 1


def test_guardrails_generator_preserves_rails_exception_chain() -> None:
    raised = RuntimeError("rail runtime failed")
    generator = NeMoGuardrailsPreflightGenerator(
        base_generator=FakeGenerator([make_draft()]),
        rails=RaisingRails(raised),
    )

    with pytest.raises(ProviderError, match="output rail execution failed") as excinfo:
        generator.generate_preflight(make_request(), [make_chunk()])

    assert excinfo.value.failure_class == "guardrails_execution"
    assert excinfo.value.__cause__ is raised


def test_guardrails_generator_passes_regeneration_context_unchanged() -> None:
    base_generator = FakeGenerator([make_draft()])
    rails = FakeRails([rail_result(status="passed", content=draft_json(make_draft()))])
    generator = NeMoGuardrailsPreflightGenerator(
        base_generator=base_generator,
        rails=rails,
    )
    regeneration_context = make_regeneration_context()

    generator.generate_preflight(
        make_request(),
        [make_chunk()],
        compiled_packet=make_compiled_packet(),
        regeneration_context=regeneration_context,
    )

    assert base_generator.calls[0].regeneration_context is regeneration_context
    assert base_generator.calls[0].compiled_packet == make_compiled_packet()


class GenerateCall:
    """Captured generator call."""

    def __init__(
        self,
        *,
        request: PreflightRequest,
        context: Sequence[ScoredChunk],
        compiled_packet: CompiledPolicyPacket | None,
        regeneration_context: RegenerationContext | None,
    ) -> None:
        self.request = request
        self.context = list(context)
        self.compiled_packet = compiled_packet
        self.regeneration_context = regeneration_context


class FakeGenerator:
    """Static generator double."""

    def __init__(self, drafts: list[GeneratedPreflightDraft]) -> None:
        self._drafts = drafts
        self.calls: list[GenerateCall] = []
        self.closed = False

    def generate_preflight(
        self,
        request: PreflightRequest,
        context: Sequence[ScoredChunk],
        *,
        compiled_packet: CompiledPolicyPacket | None = None,
        regeneration_context: RegenerationContext | None = None,
    ) -> GeneratedPreflightDraft:
        self.calls.append(
            GenerateCall(
                request=request,
                context=context,
                compiled_packet=compiled_packet,
                regeneration_context=regeneration_context,
            )
        )
        return self._drafts[min(len(self.calls) - 1, len(self._drafts) - 1)]

    def close(self) -> None:
        self.closed = True


class RailCall:
    """Captured output-rail call."""

    def __init__(
        self,
        *,
        messages: list[dict[str, str]],
        rail_types: Sequence[object] | None,
    ) -> None:
        self.messages = messages
        self.rail_types = list(rail_types) if rail_types is not None else None


class FakeRails:
    """Static Guardrails double."""

    def __init__(self, results: list[object]) -> None:
        self._results = results
        self.calls: list[RailCall] = []
        self.closed = False

    def check(
        self,
        messages: list[dict[str, str]],
        rail_types: Sequence[object] | None = None,
    ) -> object:
        self.calls.append(RailCall(messages=messages, rail_types=rail_types))
        return self._results[min(len(self.calls) - 1, len(self._results) - 1)]

    def close(self) -> None:
        self.closed = True


class RaisingRails:
    """Guardrails double that raises during output checks."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def check(
        self,
        messages: list[dict[str, str]],
        rail_types: Sequence[object] | None = None,
    ) -> object:
        raise self._exc


def rail_result(*, status: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(status=status, content=content)


def draft_json(draft: GeneratedPreflightDraft) -> str:
    return json.dumps(draft.model_dump(mode="json"))


def make_request() -> PreflightRequest:
    return PreflightRequest(task="fix backend logging", top_k=2)


def make_chunk(chunk_id: str = "BACKEND-1") -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        path="policies/backend/logging.md",
        section="Rules",
        lines="1-4",
        text="Thread request ids through log context.",
        policy=PolicyMetadata(
            policy_id="BACKEND-LOG-001",
            title="Backend Logging",
            doc_type="guidance",
            domain="backend",
        ),
        score=0.99,
    )


def make_draft(
    *,
    summary: str = "Use request ids in backend logs.",
    citation_ids: list[str] | None = None,
) -> GeneratedPreflightDraft:
    resolved_citation_ids = citation_ids if citation_ids is not None else ["BACKEND-1"]
    return GeneratedPreflightDraft(
        summary=summary,
        applicable_policies=[
            GeneratedPolicyGuidance(
                policy_id="BACKEND-LOG-001",
                title="Backend Logging",
                rationale="Request ids keep backend logs traceable.",
                citation_ids=resolved_citation_ids,
            )
        ],
        plan_steps=["Thread request ids through log context."],
        implementation_guidance=["Keep logging changes in the service layer."],
        review_flags=[],
        tests_required=["Add a regression test for request-id logging."],
        citation_ids=resolved_citation_ids,
    )


def make_compiled_packet() -> CompiledPolicyPacket:
    return CompiledPolicyPacket(
        task="fix backend logging",
        top_k=2,
        task_type="bug_fix",
        insufficient_context=False,
    )


def make_regeneration_context() -> RegenerationContext:
    return RegenerationContext(
        attempt_index=1,
        max_regenerations=1,
        compiled_packet_id="packet-1",
        previous_result=PreflightResult(
            task="fix backend logging",
            summary="Previous preflight.",
            insufficient_context=True,
        ),
        triggers=[],
    )
