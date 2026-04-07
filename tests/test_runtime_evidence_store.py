"""Tests for the runtime execution SQLite evidence store."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from policynim.storage import RuntimeEvidenceStore
from policynim.types import (
    FileWriteExecutionMetadata,
    FileWriteExecutionRequest,
    HTTPRequestExecutionMetadata,
    HTTPRequestExecutionRequest,
    RuntimeEvidenceEventKind,
    RuntimeExecutionEvidenceRecord,
    RuntimeExecutionMetadata,
    RuntimeExecutionOutcome,
    RuntimeExecutionRequest,
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
    request: RuntimeExecutionRequest | None = None,
    result_metadata: RuntimeExecutionMetadata | None = None,
) -> RuntimeExecutionEvidenceRecord:
    timestamp = created_at or datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    execution_outcome: RuntimeExecutionOutcome | None = None
    if event_kind != "decision":
        execution_outcome = cast(RuntimeExecutionOutcome, event_kind)
    return RuntimeExecutionEvidenceRecord(
        event_id=event_id,
        execution_id=execution_id,
        session_id=session_id,
        created_at=timestamp,
        event_kind=event_kind,
        request=(
            request
            if request is not None
            else ShellCommandExecutionRequest(
                kind="shell_command",
                task="Run tests.",
                cwd=Path("/tmp/workspace"),
                session_id=session_id,
                command=["make", "test"],
            )
        ),
        decision="allow",
        summary="No runtime policy rules matched this action.",
        matched_rules=[],
        citations=[],
        confirmation_outcome="not_required",
        execution_outcome=execution_outcome,
        result_metadata=(
            result_metadata
            if result_metadata is not None
            else (
                None
                if event_kind == "decision"
                else ShellCommandExecutionMetadata(exit_code=0, duration_ms=12.5)
            )
        ),
        failure_class=None,
        residual_uncertainty=None,
    )


def test_runtime_evidence_store_initializes_schema_idempotently(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime_evidence.sqlite3"

    first = RuntimeEvidenceStore(path=db_path)
    second = RuntimeEvidenceStore(path=db_path)

    assert first.path == db_path
    assert second.path == db_path
    assert second.list_session_events("missing-session") == []


def test_runtime_evidence_store_lists_session_events_in_created_order(tmp_path: Path) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    first_created_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    second_created_at = first_created_at + timedelta(seconds=1)

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

    events = store.list_session_events("session-1")

    assert [event.event_id for event in events] == ["event-1", "event-2"]
    assert events[1].result_metadata == ShellCommandExecutionMetadata(exit_code=0, duration_ms=12.5)


def test_runtime_evidence_store_preserves_append_order_for_out_of_order_timestamps(
    tmp_path: Path,
) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    first_created_at = datetime(2026, 4, 5, 12, 5, tzinfo=UTC)
    second_created_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)

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

    events = store.list_session_events("session-1")

    assert [event.event_id for event in events] == ["event-1", "event-2"]


def test_runtime_evidence_store_reset_for_tests_clears_existing_state(tmp_path: Path) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    store.append_event(make_record(event_id="event-1", execution_id="exec-1"))

    store.reset_for_tests()

    assert store.list_session_events("session-1") == []


def test_runtime_evidence_store_survives_reopen_cycles(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime_evidence.sqlite3"
    first = RuntimeEvidenceStore(path=db_path)
    first.append_event(make_record(event_id="event-1", execution_id="exec-1"))
    first.close()

    second = RuntimeEvidenceStore(path=db_path)

    events = second.list_session_events("session-1")

    assert [event.event_id for event in events] == ["event-1"]


def test_runtime_evidence_store_round_trips_file_write_payloads(tmp_path: Path) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    store.append_event(
        make_record(
            event_id="event-1",
            execution_id="exec-1",
            request=FileWriteExecutionRequest(
                kind="file_write",
                task="Write a note.",
                cwd=tmp_path,
                session_id="session-1",
                path=Path("notes.txt"),
            ),
            result_metadata=FileWriteExecutionMetadata(
                path=tmp_path / "notes.txt",
                bytes_written=12,
            ),
        )
    )

    event = store.list_session_events("session-1")[0]

    assert event.request.kind == "file_write"
    assert event.request.path == Path("notes.txt")
    assert event.result_metadata == FileWriteExecutionMetadata(
        path=tmp_path / "notes.txt",
        bytes_written=12,
    )


def test_runtime_evidence_store_round_trips_http_request_payloads(tmp_path: Path) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    store.append_event(
        make_record(
            event_id="event-1",
            execution_id="exec-1",
            request=HTTPRequestExecutionRequest(
                kind="http_request",
                task="Call a remote API.",
                cwd=tmp_path,
                session_id="session-1",
                method="GET",
                url="https://example.com/api",
            ),
            result_metadata=HTTPRequestExecutionMetadata(
                status_code=204,
                duration_ms=3.5,
            ),
        )
    )

    event = store.list_session_events("session-1")[0]

    assert event.request.kind == "http_request"
    assert event.request.method == "GET"
    assert str(event.request.url) == "https://example.com/api"
    assert event.result_metadata == HTTPRequestExecutionMetadata(
        status_code=204,
        duration_ms=3.5,
    )


def test_runtime_evidence_store_filters_requested_session_from_shared_db(tmp_path: Path) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    store.append_event(
        make_record(
            event_id="event-1",
            execution_id="exec-1",
            session_id="session-1",
        )
    )
    store.append_event(
        make_record(
            event_id="event-2",
            execution_id="exec-2",
            session_id="session-2",
        )
    )

    events = store.list_session_events("session-2")

    assert [event.event_id for event in events] == ["event-2"]


def test_runtime_evidence_store_handles_concurrent_appends(tmp_path: Path) -> None:
    store = RuntimeEvidenceStore(path=tmp_path / "runtime_evidence.sqlite3")
    base_time = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)

    def append_one(index: int) -> None:
        store.append_event(
            make_record(
                event_id=f"event-{index}",
                execution_id=f"exec-{index}",
                created_at=base_time + timedelta(microseconds=index),
            )
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append_one, range(20)))

    events = store.list_session_events("session-1")

    assert len(events) == 20
    assert {event.event_id for event in events} == {f"event-{index}" for index in range(20)}
