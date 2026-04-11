"""Policy compiler service for citation-backed planning constraints."""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType

from policynim.contracts import Embedder, IndexStore, PolicyCompiler, Reranker
from policynim.services.router import PolicyRouterService, create_policy_router_service
from policynim.settings import Settings, get_settings
from policynim.types import (
    Citation,
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    CompileRequest,
    CompileResult,
    GeneratedCompiledPolicyDraft,
    GeneratedPolicyConstraint,
    PolicySelectionPacket,
    RouteRequest,
    ScoredChunk,
)


class PolicyCompilerService:
    """Route policy evidence and compile it into grounded constraint packets."""

    def __init__(
        self,
        *,
        compiler: PolicyCompiler,
        router: PolicyRouterService | None = None,
        embedder: Embedder | None = None,
        index_store: IndexStore | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        if router is None:
            if embedder is None or index_store is None or reranker is None:
                raise ValueError(
                    "PolicyCompilerService requires either a router or "
                    "embedder/index_store/reranker."
                )
            router = PolicyRouterService(
                embedder=embedder,
                index_store=index_store,
                reranker=reranker,
            )
        self._router = router
        self._compiler = compiler

    def __enter__(self) -> PolicyCompilerService:
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
        _close_component(self._router)
        _close_component(self._compiler)

    def compile(self, request: CompileRequest) -> CompileResult:
        """Compile routed policy evidence into a grounded policy packet."""
        route_result = self._router.route(
            RouteRequest(
                task=request.task,
                domain=request.domain,
                top_k=request.top_k,
                task_type=request.task_type,
            )
        )
        selection_packet = route_result.packet
        if selection_packet.insufficient_context or not route_result.retained_context:
            return CompileResult(
                packet=_insufficient_packet_from_selection(selection_packet),
                retained_context=route_result.retained_context,
            )

        generated_draft = self._compiler.compile_policy_packet(
            request,
            selection_packet,
            route_result.retained_context,
        )
        compiled_packet = _materialize_compiled_packet(
            selection_packet,
            route_result.retained_context,
            generated_draft,
        )
        return CompileResult(
            packet=compiled_packet,
            retained_context=route_result.retained_context,
        )


def create_policy_compiler_service(settings: Settings | None = None) -> PolicyCompilerService:
    """Build the default policy compiler service from application settings."""
    active_settings = settings or get_settings()
    router = create_policy_router_service(active_settings)
    compiler = _create_default_policy_compiler(active_settings)
    return PolicyCompilerService(
        router=router,
        compiler=compiler,
    )


def _create_default_policy_compiler(settings: Settings) -> PolicyCompiler:
    from policynim.providers import NVIDIAPolicyCompiler

    return NVIDIAPolicyCompiler.from_settings(settings)


def _materialize_compiled_packet(
    selection_packet: PolicySelectionPacket,
    retained_context: Sequence[ScoredChunk],
    generated_draft: GeneratedCompiledPolicyDraft,
) -> CompiledPolicyPacket:
    if generated_draft.insufficient_context:
        return _insufficient_packet_from_selection(selection_packet)

    context_by_id = {chunk.chunk_id: chunk for chunk in retained_context}
    if not context_by_id:
        return _insufficient_packet_from_selection(selection_packet)

    required_steps = _compile_constraint_list(generated_draft.required_steps, context_by_id)
    forbidden_patterns = _compile_constraint_list(
        generated_draft.forbidden_patterns,
        context_by_id,
    )
    architectural_expectations = _compile_constraint_list(
        generated_draft.architectural_expectations,
        context_by_id,
    )
    test_expectations = _compile_constraint_list(generated_draft.test_expectations, context_by_id)
    style_constraints = _compile_constraint_list(generated_draft.style_constraints, context_by_id)
    compiled_categories = [
        required_steps,
        forbidden_patterns,
        architectural_expectations,
        test_expectations,
        style_constraints,
    ]
    if any(category is None for category in compiled_categories):
        return _insufficient_packet_from_selection(selection_packet)

    all_constraints = [
        constraint
        for category in compiled_categories
        if category is not None
        for constraint in category
    ]
    if not all_constraints:
        return _insufficient_packet_from_selection(selection_packet)

    citation_ids = _ordered_unique(
        [citation_id for constraint in all_constraints for citation_id in constraint.citation_ids]
    )
    citations = [_citation_from_chunk(context_by_id[citation_id]) for citation_id in citation_ids]
    if not citations:
        return _insufficient_packet_from_selection(selection_packet)

    return CompiledPolicyPacket(
        task=selection_packet.task,
        domain=selection_packet.domain,
        top_k=selection_packet.top_k,
        task_type=selection_packet.task_type,
        explicit_task_type=selection_packet.explicit_task_type,
        profile_signals=list(selection_packet.profile_signals),
        selected_policies=list(selection_packet.selected_policies),
        required_steps=required_steps or [],
        forbidden_patterns=forbidden_patterns or [],
        architectural_expectations=architectural_expectations or [],
        test_expectations=test_expectations or [],
        style_constraints=style_constraints or [],
        citations=citations,
        insufficient_context=False,
    )


def _compile_constraint_list(
    generated_constraints: Sequence[GeneratedPolicyConstraint],
    context_by_id: dict[str, ScoredChunk],
) -> list[CompiledPolicyConstraint] | None:
    compiled_constraints: list[CompiledPolicyConstraint] = []
    for generated_constraint in generated_constraints:
        statement = generated_constraint.statement.strip()
        citation_ids = _ordered_unique(
            [citation_id.strip() for citation_id in generated_constraint.citation_ids]
        )
        if not statement or not citation_ids:
            return None
        if any(citation_id not in context_by_id for citation_id in citation_ids):
            return None

        source_policy_ids = _ordered_unique(
            [context_by_id[citation_id].policy.policy_id for citation_id in citation_ids]
        )
        compiled_constraints.append(
            CompiledPolicyConstraint(
                statement=statement,
                citation_ids=citation_ids,
                source_policy_ids=source_policy_ids,
            )
        )
    return compiled_constraints


def _insufficient_packet_from_selection(
    selection_packet: PolicySelectionPacket,
) -> CompiledPolicyPacket:
    return CompiledPolicyPacket(
        task=selection_packet.task,
        domain=selection_packet.domain,
        top_k=selection_packet.top_k,
        task_type=selection_packet.task_type,
        explicit_task_type=selection_packet.explicit_task_type,
        profile_signals=list(selection_packet.profile_signals),
        selected_policies=list(selection_packet.selected_policies),
        required_steps=[],
        forbidden_patterns=[],
        architectural_expectations=[],
        test_expectations=[],
        style_constraints=[],
        citations=[],
        insufficient_context=True,
    )


def _citation_from_chunk(chunk: ScoredChunk) -> Citation:
    return Citation(
        policy_id=chunk.policy.policy_id,
        title=chunk.policy.title,
        path=chunk.path,
        section=chunk.section,
        lines=chunk.lines,
        chunk_id=chunk.chunk_id,
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


def _close_component(component: object | None) -> None:
    close = getattr(component, "close", None)
    if callable(close):
        close()


__all__ = [
    "PolicyCompilerService",
    "create_policy_compiler_service",
]
