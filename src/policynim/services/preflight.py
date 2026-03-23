"""Preflight service for grounded PolicyNIM synthesis."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from policynim.contracts import Embedder, Generator, IndexStore, Reranker
from policynim.errors import MissingIndexError
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import (
    Citation,
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    PolicyGuidance,
    PreflightRequest,
    PreflightResult,
    ScoredChunk,
)

DraftPolicyGuidance = GeneratedPolicyGuidance

_DEFAULT_RETRIEVAL_CANDIDATE_POOL = 15
_MAX_CHUNKS_PER_POLICY = 2
_INSUFFICIENT_CONTEXT_SUMMARY = (
    "PolicyNIM could not find enough grounded policy evidence for this task."
)


class PreflightService:
    """Retrieve, rerank, synthesize, and validate grounded policy guidance."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        index_store: IndexStore,
        reranker: Reranker,
        generator: Generator,
    ) -> None:
        self._embedder = embedder
        self._index_store = index_store
        self._reranker = reranker
        self._generator = generator

    def preflight(self, request: PreflightRequest) -> PreflightResult:
        """Run the grounded preflight pipeline."""
        _ensure_index_ready(self._index_store)

        task_embedding = self._embedder.embed_query(request.task)
        dense_candidates = self._index_store.search(
            task_embedding,
            top_k=max(request.top_k, _DEFAULT_RETRIEVAL_CANDIDATE_POOL),
            domain=request.domain,
        )
        if not dense_candidates:
            return _insufficient_context_result(request)

        reranked_candidates = self._reranker.rerank(
            request.task,
            dense_candidates,
            top_k=max(request.top_k, _DEFAULT_RETRIEVAL_CANDIDATE_POOL),
        )
        if not reranked_candidates:
            return _insufficient_context_result(request)

        retained_context = _retain_diverse_context(reranked_candidates, top_k=request.top_k)
        if not retained_context:
            return _insufficient_context_result(request)

        generated = self._generator.generate_preflight(request, retained_context)
        draft = _coerce_generated_draft(generated)
        validated = _validate_and_materialize_result(request, retained_context, draft)
        if validated is None:
            return _insufficient_context_result(request)
        return validated


def create_preflight_service(settings: Settings | None = None) -> PreflightService:
    """Build the default preflight service from application settings."""
    active_settings = settings or get_settings()
    embedder, reranker, generator = _create_default_preflight_components(active_settings)
    return PreflightService(
        embedder=embedder,
        index_store=LanceDBIndexStore(
            uri=resolve_runtime_path(active_settings.lancedb_uri),
            table_name=active_settings.lancedb_table,
        ),
        reranker=reranker,
        generator=generator,
    )


def _create_default_preflight_components(
    settings: Settings,
) -> tuple[Embedder, Reranker, Generator]:
    from policynim.providers import NVIDIAEmbedder, NVIDIAGenerator, NVIDIAReranker

    return (
        NVIDIAEmbedder.from_settings(settings),
        NVIDIAReranker.from_settings(settings),
        NVIDIAGenerator.from_settings(settings),
    )


def _ensure_index_ready(index_store: IndexStore) -> None:
    if not index_store.exists() or index_store.count() == 0:
        raise MissingIndexError("Run `policynim ingest` before using grounded preflight.")


def _retain_diverse_context(chunks: Sequence[ScoredChunk], *, top_k: int) -> list[ScoredChunk]:
    selected: list[ScoredChunk] = []
    counts: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        policy_id = chunk.policy.policy_id
        if counts[policy_id] >= _MAX_CHUNKS_PER_POLICY:
            continue
        selected.append(chunk)
        counts[policy_id] += 1
        if len(selected) >= top_k:
            break
    return selected


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


__all__ = [
    "DraftPolicyGuidance",
    "GeneratedPreflightDraft",
    "PreflightService",
    "create_preflight_service",
]
