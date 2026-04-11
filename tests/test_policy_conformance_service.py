"""Tests for policy conformance scoring."""

from __future__ import annotations

from policynim.services.conformance import PolicyConformanceService
from policynim.types import (
    Citation,
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    GeneratedPolicyConformanceDraft,
    PolicyConformanceRequest,
    PolicyConformanceTraceStep,
    PolicyGuidance,
    PreflightResult,
)


class MockConformanceEvaluator:
    """Static external evaluator double."""

    def __init__(self, draft: GeneratedPolicyConformanceDraft | None = None) -> None:
        self._draft = draft or GeneratedPolicyConformanceDraft(
            final_adherence_score=0.95,
            final_adherence_rationale="Final output follows the constraints.",
            trajectory_adherence_score=None,
        )
        self.closed = False

    def evaluate_policy_conformance(
        self,
        request: PolicyConformanceRequest,
    ) -> GeneratedPolicyConformanceDraft:
        assert request.task == "fix backend logging"
        return self._draft

    def close(self) -> None:
        self.closed = True


def test_conformance_service_scores_compiled_constraints_against_result() -> None:
    service = PolicyConformanceService(evaluator=MockConformanceEvaluator())

    result = service.evaluate(make_request(), backend="nemo")

    assert result.passed
    assert result.backend == "nemo"
    assert result.final_adherence_score == 0.95
    assert {metric.name: metric.passed for metric in result.metrics} == {
        "plan_completeness": True,
        "guidance_coverage": True,
        "test_coverage": True,
        "forbidden_pattern_handling": True,
        "citation_support": True,
        "final_adherence": True,
    }


def test_conformance_service_reports_missing_required_and_forbidden_pattern_failures() -> None:
    service = PolicyConformanceService(evaluator=MockConformanceEvaluator())
    request = make_request(
        result=make_result(
            plan_steps=[],
            implementation_guidance=["Never log token values."],
            review_flags=[],
        )
    )

    result = service.evaluate(request, backend="nemo")

    assert not result.passed
    assert any("required steps missing" in reason for reason in result.failure_reasons)
    assert any("missing from review_flags" in reason for reason in result.failure_reasons)
    assert any("positive guidance" in reason for reason in result.failure_reasons)


def test_conformance_service_skips_trajectory_metric_when_evaluator_omits_it() -> None:
    service = PolicyConformanceService(evaluator=MockConformanceEvaluator())

    result = service.evaluate(make_request(trace_steps=[]), backend="nemo")

    assert result.trajectory_adherence_score is None
    assert "trajectory_adherence" not in {metric.name for metric in result.metrics}


def test_conformance_service_fails_closed_for_insufficient_compiled_packet() -> None:
    service = PolicyConformanceService(evaluator=None)
    request = make_request(
        compiled_packet=make_compiled_packet(insufficient_context=True),
    )

    result = service.evaluate(request, backend="nemo")

    assert not result.passed
    assert len(result.metrics) == 1
    assert result.metrics[0].name == "compiled_context"
    assert result.metrics[0].score == 0.0
    assert result.metrics[0].failure_reasons == ["compiled policy packet has insufficient context"]


def test_conformance_service_closes_owned_evaluator() -> None:
    evaluator = MockConformanceEvaluator()
    service = PolicyConformanceService(evaluator=evaluator)

    service.close()

    assert evaluator.closed is True


def make_request(
    *,
    result: PreflightResult | None = None,
    compiled_packet: CompiledPolicyPacket | None = None,
    trace_steps: list[PolicyConformanceTraceStep] | None = None,
) -> PolicyConformanceRequest:
    return PolicyConformanceRequest(
        task="fix backend logging",
        result=result or make_result(),
        compiled_packet=compiled_packet or make_compiled_packet(),
        trace_steps=trace_steps
        if trace_steps is not None
        else [
            PolicyConformanceTraceStep(
                step_id="compile",
                kind="policy_compilation",
                summary="Compiled policy constraints.",
                citation_ids=["BACKEND-1", "SECURITY-1"],
            )
        ],
    )


def make_compiled_packet(*, insufficient_context: bool = False) -> CompiledPolicyPacket:
    return CompiledPolicyPacket(
        task="fix backend logging",
        top_k=2,
        task_type="bug_fix",
        required_steps=[
            CompiledPolicyConstraint(
                statement="Thread request ids through log context.",
                citation_ids=["BACKEND-1"],
                source_policy_ids=["BACKEND-LOG-001"],
            )
        ],
        forbidden_patterns=[
            CompiledPolicyConstraint(
                statement="Never log token values.",
                citation_ids=["SECURITY-1"],
                source_policy_ids=["SECURITY-TOKEN-001"],
            )
        ],
        architectural_expectations=[
            CompiledPolicyConstraint(
                statement="Keep logging changes in the backend service layer.",
                citation_ids=["BACKEND-1"],
                source_policy_ids=["BACKEND-LOG-001"],
            )
        ],
        test_expectations=[
            CompiledPolicyConstraint(
                statement="Add a regression test for token redaction.",
                citation_ids=["SECURITY-1"],
                source_policy_ids=["SECURITY-TOKEN-001"],
            )
        ],
        style_constraints=[
            CompiledPolicyConstraint(
                statement="Use explicit request-id naming.",
                citation_ids=["BACKEND-1"],
                source_policy_ids=["BACKEND-LOG-001"],
            )
        ],
        citations=[
            make_citation("BACKEND-1", "BACKEND-LOG-001"),
            make_citation("SECURITY-1", "SECURITY-TOKEN-001"),
        ],
        insufficient_context=insufficient_context,
    )


def make_result(
    *,
    plan_steps: list[str] | None = None,
    implementation_guidance: list[str] | None = None,
    review_flags: list[str] | None = None,
) -> PreflightResult:
    return PreflightResult(
        task="fix backend logging",
        summary="Use request ids and keep tokens out of logs.",
        applicable_policies=[
            PolicyGuidance(
                policy_id="BACKEND-LOG-001",
                title="Backend Logging",
                rationale="Request ids keep logs traceable.",
                citation_ids=["BACKEND-1"],
            )
        ],
        plan_steps=plan_steps
        if plan_steps is not None
        else ["Thread request ids through log context."],
        implementation_guidance=implementation_guidance
        if implementation_guidance is not None
        else [
            "Keep logging changes in the backend service layer.",
            "Use explicit request-id naming.",
        ],
        review_flags=review_flags
        if review_flags is not None
        else ["Avoid: Never log token values."],
        tests_required=["Add a regression test for token redaction."],
        citations=[
            make_citation("BACKEND-1", "BACKEND-LOG-001"),
            make_citation("SECURITY-1", "SECURITY-TOKEN-001"),
        ],
    )


def make_citation(chunk_id: str, policy_id: str) -> Citation:
    return Citation(
        policy_id=policy_id,
        title=policy_id,
        path=f"policies/{policy_id}.md",
        section="Rules",
        lines="1-4",
        chunk_id=chunk_id,
    )
