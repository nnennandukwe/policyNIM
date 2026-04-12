"""Tests for the optional NeMo Agent Toolkit conformance adapter."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import pytest

from policynim.errors import ConfigurationError
from policynim.providers.nvidia_eval import NeMoAgentToolkitPolicyConformanceEvaluator
from policynim.settings import Settings
from policynim.types import (
    CompiledPolicyPacket,
    GeneratedPolicyConformanceDraft,
    PolicyConformanceRequest,
    PreflightResult,
)


def test_nat_adapter_requires_optional_eval_package(monkeypatch) -> None:
    def missing_distribution(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    monkeypatch.setattr("policynim.providers.nvidia_eval.installed_version", missing_distribution)
    evaluator = FakeEvaluator()

    with pytest.raises(ConfigurationError, match="uv sync --extra nvidia-eval"):
        NeMoAgentToolkitPolicyConformanceEvaluator(evaluator=evaluator)

    assert evaluator.closed is True


def test_nat_from_settings_checks_optional_package_first(monkeypatch) -> None:
    constructed: list[bool] = []

    def missing_distribution(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    def fake_from_settings(settings: Settings) -> FakeEvaluator:
        constructed.append(True)
        return FakeEvaluator()

    monkeypatch.setattr("policynim.providers.nvidia_eval.installed_version", missing_distribution)
    monkeypatch.setattr(
        "policynim.providers.nvidia_eval.NVIDIAPolicyConformanceEvaluator.from_settings",
        fake_from_settings,
    )

    with pytest.raises(ConfigurationError, match="uv sync --extra nvidia-eval"):
        NeMoAgentToolkitPolicyConformanceEvaluator.from_settings(Settings())

    assert constructed == []


def test_nat_adapter_accepts_nat_eval_package_and_delegates(monkeypatch) -> None:
    checked: list[str] = []

    def fake_installed_version(distribution_name: str) -> str:
        checked.append(distribution_name)
        return "1.0"

    monkeypatch.setattr("policynim.providers.nvidia_eval.installed_version", fake_installed_version)
    evaluator = FakeEvaluator()
    adapter = NeMoAgentToolkitPolicyConformanceEvaluator(evaluator=evaluator)

    result = adapter.evaluate_policy_conformance(make_request())

    assert checked == ["nvidia-nat-eval"]
    assert result.trajectory_adherence_score == 0.88
    assert evaluator.calls == 1
    adapter.close()
    assert evaluator.closed is True


class FakeEvaluator:
    """Static NVIDIA judge double."""

    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def evaluate_policy_conformance(
        self,
        request: PolicyConformanceRequest,
    ) -> GeneratedPolicyConformanceDraft:
        self.calls += 1
        return GeneratedPolicyConformanceDraft(
            final_adherence_score=0.9,
            final_adherence_rationale=f"Judged {request.task}.",
            trajectory_adherence_score=0.88,
            trajectory_adherence_rationale="Trace followed the workflow.",
        )

    def close(self) -> None:
        self.closed = True


def make_request() -> PolicyConformanceRequest:
    return PolicyConformanceRequest(
        task="fix backend logging",
        result=PreflightResult(
            task="fix backend logging",
            summary="Use request ids in backend logs.",
            insufficient_context=True,
        ),
        compiled_packet=CompiledPolicyPacket(
            task="fix backend logging",
            top_k=1,
            task_type="bug_fix",
            insufficient_context=True,
        ),
    )
