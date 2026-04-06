"""Runtime evidence session summary service."""

from __future__ import annotations

from types import TracebackType

from policynim.contracts import RuntimeEvidenceStoreProtocol
from policynim.errors import PolicyNIMError
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import RuntimeEvidenceStore
from policynim.types import (
    RuntimeEvidenceExecutionSummary,
    RuntimeEvidenceSessionSummary,
    RuntimeExecutionEvidenceRecord,
    RuntimeExecutionOutcome,
)


class RuntimeEvidenceReportService:
    """Summarize one stored runtime evidence session."""

    def __init__(self, *, evidence_store: RuntimeEvidenceStoreProtocol) -> None:
        self._evidence_store = evidence_store

    def __enter__(self) -> RuntimeEvidenceReportService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release owned resources held by this service."""
        close = getattr(self._evidence_store, "close", None)
        if callable(close):
            close()

    def report_session(self, session_id: str) -> RuntimeEvidenceSessionSummary:
        """Return one typed summary over the persisted session evidence."""
        events = self._evidence_store.list_session_events(session_id)
        if not events:
            raise PolicyNIMError(f"No runtime evidence found for session {session_id}.")
        return _summarize_session_events(session_id, events)


def create_runtime_evidence_report_service(
    settings: Settings | None = None,
) -> RuntimeEvidenceReportService:
    """Build the default runtime evidence report service from application settings."""
    active_settings = settings or get_settings()
    return RuntimeEvidenceReportService(
        evidence_store=RuntimeEvidenceStore(
            path=resolve_runtime_path(active_settings.runtime_evidence_db_path)
        )
    )


def _summarize_session_events(
    session_id: str,
    events: list[RuntimeExecutionEvidenceRecord],
) -> RuntimeEvidenceSessionSummary:
    executions_by_id: dict[str, list[RuntimeExecutionEvidenceRecord]] = {}
    for event in events:
        executions_by_id.setdefault(event.execution_id, []).append(event)

    execution_summaries = [
        _summarize_execution_events(execution_events)
        for execution_events in executions_by_id.values()
    ]
    counts = _count_execution_outcomes(execution_summaries)
    completed_timestamps = [
        summary.completed_at for summary in execution_summaries if summary.completed_at is not None
    ]
    session_completed_at = None
    if counts["incomplete"] == 0 and completed_timestamps:
        session_completed_at = max(completed_timestamps)

    return RuntimeEvidenceSessionSummary(
        session_id=session_id,
        started_at=min(summary.started_at for summary in execution_summaries),
        completed_at=session_completed_at,
        event_count=len(events),
        execution_count=len(execution_summaries),
        allowed_count=counts["allowed"],
        confirmed_count=counts["confirmed"],
        blocked_count=counts["blocked"],
        refused_count=counts["refused"],
        failed_count=counts["failed"],
        incomplete_count=counts["incomplete"],
        executions=execution_summaries,
    )


def _summarize_execution_events(
    execution_events: list[RuntimeExecutionEvidenceRecord],
) -> RuntimeEvidenceExecutionSummary:
    first_event = execution_events[0]
    started_at = min(event.created_at for event in execution_events)
    terminal_event = _last_terminal_event(execution_events)
    result_event = terminal_event or first_event
    completed_at = None if terminal_event is None else terminal_event.created_at

    matched_rules = result_event.matched_rules or first_event.matched_rules
    citations = result_event.citations or first_event.citations
    return RuntimeEvidenceExecutionSummary(
        execution_id=first_event.execution_id,
        action_kind=first_event.request.kind,
        task=first_event.request.task,
        decision=result_event.decision,
        summary=result_event.summary,
        confirmation_outcome=result_event.confirmation_outcome,
        execution_outcome=result_event.execution_outcome,
        failure_class=result_event.failure_class,
        started_at=started_at,
        completed_at=completed_at,
        matched_rules=list(matched_rules),
        citations=list(citations),
    )


def _last_terminal_event(
    execution_events: list[RuntimeExecutionEvidenceRecord],
) -> RuntimeExecutionEvidenceRecord | None:
    for event in reversed(execution_events):
        if event.event_kind != "decision":
            return event
    return None


def _count_execution_outcomes(
    execution_summaries: list[RuntimeEvidenceExecutionSummary],
) -> dict[str, int]:
    counts = {
        "allowed": 0,
        "confirmed": 0,
        "blocked": 0,
        "refused": 0,
        "failed": 0,
        "incomplete": 0,
    }
    for summary in execution_summaries:
        outcome = summary.execution_outcome
        if outcome is None:
            counts["incomplete"] += 1
            continue
        counts[_outcome_key(outcome)] += 1
    return counts


def _outcome_key(outcome: RuntimeExecutionOutcome) -> str:
    return outcome
