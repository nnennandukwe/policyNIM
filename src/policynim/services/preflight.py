"""Preflight service for grounded PolicyNIM synthesis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any

from policynim.contracts import Embedder, Generator, IndexStore, PolicyCompiler, Reranker
from policynim.operations import bounded_stage, check_budget, close_components
from policynim.services.authorization import AuthenticatedPrincipal, authorize_operation
from policynim.services.compiler import PolicyCompilerService, create_policy_compiler_service
from policynim.services.router import PolicyRouterService
from policynim.settings import Settings, get_settings
from policynim.types import (
    Citation,
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    CompileRequest,
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    PolicyConformanceTraceStep,
    PolicyGuidance,
    PreflightRequest,
    PreflightResult,
    PreflightTraceResult,
    ScoredChunk,
)

DraftPolicyGuidance = GeneratedPolicyGuidance

_INSUFFICIENT_CONTEXT_SUMMARY = (
    "PolicyNIM could not find enough grounded policy evidence for this task."
)


class PreflightService:
    """Retrieve, rerank, synthesize, and validate grounded policy guidance."""

    def __init__(
        self,
        *,
        generator: Generator,
        compiler_service: PolicyCompilerService | None = None,
        compiler: PolicyCompiler | None = None,
        router: PolicyRouterService | None = None,
        embedder: Embedder | None = None,
        index_store: IndexStore | None = None,
        reranker: Reranker | None = None,
        operation_timeout_seconds: float = 120.0,
    ) -> None:
        """Assign operation-local dependencies and the outer deadline default."""
        self._operation_timeout_seconds = operation_timeout_seconds
        if compiler_service is None:
            if compiler is None:
                raise ValueError(
                    "PreflightService requires either a compiler service or a policy compiler."
                )
            if router is None and (embedder is None or index_store is None or reranker is None):
                raise ValueError(
                    "PreflightService requires either a compiler service or "
                    "router or embedder/index_store/reranker."
                )
            if router is None:
                assert embedder is not None
                assert index_store is not None
                assert reranker is not None
                router = PolicyRouterService(
                    embedder=embedder,
                    index_store=index_store,
                    reranker=reranker,
                )
            compiler_service = PolicyCompilerService(
                router=router,
                compiler=compiler,
            )
        self._compiler_service = compiler_service
        self._generator = generator

    def __enter__(self) -> PreflightService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release owned provider resources held by this service."""
        close_components(self._compiler_service, self._generator)

    @bounded_stage("preflight")
    def preflight(
        self, request: PreflightRequest, *, principal: AuthenticatedPrincipal | None = None
    ) -> PreflightResult:
        """Run the grounded preflight pipeline."""
        if principal is not None:
            authorize_operation(principal, "policy_preflight")
        output = self._run_preflight(request, with_trace=False)
        assert isinstance(output, PreflightResult)
        return output

    @bounded_stage("preflight")
    def preflight_with_trace(
        self, request: PreflightRequest, *, principal: AuthenticatedPrincipal | None = None
    ) -> PreflightTraceResult:
        """Run preflight and retain internal data needed for eval scoring."""
        if principal is not None:
            authorize_operation(principal, "policy_preflight")
        output = self._run_preflight(request, with_trace=True)
        assert isinstance(output, PreflightTraceResult)
        return output

    def _run_preflight(
        self,
        request: PreflightRequest,
        *,
        with_trace: bool,
    ) -> PreflightResult | PreflightTraceResult:
        """Compile and generate guidance while retaining the outer operation budget."""
        compile_result = self._compiler_service.compile(
            CompileRequest(task=request.task, domain=request.domain, top_k=request.top_k)
        )
        compiled_packet = compile_result.packet
        trace_steps: list[PolicyConformanceTraceStep] | None = [] if with_trace else None
        if trace_steps is not None:
            trace_steps.append(
                _trace_step(
                    step_id="compile",
                    kind="policy_compilation",
                    summary=(
                        "Compiled policy packet had insufficient context."
                        if compiled_packet.insufficient_context
                        else "Compiled policy packet for generation."
                    ),
                    citation_ids=[citation.chunk_id for citation in compiled_packet.citations],
                )
            )
        if compiled_packet.insufficient_context or not compile_result.retained_context:
            return _preflight_output(
                result=_insufficient_context_result(request),
                compiled_packet=compiled_packet,
                retained_context=compile_result.retained_context,
                trace_steps=trace_steps,
            )

        retained_context = compile_result.retained_context
        check_budget()
        generated = self._generator.generate_preflight(
            request,
            retained_context,
            compiled_packet=compiled_packet,
        )
        draft = _coerce_generated_draft(generated)
        draft = _apply_compiled_packet_to_draft(draft, compiled_packet)
        if trace_steps is not None:
            trace_steps.append(
                _trace_step(
                    step_id="generate",
                    kind="grounded_generation",
                    summary=draft.summary or "Generated preflight draft.",
                    citation_ids=draft.citation_ids,
                )
            )
        validated = _validate_and_materialize_result(request, retained_context, draft)
        if validated is None:
            return _preflight_output(
                result=_insufficient_context_result(request),
                compiled_packet=compiled_packet,
                retained_context=retained_context,
                trace_steps=trace_steps,
            )
        return _preflight_output(
            result=validated,
            compiled_packet=compiled_packet,
            retained_context=retained_context,
            trace_steps=trace_steps,
        )


def _preflight_output(
    *,
    result: PreflightResult,
    compiled_packet: CompiledPolicyPacket,
    retained_context: list[ScoredChunk],
    trace_steps: list[PolicyConformanceTraceStep] | None,
) -> PreflightResult | PreflightTraceResult:
    """Attach the authoritative retrieval generation before returning guidance or its trace."""
    result = result.model_copy(update={"generation_id": compiled_packet.generation_id})
    if trace_steps is None:
        return result
    return PreflightTraceResult(
        result=result,
        compiled_packet=compiled_packet,
        retained_context=retained_context,
        trace_steps=trace_steps,
    )


def create_preflight_service(settings: Settings | None = None) -> PreflightService:
    """Build the default preflight service from application settings."""
    active_settings = (settings or get_settings()).model_copy(deep=True)
    compiler_service = create_policy_compiler_service(active_settings)
    try:
        generator = _create_default_generator(active_settings)
        return PreflightService(
            compiler_service=compiler_service,
            generator=generator,
            operation_timeout_seconds=active_settings.operation_timeout_seconds,
        )
    except BaseException as error:
        close_components(compiler_service, primary_error=error)
        raise


def _create_default_generator(settings: Settings) -> Generator:
    from policynim.providers import NVIDIAGenerator

    return NVIDIAGenerator.from_settings(settings)


def _coerce_generated_draft(generated: Any) -> GeneratedPreflightDraft:
    if isinstance(generated, GeneratedPreflightDraft):
        return generated
    if isinstance(generated, Mapping):
        return GeneratedPreflightDraft.model_validate(generated)

    payload = {
        "summary": getattr(generated, "summary"),
        "applicable_policies": [
            _coerce_generated_policy_guidance(item)
            for item in getattr(generated, "applicable_policies", [])
        ],
        "plan_steps": list(getattr(generated, "plan_steps", [])),
        "implementation_guidance": list(getattr(generated, "implementation_guidance", [])),
        "review_flags": list(getattr(generated, "review_flags", [])),
        "tests_required": list(getattr(generated, "tests_required", [])),
        "citation_ids": list(getattr(generated, "citation_ids", [])),
        "insufficient_context": bool(getattr(generated, "insufficient_context", False)),
    }
    return GeneratedPreflightDraft.model_validate(payload)


def _coerce_generated_policy_guidance(item: Any) -> GeneratedPolicyGuidance:
    if isinstance(item, GeneratedPolicyGuidance):
        return item
    if isinstance(item, Mapping):
        return GeneratedPolicyGuidance.model_validate(item)
    return GeneratedPolicyGuidance.model_validate(
        {
            "policy_id": getattr(item, "policy_id"),
            "title": getattr(item, "title"),
            "rationale": getattr(item, "rationale"),
            "citation_ids": list(getattr(item, "citation_ids", [])),
        }
    )


def _validate_and_materialize_result(
    request: PreflightRequest,
    context: Sequence[ScoredChunk],
    draft: GeneratedPreflightDraft,
) -> PreflightResult | None:
    if draft.insufficient_context:
        return None

    context_by_id = {chunk.chunk_id: chunk for chunk in context}
    if not context_by_id:
        return None

    citation_ids = _ordered_unique(
        draft.citation_ids
        + [
            citation_id
            for policy in draft.applicable_policies
            for citation_id in policy.citation_ids
        ]
    )
    if not citation_ids:
        return None
    if any(citation_id not in context_by_id for citation_id in citation_ids):
        return None

    applicable_policies: list[PolicyGuidance] = []
    for policy in draft.applicable_policies:
        policy_citation_ids = _ordered_unique(policy.citation_ids)
        if not policy_citation_ids:
            continue
        if any(citation_id not in context_by_id for citation_id in policy_citation_ids):
            return None
        if any(
            context_by_id[citation_id].policy.policy_id != policy.policy_id
            or context_by_id[citation_id].policy.title != policy.title
            for citation_id in policy_citation_ids
        ):
            return None
        applicable_policies.append(
            PolicyGuidance(
                policy_id=policy.policy_id,
                title=policy.title,
                rationale=policy.rationale,
                citation_ids=policy_citation_ids,
            )
        )

    if not applicable_policies:
        return None

    citations = [
        Citation(
            policy_id=context_by_id[citation_id].policy.policy_id,
            title=context_by_id[citation_id].policy.title,
            path=context_by_id[citation_id].path,
            section=context_by_id[citation_id].section,
            lines=context_by_id[citation_id].lines,
            chunk_id=citation_id,
        )
        for citation_id in citation_ids
    ]
    if not citations:
        return None

    return PreflightResult(
        task=request.task,
        domain=request.domain,
        summary=draft.summary,
        applicable_policies=applicable_policies,
        plan_steps=list(draft.plan_steps),
        implementation_guidance=list(draft.implementation_guidance),
        review_flags=list(draft.review_flags),
        tests_required=list(draft.tests_required),
        citations=citations,
        insufficient_context=False,
    )


def _insufficient_context_result(request: PreflightRequest) -> PreflightResult:
    return PreflightResult(
        task=request.task,
        domain=request.domain,
        summary=_INSUFFICIENT_CONTEXT_SUMMARY,
        applicable_policies=[],
        plan_steps=[],
        implementation_guidance=[],
        review_flags=[],
        tests_required=[],
        citations=[],
        insufficient_context=True,
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


def _apply_compiled_packet_to_draft(
    draft: GeneratedPreflightDraft,
    compiled_packet: CompiledPolicyPacket,
) -> GeneratedPreflightDraft:
    if compiled_packet.insufficient_context:
        return draft

    compiled_plan_steps = _constraint_statements(compiled_packet.required_steps)
    compiled_guidance = _constraint_statements(
        [
            *compiled_packet.architectural_expectations,
            *compiled_packet.style_constraints,
        ]
    )
    compiled_review_flags = [
        f"Avoid: {statement}"
        for statement in _constraint_statements(compiled_packet.forbidden_patterns)
    ]
    compiled_tests = _constraint_statements(compiled_packet.test_expectations)
    compiled_citation_ids = [citation.chunk_id for citation in compiled_packet.citations]
    draft_citation_ids = draft.citation_ids or [
        citation_id for policy in draft.applicable_policies for citation_id in policy.citation_ids
    ]

    return draft.model_copy(
        update={
            "plan_steps": _ordered_unique([*compiled_plan_steps, *draft.plan_steps]),
            "implementation_guidance": _ordered_unique(
                [*compiled_guidance, *draft.implementation_guidance]
            ),
            "review_flags": _ordered_unique([*compiled_review_flags, *draft.review_flags]),
            "tests_required": _ordered_unique([*compiled_tests, *draft.tests_required]),
            "citation_ids": _ordered_unique([*draft_citation_ids, *compiled_citation_ids]),
        }
    )


def _constraint_statements(
    constraints: Sequence[CompiledPolicyConstraint],
) -> list[str]:
    return [constraint.statement for constraint in constraints]


def _trace_step(
    *,
    step_id: str,
    kind: str,
    summary: str,
    citation_ids: Sequence[str],
) -> PolicyConformanceTraceStep:
    return PolicyConformanceTraceStep(
        step_id=step_id,
        kind=kind,
        summary=summary.strip() or kind,
        citation_ids=_ordered_unique(list(citation_ids)),
    )


__all__ = [
    "DraftPolicyGuidance",
    "GeneratedPreflightDraft",
    "PreflightService",
    "create_preflight_service",
]
