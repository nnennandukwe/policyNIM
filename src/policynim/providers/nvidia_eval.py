"""Optional NVIDIA evaluation package adapters for policy conformance."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from typing import Protocol

from policynim.errors import ConfigurationError
from policynim.providers.nvidia import NVIDIAPolicyConformanceEvaluator
from policynim.settings import Settings
from policynim.types import GeneratedPolicyConformanceDraft, PolicyConformanceRequest


class _ClosablePolicyConformanceEvaluator(Protocol):
    def evaluate_policy_conformance(
        self,
        request: PolicyConformanceRequest,
    ) -> GeneratedPolicyConformanceDraft:
        """Evaluate policy conformance for a preflight request."""
        ...

    def close(self) -> None:
        """Release evaluator resources."""
        ...


class NeMoEvaluatorPolicyConformanceEvaluator:
    """Policy conformance adapter gated on NeMo Evaluator SDK packages."""

    def __init__(self, *, evaluator: _ClosablePolicyConformanceEvaluator) -> None:
        _require_optional_distributions(
            ["nemo-evaluator", "nvidia-simple-evals"],
            install_hint="uv sync --extra nvidia-eval",
            backend="nemo_evaluator",
        )
        self._evaluator = evaluator

    @classmethod
    def from_settings(cls, settings: Settings) -> NeMoEvaluatorPolicyConformanceEvaluator:
        """Construct a NeMo Evaluator backed conformance evaluator from settings."""
        evaluator = NVIDIAPolicyConformanceEvaluator.from_settings(settings)
        try:
            return cls(evaluator=evaluator)
        except Exception:
            evaluator.close()
            raise

    def evaluate_policy_conformance(
        self,
        request: PolicyConformanceRequest,
    ) -> GeneratedPolicyConformanceDraft:
        """Evaluate final adherence through the configured NVIDIA judge endpoint."""
        return self._evaluator.evaluate_policy_conformance(request)

    def close(self) -> None:
        """Release owned evaluator resources."""
        self._evaluator.close()


class NeMoAgentToolkitPolicyConformanceEvaluator:
    """Policy conformance adapter gated on NeMo Agent Toolkit eval packages."""

    def __init__(self, *, evaluator: _ClosablePolicyConformanceEvaluator) -> None:
        _require_optional_distributions(
            ["nvidia-nat-eval"],
            install_hint="uv sync --extra nvidia-eval",
            backend="nat",
        )
        self._evaluator = evaluator

    @classmethod
    def from_settings(cls, settings: Settings) -> NeMoAgentToolkitPolicyConformanceEvaluator:
        """Construct a NeMo Agent Toolkit backed conformance evaluator from settings."""
        evaluator = NVIDIAPolicyConformanceEvaluator.from_settings(settings)
        try:
            return cls(evaluator=evaluator)
        except Exception:
            evaluator.close()
            raise

    def evaluate_policy_conformance(
        self,
        request: PolicyConformanceRequest,
    ) -> GeneratedPolicyConformanceDraft:
        """Evaluate trajectory-aware conformance through the configured NVIDIA judge endpoint."""
        return self._evaluator.evaluate_policy_conformance(request)

    def close(self) -> None:
        """Release owned evaluator resources."""
        self._evaluator.close()


def _require_optional_distributions(
    distribution_names: list[str],
    *,
    install_hint: str,
    backend: str,
) -> None:
    missing: list[str] = []
    for distribution_name in distribution_names:
        try:
            installed_version(distribution_name)
        except PackageNotFoundError:
            missing.append(distribution_name)
    if missing:
        raise ConfigurationError(
            f"Eval backend '{backend}' requires optional packages: "
            f"{', '.join(missing)}. Install them with `{install_hint}`.",
            failure_class="missing_optional_dependency",
        )


__all__ = [
    "NeMoAgentToolkitPolicyConformanceEvaluator",
    "NeMoEvaluatorPolicyConformanceEvaluator",
]
