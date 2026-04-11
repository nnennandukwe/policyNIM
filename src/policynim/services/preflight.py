"""Preflight service for grounded PolicyNIM synthesis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any

from policynim.contracts import Embedder, Generator, IndexStore, Reranker
from policynim.services.router import PolicyRouterService, create_policy_router_service
from policynim.settings import Settings, get_settings
from policynim.types import (
    Citation,
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    PolicyGuidance,
    PreflightRequest,
    PreflightResult,
    RouteRequest,
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
        router: PolicyRouterService | None = None,
        embedder: Embedder | None = None,
        index_store: IndexStore | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        if router is None:
            if embedder is None or index_store is None or reranker is None:
                raise ValueError(
                    "PreflightService requires either a router or embedder/index_store/reranker."
                )
            router = PolicyRouterService(
                embedder=embedder,
                index_store=index_store,
                reranker=reranker,
            )
        self._router = router
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
        _close_component(self._router)
        _close_component(self._generator)

    def preflight(self, request: PreflightRequest) -> PreflightResult:
        """Run the grounded preflight pipeline."""
        route_result = self._router.route(
            RouteRequest(task=request.task, domain=request.domain, top_k=request.top_k)
        )
        if route_result.packet.insufficient_context or not route_result.retained_context:
            return _insufficient_context_result(request)

        retained_context = route_result.retained_context
        generated = self._generator.generate_preflight(request, retained_context)
        draft = _coerce_generated_draft(generated)
        validated = _validate_and_materialize_result(request, retained_context, draft)
        if validated is None:
            return _insufficient_context_result(request)
        return validated


def create_preflight_service(settings: Settings | None = None) -> PreflightService:
    """Build the default preflight service from application settings."""
    active_settings = settings or get_settings()
    router = create_policy_router_service(active_settings)
    generator = _create_default_generator(active_settings)
    return PreflightService(
        router=router,
        generator=generator,
    )


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
        or [
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


def _close_component(component: object | None) -> None:
    close = getattr(component, "close", None)
    if callable(close):
        close()


__all__ = [
    "DraftPolicyGuidance",
    "GeneratedPreflightDraft",
    "PreflightService",
    "create_preflight_service",
]
