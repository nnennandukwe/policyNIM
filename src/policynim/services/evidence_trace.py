"""Evidence trace materialization for policy-conditioned preflight runs."""

from __future__ import annotations

from collections.abc import Sequence

from policynim.types import (
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    PolicyConformanceResult,
    PolicyEvidenceTrace,
    PolicyEvidenceTraceChunk,
    PolicyEvidenceTraceConformanceCheck,
    PolicyEvidenceTraceConstraint,
    PolicyEvidenceTraceConstraintCategory,
    PolicyEvidenceTraceOutputField,
    PolicyEvidenceTraceOutputLink,
    PolicyEvidenceTracePolicy,
    PreflightResult,
    PreflightTraceResult,
    ScoredChunk,
)


class PolicyEvidenceTraceService:
    """Build replay-free trace records from existing preflight and eval data."""

    def build(
        self,
        trace_result: PreflightTraceResult,
        *,
        conformance_result: PolicyConformanceResult | None = None,
        include_chunk_text: bool = True,
    ) -> PolicyEvidenceTrace:
        """Materialize a policy evidence trace without re-running the pipeline."""
        compiled_packet = trace_result.compiled_packet
        constraints = _trace_constraints(compiled_packet)
        return PolicyEvidenceTrace(
            task=trace_result.result.task,
            domain=trace_result.result.domain,
            top_k=compiled_packet.top_k,
            task_type=compiled_packet.task_type,
            explicit_task_type=compiled_packet.explicit_task_type,
            profile_signals=list(compiled_packet.profile_signals),
            insufficient_context=trace_result.result.insufficient_context,
            compiled_insufficient_context=compiled_packet.insufficient_context,
            chunks=_trace_chunks(
                trace_result.retained_context,
                include_chunk_text=include_chunk_text,
            ),
            selected_policies=_trace_policies(compiled_packet),
            constraints=constraints,
            output_links=_trace_output_links(trace_result.result, constraints),
            trace_steps=list(trace_result.trace_steps),
            conformance_checks=_trace_conformance_checks(conformance_result, constraints),
        )


def create_policy_evidence_trace_service() -> PolicyEvidenceTraceService:
    """Build the default policy evidence trace service."""
    return PolicyEvidenceTraceService()


def _trace_chunks(
    chunks: Sequence[ScoredChunk],
    *,
    include_chunk_text: bool,
) -> list[PolicyEvidenceTraceChunk]:
    seen_chunk_ids: set[str] = set()
    trace_chunks: list[PolicyEvidenceTraceChunk] = []
    for chunk in chunks:
        if chunk.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk.chunk_id)
        trace_chunks.append(
            PolicyEvidenceTraceChunk(
                chunk_id=chunk.chunk_id,
                policy_id=chunk.policy.policy_id,
                policy_title=chunk.policy.title,
                domain=chunk.policy.domain,
                path=chunk.path,
                section=chunk.section,
                lines=chunk.lines,
                text=chunk.text if include_chunk_text else None,
                score=chunk.score,
            )
        )
    return trace_chunks


def _trace_policies(compiled_packet: CompiledPolicyPacket) -> list[PolicyEvidenceTracePolicy]:
    return [
        PolicyEvidenceTracePolicy(
            policy_id=policy.policy_id,
            title=policy.title,
            reason=policy.reason,
            supporting_chunk_ids=_ordered_unique(
                [evidence.chunk_id for evidence in policy.evidence]
            ),
        )
        for policy in compiled_packet.selected_policies
    ]


def _trace_constraints(
    compiled_packet: CompiledPolicyPacket,
) -> list[PolicyEvidenceTraceConstraint]:
    constraints: list[PolicyEvidenceTraceConstraint] = []
    categories: tuple[
        tuple[PolicyEvidenceTraceConstraintCategory, Sequence[CompiledPolicyConstraint]],
        ...,
    ] = (
        ("required_steps", compiled_packet.required_steps),
        ("forbidden_patterns", compiled_packet.forbidden_patterns),
        ("architectural_expectations", compiled_packet.architectural_expectations),
        ("test_expectations", compiled_packet.test_expectations),
        ("style_constraints", compiled_packet.style_constraints),
    )
    for category, category_constraints in categories:
        for index, constraint in enumerate(category_constraints):
            constraints.append(
                PolicyEvidenceTraceConstraint(
                    constraint_id=f"{category}:{index}",
                    category=category,
                    statement=constraint.statement,
                    citation_ids=list(constraint.citation_ids),
                    source_policy_ids=list(constraint.source_policy_ids),
                )
            )
    return constraints


def _trace_output_links(
    result: PreflightResult,
    constraints: Sequence[PolicyEvidenceTraceConstraint],
) -> list[PolicyEvidenceTraceOutputLink]:
    constraints_by_text = _constraints_by_output_text(constraints)
    links: list[PolicyEvidenceTraceOutputLink] = []
    links.extend(_trace_text_outputs("plan_steps", result.plan_steps, constraints_by_text))
    links.extend(
        _trace_text_outputs(
            "implementation_guidance",
            result.implementation_guidance,
            constraints_by_text,
        )
    )
    links.extend(_trace_text_outputs("review_flags", result.review_flags, constraints_by_text))
    links.extend(_trace_text_outputs("tests_required", result.tests_required, constraints_by_text))
    constraints_by_chunk_id = _constraints_by_chunk_id(constraints)
    for index, citation in enumerate(result.citations):
        linked_constraints = constraints_by_chunk_id.get(citation.chunk_id, [])
        links.append(
            PolicyEvidenceTraceOutputLink(
                field="citations",
                index=index,
                text=citation.chunk_id,
                constraint_ids=[constraint.constraint_id for constraint in linked_constraints],
                chunk_ids=[citation.chunk_id],
            )
        )
    return links


def _trace_text_outputs(
    field: PolicyEvidenceTraceOutputField,
    values: Sequence[str],
    constraints_by_text: dict[str, list[PolicyEvidenceTraceConstraint]],
) -> list[PolicyEvidenceTraceOutputLink]:
    links: list[PolicyEvidenceTraceOutputLink] = []
    for index, text in enumerate(values):
        linked_constraints = constraints_by_text.get(text, [])
        links.append(
            PolicyEvidenceTraceOutputLink(
                field=field,
                index=index,
                text=text,
                constraint_ids=[constraint.constraint_id for constraint in linked_constraints],
                chunk_ids=_ordered_unique(
                    [
                        chunk_id
                        for constraint in linked_constraints
                        for chunk_id in constraint.citation_ids
                    ]
                ),
            )
        )
    return links


def _trace_conformance_checks(
    conformance_result: PolicyConformanceResult | None,
    constraints: Sequence[PolicyEvidenceTraceConstraint],
) -> list[PolicyEvidenceTraceConformanceCheck]:
    if conformance_result is None:
        return []

    constraints_by_category = _constraints_by_category(constraints)
    all_constraint_ids = [constraint.constraint_id for constraint in constraints]
    all_chunk_ids = _ordered_unique(
        [chunk_id for constraint in constraints for chunk_id in constraint.citation_ids]
    )
    checks: list[PolicyEvidenceTraceConformanceCheck] = []
    for metric in conformance_result.metrics:
        constraint_ids = _metric_constraint_ids(
            metric.name,
            constraints_by_category,
            all_constraint_ids,
        )
        chunk_ids = _metric_chunk_ids(metric.name, constraints_by_category, all_chunk_ids)
        if metric.name in {"final_adherence", "trajectory_adherence"}:
            constraint_ids = list(conformance_result.constraint_ids)
            chunk_ids = list(conformance_result.chunk_ids)
        checks.append(
            PolicyEvidenceTraceConformanceCheck(
                backend=conformance_result.backend,
                name=metric.name,
                passed=metric.passed,
                score=metric.score,
                failure_reasons=list(metric.failure_reasons),
                constraint_ids=constraint_ids,
                chunk_ids=chunk_ids,
            )
        )
    return checks


def _constraints_by_output_text(
    constraints: Sequence[PolicyEvidenceTraceConstraint],
) -> dict[str, list[PolicyEvidenceTraceConstraint]]:
    constraints_by_text: dict[str, list[PolicyEvidenceTraceConstraint]] = {}
    for constraint in constraints:
        constraints_by_text.setdefault(constraint.statement, []).append(constraint)
        if constraint.category == "forbidden_patterns":
            constraints_by_text.setdefault(f"Avoid: {constraint.statement}", []).append(constraint)
    return constraints_by_text


def _constraints_by_chunk_id(
    constraints: Sequence[PolicyEvidenceTraceConstraint],
) -> dict[str, list[PolicyEvidenceTraceConstraint]]:
    constraints_by_chunk_id: dict[str, list[PolicyEvidenceTraceConstraint]] = {}
    for constraint in constraints:
        for citation_id in constraint.citation_ids:
            constraints_by_chunk_id.setdefault(citation_id, []).append(constraint)
    return constraints_by_chunk_id


def _constraints_by_category(
    constraints: Sequence[PolicyEvidenceTraceConstraint],
) -> dict[PolicyEvidenceTraceConstraintCategory, list[PolicyEvidenceTraceConstraint]]:
    constraints_by_category: dict[
        PolicyEvidenceTraceConstraintCategory, list[PolicyEvidenceTraceConstraint]
    ] = {
        "required_steps": [],
        "forbidden_patterns": [],
        "architectural_expectations": [],
        "test_expectations": [],
        "style_constraints": [],
    }
    for constraint in constraints:
        constraints_by_category[constraint.category].append(constraint)
    return constraints_by_category


def _metric_constraint_ids(
    metric_name: str,
    constraints_by_category: dict[
        PolicyEvidenceTraceConstraintCategory, list[PolicyEvidenceTraceConstraint]
    ],
    all_constraint_ids: Sequence[str],
) -> list[str]:
    if metric_name == "plan_completeness":
        return [
            constraint.constraint_id for constraint in constraints_by_category["required_steps"]
        ]
    if metric_name == "guidance_coverage":
        return [
            constraint.constraint_id
            for constraint in (
                *constraints_by_category["architectural_expectations"],
                *constraints_by_category["style_constraints"],
            )
        ]
    if metric_name == "test_coverage":
        return [
            constraint.constraint_id for constraint in constraints_by_category["test_expectations"]
        ]
    if metric_name == "forbidden_pattern_handling":
        return [
            constraint.constraint_id for constraint in constraints_by_category["forbidden_patterns"]
        ]
    if metric_name == "citation_support":
        return list(all_constraint_ids)
    return []


def _metric_chunk_ids(
    metric_name: str,
    constraints_by_category: dict[
        PolicyEvidenceTraceConstraintCategory, list[PolicyEvidenceTraceConstraint]
    ],
    all_chunk_ids: Sequence[str],
) -> list[str]:
    if metric_name == "citation_support":
        return list(all_chunk_ids)

    if metric_name == "guidance_coverage":
        constraints = [
            *constraints_by_category["architectural_expectations"],
            *constraints_by_category["style_constraints"],
        ]
    elif metric_name == "plan_completeness":
        constraints = constraints_by_category["required_steps"]
    elif metric_name == "test_coverage":
        constraints = constraints_by_category["test_expectations"]
    elif metric_name == "forbidden_pattern_handling":
        constraints = constraints_by_category["forbidden_patterns"]
    else:
        constraints = []
    return _ordered_unique(
        [chunk_id for constraint in constraints for chunk_id in constraint.citation_ids]
    )


def _ordered_unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


__all__ = ["PolicyEvidenceTraceService", "create_policy_evidence_trace_service"]
