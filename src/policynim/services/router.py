"""Task-aware policy router for PolicyNIM selection packets."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from types import TracebackType

from policynim.contracts import Embedder, IndexStore, Reranker
from policynim.errors import MissingIndexError
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import (
    PolicyMetadata,
    PolicySelectionPacket,
    RouteRequest,
    RouteResult,
    ScoredChunk,
    SelectedPolicy,
    SelectedPolicyEvidence,
    TaskProfile,
    TaskType,
)

_DEFAULT_ROUTING_CANDIDATE_POOL = 15
_MAX_CHUNKS_PER_POLICY = 2
_TaskPattern = tuple[str, str]
_ROUTABLE_TASK_TYPES: tuple[TaskType, ...] = (
    "bug_fix",
    "refactor",
    "api_change",
    "migration",
    "test_change",
    "feature_work",
)
_TASK_TYPE_PATTERNS: dict[TaskType, tuple[_TaskPattern, ...]] = {
    "bug_fix": (
        (r"\bbug\b", "bug"),
        (r"\bfix(?:e[ds])?\b", "fix"),
        (r"\bregression\b", "regression"),
        (r"\berror\b", "error"),
        (r"\bexception\b", "exception"),
        (r"\bfail(?:s|ed|ing|ure)?\b", "failure"),
        (r"\bbroken\b", "broken"),
        (r"\bcrash(?:es|ed|ing)?\b", "crash"),
    ),
    "refactor": (
        (r"\brefactor(?:ing|ed)?\b", "refactor"),
        (r"\bcleanup\b", "cleanup"),
        (r"\bsimplif(?:y|ies|ied|ication)\b", "simplify"),
        (r"\brestructure\b", "restructure"),
        (r"\breorganize\b", "reorganize"),
        (r"\brename\b", "rename"),
        (r"\bdeduplicate\b", "deduplicate"),
    ),
    "api_change": (
        (r"\bapi\b", "api"),
        (r"\bendpoint\b", "endpoint"),
        (r"\bcontract\b", "contract"),
        (r"\bschema\b", "schema"),
        (r"\brequest\b", "request"),
        (r"\bresponse\b", "response"),
        (r"\bversion(?:ing)?\b", "versioning"),
        (r"\bpublic interface\b", "public interface"),
    ),
    "migration": (
        (r"\bmigration\b", "migration"),
        (r"\bmigrate\b", "migrate"),
        (r"\bbackfill\b", "backfill"),
        (r"\bschema migration\b", "schema migration"),
        (r"\bdata migration\b", "data migration"),
    ),
    "test_change": (
        (r"\btest(?:s|ing)?\b", "test"),
        (r"\bpytest\b", "pytest"),
        (r"\bcoverage\b", "coverage"),
        (r"\bassert(?:ion)?\b", "assertion"),
        (r"\bfixture\b", "fixture"),
        (r"\bmock\b", "mock"),
    ),
    "feature_work": (
        (r"\bfeature\b", "feature"),
        (r"\badd\b", "add"),
        (r"\bimplement\b", "implement"),
        (r"\bbuild\b", "build"),
        (r"\bsupport\b", "support"),
        (r"\bcreate\b", "create"),
    ),
}


class PolicyRouterService:
    """Profile tasks, retrieve/rerank evidence, and emit selection packets."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        index_store: IndexStore,
        reranker: Reranker,
    ) -> None:
        self._embedder = embedder
        self._index_store = index_store
        self._reranker = reranker

    def __enter__(self) -> PolicyRouterService:
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
        _close_component(self._embedder)
        _close_component(self._reranker)

    def route(self, request: RouteRequest) -> RouteResult:
        """Return task-aware selected policy evidence for a coding task."""
        _ensure_index_ready(self._index_store)

        profile = profile_task(request.task, explicit_task_type=request.task_type)
        task_embedding = self._embedder.embed_query(request.task)
        dense_candidates = self._index_store.search(
            task_embedding,
            top_k=max(request.top_k, _DEFAULT_ROUTING_CANDIDATE_POOL),
            domain=request.domain,
        )
        if not dense_candidates:
            return _empty_route_result(request, profile)

        route_query = _build_route_query(profile, domain=request.domain)
        reranked_candidates = self._reranker.rerank(
            route_query,
            dense_candidates,
            top_k=max(request.top_k, _DEFAULT_ROUTING_CANDIDATE_POOL),
        )
        retained_context = _retain_policy_evidence(reranked_candidates, top_k=request.top_k)
        if not retained_context:
            return _empty_route_result(request, profile)

        return RouteResult(
            packet=_build_selection_packet(request, profile, retained_context),
            retained_context=retained_context,
        )


def create_policy_router_service(settings: Settings | None = None) -> PolicyRouterService:
    """Build the default policy router from application settings."""
    active_settings = settings or get_settings()
    embedder, reranker = _create_default_router_components(active_settings)
    return PolicyRouterService(
        embedder=embedder,
        index_store=LanceDBIndexStore(
            uri=resolve_runtime_path(active_settings.lancedb_uri),
            table_name=active_settings.lancedb_table,
        ),
        reranker=reranker,
    )


def profile_task(task: str, *, explicit_task_type: TaskType | None = None) -> TaskProfile:
    """Infer a deterministic task profile from freeform task text."""
    normalized_task = task.strip()
    if not normalized_task:
        raise ValueError("Task must not be empty.")
    if explicit_task_type is not None:
        return TaskProfile(
            task=normalized_task,
            task_type=explicit_task_type,
            explicit_task_type=explicit_task_type,
            signals=[f"explicit:{explicit_task_type}"],
        )

    normalized_text = normalized_task.casefold()
    signal_map = {
        task_type: _matching_signals(normalized_text, _TASK_TYPE_PATTERNS[task_type])
        for task_type in _ROUTABLE_TASK_TYPES
    }
    scores = {task_type: len(signal_map[task_type]) for task_type in _ROUTABLE_TASK_TYPES}
    max_score = max(scores.values(), default=0)
    matched_signals = [
        signal for task_type in _ROUTABLE_TASK_TYPES for signal in signal_map[task_type]
    ]
    if max_score == 0:
        return TaskProfile(task=normalized_task, task_type="unknown", signals=[])

    best_types: list[TaskType] = [
        task_type
        for task_type in _ROUTABLE_TASK_TYPES
        if scores[task_type] == max_score and scores[task_type] > 0
    ]
    if len(best_types) != 1:
        return TaskProfile(
            task=normalized_task,
            task_type="unknown",
            signals=matched_signals,
        )

    task_type: TaskType = best_types[0]
    return TaskProfile(
        task=normalized_task,
        task_type=task_type,
        signals=signal_map[task_type],
    )


def _create_default_router_components(settings: Settings) -> tuple[Embedder, Reranker]:
    from policynim.providers import NVIDIAEmbedder, NVIDIAReranker

    return (
        NVIDIAEmbedder.from_settings(settings),
        NVIDIAReranker.from_settings(settings),
    )


def _ensure_index_ready(index_store: IndexStore) -> None:
    if not index_store.exists() or index_store.count() == 0:
        raise MissingIndexError("Run `policynim ingest` before routing policy selection.")


def _matching_signals(text: str, patterns: Sequence[_TaskPattern]) -> list[str]:
    signals: list[str] = []
    for pattern, label in patterns:
        if re.search(pattern, text):
            signals.append(label)
    return signals


def _build_route_query(profile: TaskProfile, *, domain: str | None) -> str:
    task_type_label = profile.task_type.replace("_", " ")
    parts = [profile.task, f"Task type: {task_type_label}."]
    if domain is not None:
        parts.append(f"Policy domain: {domain}.")
    if profile.signals:
        parts.append("Profile signals: " + ", ".join(profile.signals) + ".")
    return " ".join(parts)


def _retain_policy_evidence(chunks: Sequence[ScoredChunk], *, top_k: int) -> list[ScoredChunk]:
    selected: list[ScoredChunk] = []
    counts: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        if len(selected) >= top_k:
            break
        if not _has_evidence_signal(chunk):
            continue

        policy_id = chunk.policy.policy_id
        if counts[policy_id] >= _MAX_CHUNKS_PER_POLICY:
            continue
        selected.append(chunk)
        counts[policy_id] += 1
    return selected


def _has_evidence_signal(chunk: ScoredChunk) -> bool:
    return chunk.score is None or chunk.score > 0.0


def _build_selection_packet(
    request: RouteRequest,
    profile: TaskProfile,
    retained_context: Sequence[ScoredChunk],
) -> PolicySelectionPacket:
    policies_by_id: dict[str, list[ScoredChunk]] = defaultdict(list)
    policy_order: list[PolicyMetadata] = []
    for chunk in retained_context:
        policy_id = chunk.policy.policy_id
        if policy_id not in policies_by_id:
            policy_order.append(chunk.policy)
        policies_by_id[policy_id].append(chunk)

    selected_policies = [
        _selected_policy_from_chunks(profile, policy, policies_by_id[policy.policy_id])
        for policy in policy_order
    ]
    return PolicySelectionPacket(
        task=profile.task,
        domain=request.domain,
        top_k=request.top_k,
        task_type=profile.task_type,
        explicit_task_type=profile.explicit_task_type,
        profile_signals=list(profile.signals),
        selected_policies=selected_policies,
        insufficient_context=not selected_policies,
    )


def _selected_policy_from_chunks(
    profile: TaskProfile,
    policy: PolicyMetadata,
    chunks: Sequence[ScoredChunk],
) -> SelectedPolicy:
    return SelectedPolicy(
        policy_id=policy.policy_id,
        title=policy.title,
        domain=policy.domain,
        reason=_selection_reason(profile, policy, evidence_count=len(chunks)),
        evidence=[_evidence_from_chunk(chunk) for chunk in chunks],
    )


def _selection_reason(
    profile: TaskProfile,
    policy: PolicyMetadata,
    *,
    evidence_count: int,
) -> str:
    task_type_label = profile.task_type.replace("_", " ")
    if profile.explicit_task_type is not None:
        signal_source = "the explicit task-type override"
    elif profile.signals:
        signal_source = "deterministic task-profile signals"
    else:
        signal_source = "the task text"
    return (
        f"Selected for {task_type_label} routing from {evidence_count} retained "
        f"evidence chunk(s) in the {policy.domain} domain using {signal_source}."
    )


def _evidence_from_chunk(chunk: ScoredChunk) -> SelectedPolicyEvidence:
    return SelectedPolicyEvidence(
        chunk_id=chunk.chunk_id,
        path=chunk.path,
        section=chunk.section,
        lines=chunk.lines,
        text=chunk.text,
        score=chunk.score,
    )


def _empty_route_result(request: RouteRequest, profile: TaskProfile) -> RouteResult:
    return RouteResult(
        packet=PolicySelectionPacket(
            task=profile.task,
            domain=request.domain,
            top_k=request.top_k,
            task_type=profile.task_type,
            explicit_task_type=profile.explicit_task_type,
            profile_signals=list(profile.signals),
            selected_policies=[],
            insufficient_context=True,
        ),
        retained_context=[],
    )


def _close_component(component: object | None) -> None:
    close = getattr(component, "close", None)
    if callable(close):
        close()


__all__ = ["PolicyRouterService", "create_policy_router_service", "profile_task"]
