"""Tests for policy evidence trace materialization."""

from __future__ import annotations

from policynim.services.evidence_trace import PolicyEvidenceTraceService
from policynim.types import (
    Citation,
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    PolicyConformanceMetric,
    PolicyConformanceResult,
    PolicyConformanceTraceStep,
    PolicyGuidance,
    PolicyMetadata,
    PreflightResult,
    PreflightTraceResult,
    ScoredChunk,
    SelectedPolicy,
    SelectedPolicyEvidence,
)


def test_evidence_trace_links_chunks_constraints_outputs_and_conformance() -> None:
    service = PolicyEvidenceTraceService()

    trace = service.build(make_trace_result(), conformance_result=make_conformance_result())

    assert trace.task == "fix backend logging"
    assert trace.top_k == 2
    assert trace.task_type == "bug_fix"
    assert trace.profile_signals == ["bug"]
    assert [chunk.chunk_id for chunk in trace.chunks] == ["BACKEND-1", "SECURITY-1"]
    assert trace.chunks[0].policy_id == "BACKEND-LOG-001"
    assert trace.chunks[0].path == "policies/backend/logging.md"
    assert trace.chunks[0].text == "Use request ids in backend logs."
    assert trace.selected_policies[0].supporting_chunk_ids == ["BACKEND-1"]
    assert [constraint.constraint_id for constraint in trace.constraints] == [
        "required_steps:0",
        "forbidden_patterns:0",
        "architectural_expectations:0",
        "test_expectations:0",
        "style_constraints:0",
    ]
    assert trace.output_links[0].field == "plan_steps"
    assert trace.output_links[0].constraint_ids == ["required_steps:0"]
    assert trace.output_links[1].field == "implementation_guidance"
    assert trace.output_links[1].constraint_ids == ["architectural_expectations:0"]
    assert trace.output_links[3].field == "review_flags"
    assert trace.output_links[3].constraint_ids == ["forbidden_patterns:0"]
    assert trace.output_links[-1].field == "citations"
    assert trace.output_links[-1].chunk_ids == ["SECURITY-1"]
    assert trace.trace_steps[0].step_id == "compile"
    assert trace.conformance_checks[-1].name == "final_adherence"
    assert trace.conformance_checks[-1].constraint_ids == ["required_steps:0"]
    assert trace.conformance_checks[-1].chunk_ids == ["BACKEND-1"]


def test_evidence_trace_preserves_insufficient_context_without_output_links() -> None:
    service = PolicyEvidenceTraceService()
    result = PreflightResult(
        task="unclear task",
        summary="PolicyNIM could not find enough grounded policy evidence for this task.",
        insufficient_context=True,
    )
    trace_result = PreflightTraceResult(
        result=result,
        compiled_packet=CompiledPolicyPacket(
            task="unclear task",
            top_k=2,
            task_type="unknown",
            insufficient_context=True,
        ),
        retained_context=[],
        trace_steps=[],
    )

    trace = service.build(trace_result)

    assert trace.task == "unclear task"
    assert trace.insufficient_context is True
    assert trace.compiled_insufficient_context is True
    assert trace.chunks == []
    assert trace.constraints == []
    assert trace.output_links == []
    assert trace.conformance_checks == []


def make_trace_result() -> PreflightTraceResult:
    return PreflightTraceResult(
        result=make_result(),
        compiled_packet=make_compiled_packet(),
        retained_context=[
            make_chunk(
                chunk_id="BACKEND-1",
                policy_id="BACKEND-LOG-001",
                title="Backend Logging",
                domain="backend",
                path="policies/backend/logging.md",
                text="Use request ids in backend logs.",
                score=0.98,
            ),
            make_chunk(
                chunk_id="SECURITY-1",
                policy_id="SECURITY-TOKEN-001",
                title="Token Handling",
                domain="security",
                path="policies/security/token.md",
                text="Never log token values.",
                score=0.95,
            ),
        ],
        trace_steps=[
            PolicyConformanceTraceStep(
                step_id="compile",
                kind="policy_compilation",
                summary="Compiled policy constraints.",
                citation_ids=["BACKEND-1", "SECURITY-1"],
            )
        ],
    )


def make_compiled_packet() -> CompiledPolicyPacket:
    return CompiledPolicyPacket(
        task="fix backend logging",
        top_k=2,
        task_type="bug_fix",
        profile_signals=["bug"],
        selected_policies=[
            SelectedPolicy(
                policy_id="BACKEND-LOG-001",
                title="Backend Logging",
                domain="backend",
                reason="Selected for logging guidance.",
                evidence=[
                    SelectedPolicyEvidence(
                        chunk_id="BACKEND-1",
                        path="policies/backend/logging.md",
                        section="Rules",
                        lines="1-4",
                        text="Use request ids in backend logs.",
                        score=0.98,
                    )
                ],
            )
        ],
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
            make_citation("BACKEND-1", "BACKEND-LOG-001", "Backend Logging"),
            make_citation("SECURITY-1", "SECURITY-TOKEN-001", "Token Handling"),
        ],
    )


def make_result() -> PreflightResult:
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
        plan_steps=["Thread request ids through log context."],
        implementation_guidance=[
            "Keep logging changes in the backend service layer.",
            "Use explicit request-id naming.",
        ],
        review_flags=["Avoid: Never log token values."],
        tests_required=["Add a regression test for token redaction."],
        citations=[
            make_citation("BACKEND-1", "BACKEND-LOG-001", "Backend Logging"),
            make_citation("SECURITY-1", "SECURITY-TOKEN-001", "Token Handling"),
        ],
    )


def make_conformance_result() -> PolicyConformanceResult:
    return PolicyConformanceResult(
        backend="nemo",
        passed=True,
        overall_score=1.0,
        metrics=[
            PolicyConformanceMetric(
                name="plan_completeness",
                score=1.0,
                passed=True,
            ),
            PolicyConformanceMetric(
                name="final_adherence",
                score=1.0,
                passed=True,
            ),
        ],
        final_adherence_score=1.0,
        final_adherence_rationale="Output follows the compiled constraints.",
        constraint_ids=["required_steps:0"],
        chunk_ids=["BACKEND-1"],
    )


def make_chunk(
    *,
    chunk_id: str,
    policy_id: str,
    title: str,
    domain: str,
    path: str,
    text: str,
    score: float,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        path=path,
        section="Rules",
        lines="1-4",
        text=text,
        policy=PolicyMetadata(
            policy_id=policy_id,
            title=title,
            doc_type="guidance",
            domain=domain,
        ),
        score=score,
    )


def make_citation(chunk_id: str, policy_id: str, title: str) -> Citation:
    return Citation(
        policy_id=policy_id,
        title=title,
        path=f"policies/{policy_id}.md",
        section="Rules",
        lines="1-4",
        chunk_id=chunk_id,
    )
