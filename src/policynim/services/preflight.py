"""Preflight service for grounded PolicyNIM synthesis."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import TracebackType
from typing import Any, Literal

from policynim.contracts import Embedder, Generator, IndexStore, PolicyCompiler, Reranker
from policynim.errors import PolicyContextError
from policynim.iterables import ordered_unique
from policynim.operations import bounded_stage, check_budget, close_components
from policynim.services.authorization import AuthenticatedPrincipal, authorize_operation
from policynim.services.compiler import (
    PolicyCompilerService,
    create_policy_compiler_service,
    source_catalog_for,
)
from policynim.services.router import PolicyRouterService
from policynim.settings import Settings, get_settings
from policynim.source_units import MAX_OUTPUT_BYTES, SourceCatalog, SourceContext
from policynim.types import (
    Citation,
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


GroundingRefusalCode = Literal[
    "model_insufficient_context",
    "empty_source_context",
    "missing_citations",
    "unknown_citation",
    "policy_id_mismatch",
    "policy_title_mismatch",
    "missing_policy_guidance",
    "unsupported_source_text",
]


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
        source_catalog_resolver: Callable[[Sequence[ScoredChunk]], SourceCatalog] | None = None,
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
                source_catalog_resolver=source_catalog_resolver,
            )
        elif source_catalog_resolver is not None:
            raise ValueError("Source authority must belong to the supplied compiler service.")
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
        retained_catalog = source_catalog_for(self._compiler_service, retained_context)
        catalog = retained_catalog.compiled_sources(compiled_packet)
        if not catalog.units:
            return _preflight_output(
                result=_insufficient_context_result(request),
                compiled_packet=compiled_packet,
                retained_context=retained_context,
                trace_steps=trace_steps,
            )
        check_budget()
        generated = self._generator.generate_preflight(
            request,
            SourceContext(tuple(retained_context), retained_catalog),
            compiled_packet=compiled_packet,
        )
        draft = _coerce_generated_draft(generated)
        raw_valid = catalog.accepts_preflight(draft, allow_missing_references=True)
        if raw_valid:
            draft = _apply_compiled_packet_to_draft(draft, compiled_packet)
        if trace_steps is not None:
            trace_steps.append(
                _trace_step(
                    step_id="generate",
                    kind="grounded_generation",
                    summary="Provider source selection completed; local validation follows.",
                    citation_ids=draft.citation_ids if raw_valid else [],
                )
            )
        owned = source_catalog_for(self._compiler_service, retained_context) == retained_catalog
        validated = (
            _validate_and_materialize_result(request, retained_context, draft, catalog=catalog)
            if owned and raw_valid
            else None
        )
        if validated is None:
            refusal = (
                _grounding_refusal_code(retained_context, draft, catalog=catalog)
                if owned
                else "unsupported_source_text"
            )
            refusal = refusal or "unsupported_source_text"
            if trace_steps is not None:
                trace_steps.append(
                    _trace_step(
                        step_id="grounding_validation",
                        kind="grounding_refusal",
                        summary=f"Grounding refused: {refusal}.",
                        citation_ids=[],
                    )
                )
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
    if len(result.model_dump_json().encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise PolicyContextError(output_budget=True)
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


def _grounding_refusal_code(
    context: Sequence[ScoredChunk],
    draft: GeneratedPreflightDraft,
    *,
    catalog: SourceCatalog | None = None,
) -> GroundingRefusalCode | None:
    """Classify existing grounding guards without returning any model-supplied values."""
    if draft.insufficient_context:
        return "model_insufficient_context"
    context_by_id = {chunk.chunk_id: chunk for chunk in context}
    if not context_by_id:
        return "empty_source_context"
    citation_ids = ordered_unique(
        draft.citation_ids
        + [
            citation_id
            for policy in draft.applicable_policies
            for citation_id in policy.citation_ids
        ]
    )
    if not citation_ids:
        return "missing_citations"
    if any(citation_id not in context_by_id for citation_id in citation_ids):
        return "unknown_citation"
    supported_policy = False
    for policy in draft.applicable_policies:
        if not policy.citation_ids:
            continue
        if any(
            context_by_id[citation_id].policy.policy_id != policy.policy_id
            for citation_id in policy.citation_ids
        ):
            return "policy_id_mismatch"
        if any(
            context_by_id[citation_id].policy.title != policy.title
            for citation_id in policy.citation_ids
        ):
            return "policy_title_mismatch"
        supported_policy = True
    if not supported_policy:
        return "missing_policy_guidance"
    if catalog is not None and not catalog.accepts_preflight(draft):
        return "unsupported_source_text"
    return None


def _validate_and_materialize_result(
    request: PreflightRequest,
    context: Sequence[ScoredChunk],
    draft: GeneratedPreflightDraft,
    *,
    catalog: SourceCatalog | None = None,
) -> PreflightResult | None:
    """Materialize only source-bound guidance; preserve the shared regeneration contract."""
    if _grounding_refusal_code(context, draft, catalog=catalog) is not None:
        return None
    context_by_id = {chunk.chunk_id: chunk for chunk in context}
    citation_ids = ordered_unique(
        draft.citation_ids
        + [
            citation_id
            for policy in draft.applicable_policies
            for citation_id in policy.citation_ids
        ]
    )
    applicable_policies = [
        PolicyGuidance(
            policy_id=policy.policy_id,
            title=policy.title,
            rationale=policy.rationale,
            citation_ids=ordered_unique(policy.citation_ids),
        )
        for policy in draft.applicable_policies
        if policy.citation_ids
    ]

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

    result = PreflightResult(
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
    if len(result.model_dump_json().encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise PolicyContextError(output_budget=True)
    return result


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


def _apply_compiled_packet_to_draft(
    draft: GeneratedPreflightDraft,
    compiled_packet: CompiledPolicyPacket,
) -> GeneratedPreflightDraft:
    """Fill only derived references after source validation; preserve one body per owner."""
    return draft.model_copy(
        update={
            "citation_ids": ordered_unique(
                [
                    citation_id
                    for policy in draft.applicable_policies
                    for citation_id in policy.citation_ids
                ]
                + [citation.chunk_id for citation in compiled_packet.citations]
            )
        }
    )


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
        citation_ids=ordered_unique(list(citation_ids)),
    )


__all__ = [
    "DraftPolicyGuidance",
    "GeneratedPreflightDraft",
    "PreflightService",
    "create_preflight_service",
]
