"""Tests for policy-backed preflight regeneration."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from policynim.errors import ProviderError
from policynim.services.evidence_trace import compiled_policy_packet_id
from policynim.services.regeneration import (
    PolicyRegenerationService,
    regeneration_triggers_from_conformance,
)
from policynim.types import (
    Citation,
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    CompileRequest,
    CompileResult,
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    PolicyConformanceMetric,
    PolicyConformanceRequest,
    PolicyConformanceResult,
    PolicyMetadata,
    PreflightRegenerationRequest,
    PreflightRequest,
    RegenerationContext,
    ScoredChunk,
)


def test_regeneration_compiles_once_and_retries_from_typed_triggers() -> None:
    packet = make_compiled_packet()
    compiler = FakeCompilerService(packet=packet, context=[make_chunk()])
    generator = FakeGenerator(
        [
            make_draft(summary="Initial preflight."),
            make_draft(summary="Regenerated preflight."),
        ]
    )
    conformance = FakeConformanceService(
        [
            make_conformance_result(
                passed=False,
                metrics=[
                    PolicyConformanceMetric(
                        name="plan_completeness",
                        score=0.0,
                        passed=False,
                        failure_reasons=["required step was missing"],
                    )
                ],
            ),
            make_conformance_result(passed=True),
        ]
    )
    service = PolicyRegenerationService(
        compiler_service=compiler,
        generator=generator,
        conformance_service=conformance,
    )

    result = service.regenerate(
        PreflightRegenerationRequest(
            task="fix backend logging",
            top_k=2,
            backend="nemo",
            max_regenerations=1,
        )
    )

    expected_packet_id = compiled_policy_packet_id(packet)
    assert result.passed is True
    assert result.stop_reason == "passed"
    assert result.compiled_packet_id == expected_packet_id
    assert result.final_result.summary == "Regenerated preflight."
    assert compiler.calls == 1
    assert len(generator.calls) == 2
    assert generator.calls[0].regeneration_context is None
    retry_context = generator.calls[1].regeneration_context
    assert retry_context is not None
    assert retry_context.compiled_packet_id == expected_packet_id
    assert retry_context.triggers[0].kind == "required_steps"
    assert retry_context.triggers[0].constraint_ids == ["required_steps:0"]
    assert retry_context.triggers[0].chunk_ids == ["BACKEND-1"]
    assert [attempt.compiled_packet_id for attempt in result.attempts] == [
        expected_packet_id,
        expected_packet_id,
    ]
    assert result.evidence_trace.compiled_packet_id == expected_packet_id
    assert conformance.backends == ["nemo", "nemo"]


def test_regeneration_stops_at_max_regenerations() -> None:
    service = PolicyRegenerationService(
        compiler_service=FakeCompilerService(packet=make_compiled_packet(), context=[make_chunk()]),
        generator=FakeGenerator([make_draft(), make_draft(summary="Retry still failing.")]),
        conformance_service=FakeConformanceService(
            [
                make_conformance_result(
                    passed=False,
                    metrics=[
                        PolicyConformanceMetric(
                            name="test_coverage",
                            score=0.0,
                            passed=False,
                            failure_reasons=["test expectation was missing"],
                        )
                    ],
                ),
                make_conformance_result(
                    passed=False,
                    metrics=[
                        PolicyConformanceMetric(
                            name="test_coverage",
                            score=0.0,
                            passed=False,
                            failure_reasons=["test expectation was still missing"],
                        )
                    ],
                ),
            ]
        ),
    )

    result = service.regenerate(
        PreflightRegenerationRequest(
            task="fix backend logging",
            top_k=2,
            max_regenerations=1,
        )
    )

    assert result.passed is False
    assert result.stop_reason == "max_regenerations"
    assert len(result.attempts) == 2
    assert result.attempts[1].triggers[0].kind == "test_expectations"


def test_regeneration_stops_when_failed_metrics_have_no_material_trigger() -> None:
    service = PolicyRegenerationService(
        compiler_service=FakeCompilerService(packet=make_compiled_packet(), context=[make_chunk()]),
        generator=FakeGenerator([make_draft()]),
        conformance_service=FakeConformanceService(
            [
                make_conformance_result(
                    passed=False,
                    metrics=[
                        PolicyConformanceMetric(
                            name="unknown_metric",
                            score=0.0,
                            passed=False,
                            failure_reasons=["not actionable"],
                        )
                    ],
                )
            ]
        ),
    )

    result = service.regenerate(PreflightRegenerationRequest(task="fix backend logging", top_k=2))

    assert result.passed is False
    assert result.stop_reason == "no_material_trigger"
    assert len(result.attempts) == 1


def test_regeneration_treats_insufficient_compiled_context_as_terminal() -> None:
    compiler = FakeCompilerService(
        packet=make_compiled_packet(insufficient_context=True),
        context=[],
    )
    generator = FakeGenerator([make_draft()])
    conformance = FakeConformanceService([make_conformance_result(passed=True)])
    service = PolicyRegenerationService(
        compiler_service=compiler,
        generator=generator,
        conformance_service=conformance,
    )

    result = service.regenerate(PreflightRegenerationRequest(task="unknown task", top_k=2))

    assert result.passed is False
    assert result.stop_reason == "insufficient_context"
    assert result.final_result.insufficient_context is True
    assert generator.calls == []
    assert conformance.requests == []


def test_regeneration_fails_closed_for_provider_errors_without_retry() -> None:
    compiler = FakeCompilerService(packet=make_compiled_packet(), context=[make_chunk()])
    generator = FakeGenerator([make_draft(), make_draft(summary="Should not run.")])
    conformance = FakeConformanceService([ProviderError("judge failed", failure_class="timeout")])
    service = PolicyRegenerationService(
        compiler_service=compiler,
        generator=generator,
        conformance_service=conformance,
    )

    with pytest.raises(ProviderError, match="judge failed"):
        service.regenerate(PreflightRegenerationRequest(task="fix backend logging", top_k=2))

    assert compiler.calls == 1
    assert len(generator.calls) == 1
    assert len(conformance.requests) == 1


def test_regeneration_rejects_citation_drift_as_insufficient_context() -> None:
    conformance = FakeConformanceService([make_conformance_result(passed=True)])
    service = PolicyRegenerationService(
        compiler_service=FakeCompilerService(packet=make_compiled_packet(), context=[make_chunk()]),
        generator=FakeGenerator([make_draft(citation_ids=["UNKNOWN-1"])]),
        conformance_service=conformance,
    )

    result = service.regenerate(PreflightRegenerationRequest(task="fix backend logging", top_k=2))

    assert result.stop_reason == "insufficient_context"
    assert result.final_result.insufficient_context is True
    assert conformance.requests == []


def test_trigger_mapping_preserves_judged_final_adherence_ids() -> None:
    packet = make_compiled_packet()
    conformance_result = make_conformance_result(
        passed=False,
        metrics=[
            PolicyConformanceMetric(
                name="final_adherence",
                score=0.25,
                passed=False,
                failure_reasons=["judge found missing policy detail"],
            )
        ],
        constraint_ids=["required_steps:0"],
        chunk_ids=["BACKEND-1"],
    )

    triggers = regeneration_triggers_from_conformance(conformance_result, packet)

    assert len(triggers) == 1
    assert triggers[0].kind == "final_adherence"
    assert triggers[0].constraint_ids == ["required_steps:0"]
    assert triggers[0].chunk_ids == ["BACKEND-1"]


class FakeCompilerService:
    """Static compiler service double."""

    def __init__(self, *, packet: CompiledPolicyPacket, context: list[ScoredChunk]) -> None:
        self._packet = packet
        self._context = context
        self.calls = 0
        self.closed = False

    def compile(self, request: CompileRequest) -> CompileResult:
        self.calls += 1
        return CompileResult(packet=self._packet, retained_context=list(self._context))

    def close(self) -> None:
        self.closed = True


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
        index = min(len(self.calls) - 1, len(self._drafts) - 1)
        return self._drafts[index]

    def close(self) -> None:
        self.closed = True


class FakeConformanceService:
    """Static conformance service double."""

    def __init__(self, outcomes: list[PolicyConformanceResult | ProviderError]) -> None:
        self._outcomes = outcomes
        self.requests: list[PolicyConformanceRequest] = []
        self.backends: list[str] = []
        self.closed = False

    def evaluate(
        self,
        request: PolicyConformanceRequest,
        *,
        backend,
    ) -> PolicyConformanceResult:
        self.requests.append(request)
        self.backends.append(backend)
        index = min(len(self.requests) - 1, len(self._outcomes) - 1)
        outcome = self._outcomes[index]
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome

    def close(self) -> None:
        self.closed = True


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


def make_compiled_packet(*, insufficient_context: bool = False) -> CompiledPolicyPacket:
    return CompiledPolicyPacket(
        task="fix backend logging",
        top_k=2,
        task_type="bug_fix",
        required_steps=(
            []
            if insufficient_context
            else [
                CompiledPolicyConstraint(
                    statement="Thread request ids through log context.",
                    citation_ids=["BACKEND-1"],
                    source_policy_ids=["BACKEND-LOG-001"],
                )
            ]
        ),
        test_expectations=(
            []
            if insufficient_context
            else [
                CompiledPolicyConstraint(
                    statement="Add a regression test for request-id logging.",
                    citation_ids=["BACKEND-1"],
                    source_policy_ids=["BACKEND-LOG-001"],
                )
            ]
        ),
        citations=[] if insufficient_context else [make_citation()],
        insufficient_context=insufficient_context,
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


def make_conformance_result(
    *,
    passed: bool,
    metrics: list[PolicyConformanceMetric] | None = None,
    constraint_ids: list[str] | None = None,
    chunk_ids: list[str] | None = None,
) -> PolicyConformanceResult:
    resolved_metrics = metrics or [
        PolicyConformanceMetric(
            name="plan_completeness",
            score=1.0 if passed else 0.0,
            passed=passed,
            failure_reasons=[] if passed else ["policy conformance failed"],
        )
    ]
    return PolicyConformanceResult(
        backend="nemo",
        passed=passed,
        overall_score=1.0 if passed else 0.0,
        metrics=resolved_metrics,
        constraint_ids=constraint_ids or [],
        chunk_ids=chunk_ids or [],
        failure_reasons=[
            reason for metric in resolved_metrics for reason in metric.failure_reasons
        ],
    )


def make_citation() -> Citation:
    return Citation(
        policy_id="BACKEND-LOG-001",
        title="Backend Logging",
        path="policies/backend/logging.md",
        section="Rules",
        lines="1-4",
        chunk_id="BACKEND-1",
    )
