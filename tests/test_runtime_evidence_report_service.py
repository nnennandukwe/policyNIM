"""Tests for the runtime evidence session summary service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from policynim.errors import PolicyNIMError
from policynim.services.runtime_evidence_report import RuntimeEvidenceReportService
from policynim.storage import RuntimeEvidenceStore
from policynim.types import (
    Citation,
    CompiledRuntimeRule,
    RuntimeEvidenceEventKind,
    RuntimeEvidenceSessionSummary,
    RuntimeExecutionEvidenceRecord,
    RuntimeExecutionOutcome,
    ShellCommandExecutionMetadata,
    ShellCommandExecutionRequest,
)


def make_record(
    *,
    event_id: str,
    execution_id: str,
    session_id: str = "session-1",
    created_at: datetime | None = None,
    event_kind: RuntimeEvidenceEventKind = "allowed",
    execution_outcome: RuntimeExecutionOutcome | None = None,
    failure_class: str | None = None,
    matched_rules: list[CompiledRuntimeRule] | None = None,
    citations: list[Citation] | None = None,
) -> RuntimeExecutionEvidenceRecord:
    timestamp = created_at or datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    effective_outcome = execution_outcome
    if effective_outcome is None and event_kind != "decision":
        effective_outcome = cast(RuntimeExecutionOutcome, event_kind)
    return RuntimeExecutionEvidenceRecord(
        event_id=event_id,
        execution_id=execution_id,
        session_id=session_id,
        created_at=timestamp,
        event_kind=event_kind,
        request=ShellCommandExecutionRequest(
            kind="shell_command",
            task=f"Run task for {execution_id}.",
            cwd=Path("/tmp/workspace"),
            session_id=session_id,
            command=["make", execution_id],
        ),
        decision="allow" if event_kind != "blocked" else "block",
        summary="Decision summary.",
        matched_rules=matched_rules or [],
        citations=citations or [],
        confirmation_outcome="not_required",
        execution_outcome=effective_outcome,
        result_metadata=(
            None
            if event_kind == "decision"
            else ShellCommandExecutionMetadata(exit_code=0, duration_ms=12.5)
        ),
        failure_class=failure_class,
        residual_uncertainty=None,
    )


def make_rule() -> CompiledRuntimeRule:
    return CompiledRuntimeRule(
        action="shell_command",
        effect="confirm",
        reason="Review deploy commands.",
        command_regexes=["^make deploy$"],
        policy_id="RUNTIME-001",
        title="Runtime Controls",
        domain="security",
        source_path="policies/runtime.md",
        start_line=5,
        end_line=9,
    )


def make_citation() -> Citation:
    return Citation(
        policy_id="RUNTIME-001",
        title="Runtime Controls",
        path="policies/runtime.md",
        section="Runtime > Rules",
        lines="5-9",
        chunk_id="RUNTIME-1",
    )


def test_runtime_evidence_report_service_summarizes_single_allowed_execution(
    tmp_path: Path,
) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    first_created_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    second_created_at = first_created_at + timedelta(seconds=2)
    store.append_event(
        make_record(
            event_id="event-1",
            execution_id="exec-1",
            created_at=first_created_at,
            event_kind="decision",
        )
    )
    store.append_event(
        make_record(
            event_id="event-2",
            execution_id="exec-1",
            created_at=second_created_at,
            event_kind="allowed",
        )
    )

    service = RuntimeEvidenceReportService(evidence_store=store)

    summary = service.report_session("session-1")

    assert isinstance(summary, RuntimeEvidenceSessionSummary)
    assert summary.session_id == "session-1"
    assert summary.event_count == 2
    assert summary.execution_count == 1
    assert summary.allowed_count == 1
    assert summary.incomplete_count == 0
    assert summary.started_at == first_created_at
    assert summary.completed_at == second_created_at
    assert summary.executions[0].execution_id == "exec-1"
    assert summary.executions[0].action_kind == "shell_command"
    assert summary.executions[0].execution_outcome == "allowed"


def test_runtime_evidence_report_service_preserves_execution_order_and_outcome_counts(
    tmp_path: Path,
) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    base_time = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    store.append_event(
        make_record(
            event_id="event-1",
            execution_id="exec-1",
            created_at=base_time,
            event_kind="decision",
        )
    )
    store.append_event(
        make_record(
            event_id="event-2",
            execution_id="exec-1",
            created_at=base_time + timedelta(seconds=1),
            event_kind="allowed",
        )
    )
    store.append_event(
        make_record(
            event_id="event-3",
            execution_id="exec-2",
            created_at=base_time + timedelta(seconds=3),
            event_kind="decision",
        )
    )
    store.append_event(
        make_record(
            event_id="event-4",
            execution_id="exec-2",
            created_at=base_time + timedelta(seconds=4),
            event_kind="blocked",
        )
    )

    service = RuntimeEvidenceReportService(evidence_store=store)

    summary = service.report_session("session-1")

    assert [execution.execution_id for execution in summary.executions] == ["exec-1", "exec-2"]
    assert summary.allowed_count == 1
    assert summary.blocked_count == 1
    assert summary.execution_count == 2
    assert summary.completed_at == base_time + timedelta(seconds=4)


def test_runtime_evidence_report_service_keeps_latest_failure_metadata_and_completion_state(
    tmp_path: Path,
) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    base_time = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    store.append_event(
        make_record(
            event_id="event-1",
            execution_id="exec-1",
            created_at=base_time,
            event_kind="decision",
        )
    )
    store.append_event(
        make_record(
            event_id="event-2",
            execution_id="exec-1",
            created_at=base_time + timedelta(seconds=1),
            event_kind="failed",
            failure_class="non_zero_exit",
        )
    )
    store.append_event(
        make_record(
            event_id="event-3",
            execution_id="exec-2",
            created_at=base_time + timedelta(seconds=3),
            event_kind="decision",
        )
    )

    service = RuntimeEvidenceReportService(evidence_store=store)

    summary = service.report_session("session-1")

    assert summary.failed_count == 1
    assert summary.incomplete_count == 1
    assert summary.executions[0].failure_class == "non_zero_exit"
    assert summary.executions[0].completed_at == base_time + timedelta(seconds=1)
    assert summary.executions[1].execution_outcome is None
    assert summary.executions[1].completed_at is None


def test_runtime_evidence_report_service_preserves_rules_and_citations(
    tmp_path: Path,
) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    rule = make_rule()
    citation = make_citation()
    store.append_event(
        make_record(
            event_id="event-1",
            execution_id="exec-1",
            event_kind="decision",
            matched_rules=[rule],
            citations=[citation],
        )
    )
    store.append_event(
        make_record(
            event_id="event-2",
            execution_id="exec-1",
            event_kind="confirmed",
            execution_outcome="confirmed",
            matched_rules=[rule],
            citations=[citation],
        )
    )

    service = RuntimeEvidenceReportService(evidence_store=store)

    summary = service.report_session("session-1")

    assert summary.confirmed_count == 1
    assert summary.executions[0].matched_rules == [rule]
    assert summary.executions[0].citations == [citation]


def test_runtime_evidence_report_service_raises_for_unknown_session(tmp_path: Path) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    service = RuntimeEvidenceReportService(evidence_store=store)

    with pytest.raises(PolicyNIMError, match="missing-session"):
        service.report_session("missing-session")
