"""Policy conformance scoring for traced preflight results."""

from __future__ import annotations

import re
from collections.abc import Sequence
from types import TracebackType

from policynim.contracts import PolicyConformanceEvaluator
from policynim.types import (
    CompiledPolicyConstraint,
    EvalBackend,
    GeneratedPolicyConformanceDraft,
    PolicyConformanceMetric,
    PolicyConformanceRequest,
    PolicyConformanceResult,
)

_FINAL_ADHERENCE_THRESHOLD = 0.75
_TRAJECTORY_ADHERENCE_THRESHOLD = 0.75


class PolicyConformanceService:
    """Score policy conformance for one traced preflight result."""

    def __init__(self, *, evaluator: PolicyConformanceEvaluator | None = None) -> None:
        self._evaluator = evaluator

    def __enter__(self) -> PolicyConformanceService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release owned evaluator resources."""
        _close_component(self._evaluator)

    def evaluate(
        self,
        request: PolicyConformanceRequest,
        *,
        backend: EvalBackend,
    ) -> PolicyConformanceResult:
        """Score deterministic conformance and optional evaluator judgment."""
        deterministic_metrics = _deterministic_metrics(request)
        evaluator_draft = (
            self._evaluator.evaluate_policy_conformance(request)
            if self._evaluator is not None
            else None
        )
        metrics = list(deterministic_metrics)
        if evaluator_draft is not None:
            metrics.extend(_judge_metrics(evaluator_draft))

        passed = all(metric.passed for metric in metrics)
        overall_score = _average([metric.score for metric in metrics])
        failure_reasons = [reason for metric in metrics for reason in metric.failure_reasons]
        return PolicyConformanceResult(
            backend=backend,
            passed=passed,
            overall_score=overall_score,
            metrics=metrics,
            final_adherence_score=(
                evaluator_draft.final_adherence_score if evaluator_draft is not None else None
            ),
            final_adherence_rationale=(
                evaluator_draft.final_adherence_rationale if evaluator_draft is not None else None
            ),
            trajectory_adherence_score=(
                evaluator_draft.trajectory_adherence_score if evaluator_draft is not None else None
            ),
            trajectory_adherence_rationale=(
                evaluator_draft.trajectory_adherence_rationale
                if evaluator_draft is not None
                else None
            ),
            failure_reasons=failure_reasons,
        )


def _deterministic_metrics(
    request: PolicyConformanceRequest,
) -> list[PolicyConformanceMetric]:
    compiled_packet = request.compiled_packet
    if compiled_packet.insufficient_context:
        return [
            PolicyConformanceMetric(
                name="compiled_context",
                score=0.0,
                passed=False,
                failure_reasons=["compiled policy packet has insufficient context"],
            )
        ]

    return [
        _constraint_presence_metric(
            name="plan_completeness",
            constraints=compiled_packet.required_steps,
            texts=request.result.plan_steps,
            missing_label="required steps missing from plan_steps",
        ),
        _constraint_presence_metric(
            name="guidance_coverage",
            constraints=[
                *compiled_packet.architectural_expectations,
                *compiled_packet.style_constraints,
            ],
            texts=request.result.implementation_guidance,
            missing_label="architecture or style constraints missing from implementation guidance",
        ),
        _constraint_presence_metric(
            name="test_coverage",
            constraints=compiled_packet.test_expectations,
            texts=request.result.tests_required,
            missing_label="test expectations missing from tests_required",
        ),
        _forbidden_pattern_metric(
            constraints=compiled_packet.forbidden_patterns,
            review_flags=request.result.review_flags,
            positive_texts=[
                *request.result.plan_steps,
                *request.result.implementation_guidance,
                *request.result.tests_required,
            ],
        ),
        _citation_support_metric(request),
    ]


def _constraint_presence_metric(
    *,
    name: str,
    constraints: Sequence[CompiledPolicyConstraint],
    texts: Sequence[str],
    missing_label: str,
) -> PolicyConformanceMetric:
    if not constraints:
        return PolicyConformanceMetric(name=name, score=1.0, passed=True)

    matched = [
        constraint for constraint in constraints if _contains_statement(constraint.statement, texts)
    ]
    score = len(matched) / len(constraints)
    failure_reasons = []
    if score < 1.0:
        missing = [constraint.statement for constraint in constraints if constraint not in matched]
        failure_reasons.append(f"{missing_label}: {', '.join(missing)}")
    return PolicyConformanceMetric(
        name=name,
        score=score,
        passed=score == 1.0,
        failure_reasons=failure_reasons,
    )


def _forbidden_pattern_metric(
    *,
    constraints: Sequence[CompiledPolicyConstraint],
    review_flags: Sequence[str],
    positive_texts: Sequence[str],
) -> PolicyConformanceMetric:
    if not constraints:
        return PolicyConformanceMetric(name="forbidden_pattern_handling", score=1.0, passed=True)

    handled = 0
    failure_reasons: list[str] = []
    for constraint in constraints:
        in_review_flags = _contains_statement(constraint.statement, review_flags)
        in_positive_text = _contains_statement(constraint.statement, positive_texts)
        if in_review_flags and not in_positive_text:
            handled += 1
            continue
        if not in_review_flags:
            failure_reasons.append(
                f"forbidden pattern missing from review_flags: {constraint.statement}"
            )
        if in_positive_text:
            failure_reasons.append(
                f"forbidden pattern appears as positive guidance: {constraint.statement}"
            )

    score = handled / len(constraints)
    return PolicyConformanceMetric(
        name="forbidden_pattern_handling",
        score=score,
        passed=score == 1.0,
        failure_reasons=failure_reasons,
    )


def _citation_support_metric(
    request: PolicyConformanceRequest,
) -> PolicyConformanceMetric:
    expected_chunk_ids = _ordered_unique(
        [
            citation_id
            for constraints in (
                request.compiled_packet.required_steps,
                request.compiled_packet.forbidden_patterns,
                request.compiled_packet.architectural_expectations,
                request.compiled_packet.test_expectations,
                request.compiled_packet.style_constraints,
            )
            for constraint in constraints
            for citation_id in constraint.citation_ids
        ]
    )
    actual_chunk_ids = _ordered_unique([citation.chunk_id for citation in request.result.citations])
    if not expected_chunk_ids:
        return PolicyConformanceMetric(
            name="citation_support",
            score=0.0,
            passed=False,
            failure_reasons=["compiled packet has no constraint citations"],
        )
    if not actual_chunk_ids:
        return PolicyConformanceMetric(
            name="citation_support",
            score=0.0,
            passed=False,
            failure_reasons=["preflight result has no citations"],
        )

    actual_chunk_id_set = set(actual_chunk_ids)
    matched = [chunk_id for chunk_id in expected_chunk_ids if chunk_id in actual_chunk_id_set]
    score = len(matched) / len(expected_chunk_ids)
    failure_reasons = []
    if score < 1.0:
        missing = [chunk_id for chunk_id in expected_chunk_ids if chunk_id not in set(matched)]
        failure_reasons.append(f"missing compiled citation ids: {', '.join(missing)}")

    return PolicyConformanceMetric(
        name="citation_support",
        score=score,
        passed=score == 1.0,
        failure_reasons=failure_reasons,
    )


def _judge_metrics(
    draft: GeneratedPolicyConformanceDraft,
) -> list[PolicyConformanceMetric]:
    final_failure_reasons = list(draft.failure_reasons)
    final_passed = draft.final_adherence_score >= _FINAL_ADHERENCE_THRESHOLD
    if not final_passed and not final_failure_reasons:
        final_failure_reasons.append(
            f"final adherence score below threshold: {draft.final_adherence_score:.2f}"
        )

    metrics = [
        PolicyConformanceMetric(
            name="final_adherence",
            score=draft.final_adherence_score,
            passed=final_passed and not draft.failure_reasons,
            failure_reasons=final_failure_reasons,
        )
    ]
    if draft.trajectory_adherence_score is not None:
        trajectory_passed = draft.trajectory_adherence_score >= _TRAJECTORY_ADHERENCE_THRESHOLD
        trajectory_failure_reasons = []
        if not trajectory_passed:
            trajectory_failure_reasons.append(
                "trajectory adherence score below threshold: "
                f"{draft.trajectory_adherence_score:.2f}"
            )
        metrics.append(
            PolicyConformanceMetric(
                name="trajectory_adherence",
                score=draft.trajectory_adherence_score,
                passed=trajectory_passed,
                failure_reasons=trajectory_failure_reasons,
            )
        )
    return metrics


def _contains_statement(statement: str, texts: Sequence[str]) -> bool:
    normalized_statement = _normalize_for_matching(statement)
    if not normalized_statement:
        return False
    return any(normalized_statement in _normalize_for_matching(text) for text in texts)


def _normalize_for_matching(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _ordered_unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _average(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _close_component(component: object | None) -> None:
    close = getattr(component, "close", None)
    if callable(close):
        close()


__all__ = [
    "PolicyConformanceService",
]
