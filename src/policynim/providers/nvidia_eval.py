"""Optional NVIDIA evaluation package adapters for policy conformance."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from typing import ClassVar, Protocol, Self

from policynim.errors import ConfigurationError
from policynim.providers.nvidia import NVIDIAPolicyConformanceEvaluator
from policynim.settings import Settings
from policynim.types import GeneratedPolicyConformanceDraft, PolicyConformanceRequest

_NEMO_EVALUATOR_DISTRIBUTIONS = ["nemo-evaluator", "nvidia-simple-evals"]
_NEMO_EVALUATOR_BACKEND = "nemo_evaluator"
_NAT_DISTRIBUTIONS = ["nvidia-nat-eval"]
_NAT_BACKEND = "nat"
_NVIDIA_EVAL_INSTALL_HINT = "uv sync --extra nvidia-eval"


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


class _OptionalPolicyConformanceEvaluatorAdapter:
    """Common optional-package wrapper for NVIDIA policy conformance evaluators."""

    distribution_names: ClassVar[tuple[str, ...]]
    backend: ClassVar[str]

    def __init__(self, *, evaluator: _ClosablePolicyConformanceEvaluator) -> None:
        try:
            _require_optional_distributions(
                self.distribution_names,
                install_hint=_NVIDIA_EVAL_INSTALL_HINT,
                backend=self.backend,
            )
        except ConfigurationError:
            evaluator.close()
            raise
        self._evaluator = evaluator

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        _require_optional_distributions(
            cls.distribution_names,
            install_hint=_NVIDIA_EVAL_INSTALL_HINT,
            backend=cls.backend,
        )
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
        return self._evaluator.evaluate_policy_conformance(request)

    def close(self) -> None:
        self._evaluator.close()


class NeMoEvaluatorPolicyConformanceEvaluator(_OptionalPolicyConformanceEvaluatorAdapter):
    """Policy conformance adapter gated on NeMo Evaluator SDK packages."""

    distribution_names = tuple(_NEMO_EVALUATOR_DISTRIBUTIONS)
    backend = _NEMO_EVALUATOR_BACKEND


class NeMoAgentToolkitPolicyConformanceEvaluator(_OptionalPolicyConformanceEvaluatorAdapter):
    """Policy conformance adapter gated on NeMo Agent Toolkit eval packages."""

    distribution_names = tuple(_NAT_DISTRIBUTIONS)
    backend = _NAT_BACKEND


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
