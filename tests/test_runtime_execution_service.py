"""Tests for the runtime execution service."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from policynim.errors import RuntimeEvidencePersistenceError
from policynim.services.runtime_execution import RuntimeExecutionService
from policynim.types import (
    FileWriteActionRequest,
    FileWriteExecutionMetadata,
    HTTPRequestActionRequest,
    HTTPRequestExecutionMetadata,
    RuntimeActionRequest,
    RuntimeDecision,
    RuntimeDecisionResult,
    RuntimeExecutionEvidenceRecord,
    ShellCommandActionRequest,
    ShellCommandExecutionMetadata,
)


class StubDecisionService:
    """Static runtime decision service for execution tests."""

    def __init__(self, decision: RuntimeDecision, *, summary: str | None = None) -> None:
        self._decision: RuntimeDecision = decision
        self._summary = summary or "Decision summary."
        self.last_request: RuntimeActionRequest | None = None
        self.closed = False

    def decide(self, request: RuntimeActionRequest) -> RuntimeDecisionResult:
        self.last_request = request
        return RuntimeDecisionResult(
            request=request,
            decision=self._decision,
            summary=self._summary,
            matched_rules=[],
            citations=[],
        )

    def close(self) -> None:
        self.closed = True


class StubEvidenceStore:
    """In-memory evidence store with injectable failures."""

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self._fail_on_call = fail_on_call
        self._calls = 0
        self.events: list[RuntimeExecutionEvidenceRecord] = []
        self.closed = False

    def append_event(self, record: RuntimeExecutionEvidenceRecord) -> None:
        self._calls += 1
        if self._fail_on_call == self._calls:
            raise OSError("disk full")
        self.events.append(record)

    def list_session_events(self, session_id: str) -> list[RuntimeExecutionEvidenceRecord]:
        return [event for event in self.events if event.session_id == session_id]

    def close(self) -> None:
        self.closed = True


class StubHTTPClient:
    """Minimal HTTP client test double."""

    def __init__(self, *, status_code: int = 200, exc: Exception | None = None) -> None:
        self._status_code = status_code
        self._exc = exc
        self.closed = False

    def request(self, method: str, url: str) -> httpx.Response:
        if self._exc is not None:
            raise self._exc
        return httpx.Response(
            self._status_code,
            request=httpx.Request(method, url),
        )

    def close(self) -> None:
        self.closed = True


def test_runtime_execution_service_runs_allowed_shell_command(tmp_path: Path) -> None:
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
    )

    result = service.execute(
        ShellCommandActionRequest(
            kind="shell_command",
            task="Run a passing shell command.",
            cwd=tmp_path,
            command=[sys.executable, "-c", "raise SystemExit(0)"],
        )
    )

    assert result.execution_outcome == "allowed"
    assert result.confirmation_outcome == "not_required"
    assert result.session_id
    assert result.request.session_id == result.session_id
    assert [event.event_kind for event in evidence_store.events] == ["decision", "allowed"]


def test_runtime_execution_service_runs_confirmed_shell_command(tmp_path: Path) -> None:
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("confirm", summary="Review deploy commands."),
        evidence_store=evidence_store,
        confirmer=lambda decision: decision.summary == "Review deploy commands.",
    )

    result = service.execute(
        ShellCommandActionRequest(
            kind="shell_command",
            task="Run a confirmed shell command.",
            cwd=tmp_path,
            command=[sys.executable, "-c", "raise SystemExit(0)"],
        )
    )

    assert result.execution_outcome == "confirmed"
    assert result.confirmation_outcome == "confirmed"
    assert [event.event_kind for event in evidence_store.events] == ["decision", "confirmed"]


def test_runtime_execution_service_returns_refused_without_executing(tmp_path: Path) -> None:
    target_path = tmp_path / "notes.txt"
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("confirm"),
        evidence_store=evidence_store,
        confirmer=lambda _: False,
    )

    result = service.execute(
        FileWriteActionRequest(
            kind="file_write",
            task="Write a guarded file.",
            cwd=tmp_path,
            path=Path(target_path.name),
            content="secret payload",
        )
    )

    assert result.execution_outcome == "refused"
    assert result.confirmation_outcome == "refused"
    assert target_path.exists() is False
    assert [event.event_kind for event in evidence_store.events] == ["decision", "refused"]


def test_runtime_execution_service_returns_blocked_without_executing(tmp_path: Path) -> None:
    target_path = tmp_path / "blocked.txt"
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("block", summary="Protect this file."),
        evidence_store=evidence_store,
    )

    result = service.execute(
        FileWriteActionRequest(
            kind="file_write",
            task="Write a blocked file.",
            cwd=tmp_path,
            path=Path(target_path.name),
            content="blocked payload",
        )
    )

    assert result.execution_outcome == "blocked"
    assert result.confirmation_outcome == "not_required"
    assert target_path.exists() is False
    assert [event.event_kind for event in evidence_store.events] == ["decision", "blocked"]


def test_runtime_execution_service_returns_failed_for_non_zero_shell_exit(tmp_path: Path) -> None:
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
    )

    result = service.execute(
        ShellCommandActionRequest(
            kind="shell_command",
            task="Run a failing shell command.",
            cwd=tmp_path,
            command=[sys.executable, "-c", "raise SystemExit(7)"],
        )
    )

    assert result.execution_outcome == "failed"
    assert result.failure_class == "non_zero_exit"
    assert [event.event_kind for event in evidence_store.events] == ["decision", "failed"]


def test_runtime_execution_service_returns_failed_for_shell_timeout(tmp_path: Path) -> None:
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
        shell_timeout_seconds=0.01,
    )

    result = service.execute(
        ShellCommandActionRequest(
            kind="shell_command",
            task="Run a hanging shell command.",
            cwd=tmp_path,
            command=[sys.executable, "-c", "import time; time.sleep(0.2)"],
        )
    )

    assert result.execution_outcome == "failed"
    assert result.failure_class == "timeout"
    assert isinstance(result.result_metadata, ShellCommandExecutionMetadata)
    assert result.result_metadata.exit_code is None
    assert result.result_metadata.duration_ms >= 0.0
    assert [event.event_kind for event in evidence_store.events] == ["decision", "failed"]


def test_runtime_execution_service_returns_failed_for_missing_file_parent(tmp_path: Path) -> None:
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
    )

    result = service.execute(
        FileWriteActionRequest(
            kind="file_write",
            task="Write into a missing directory.",
            cwd=tmp_path,
            path=Path("missing/notes.txt"),
            content="payload",
        )
    )

    assert result.execution_outcome == "failed"
    assert result.failure_class == "missing_parent"
    assert [event.event_kind for event in evidence_store.events] == ["decision", "failed"]


def test_runtime_execution_service_runs_allowed_file_write(tmp_path: Path) -> None:
    target_path = tmp_path / "notes.txt"
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
    )

    result = service.execute(
        FileWriteActionRequest(
            kind="file_write",
            task="Write an allowed file.",
            cwd=tmp_path,
            path=Path(target_path.name),
            content="hello world",
        )
    )

    assert result.execution_outcome == "allowed"
    assert target_path.read_text(encoding="utf-8") == "hello world"
    assert result.result_metadata == FileWriteExecutionMetadata(
        path=target_path,
        bytes_written=len(b"hello world"),
    )
    assert [event.event_kind for event in evidence_store.events] == ["decision", "allowed"]


def test_runtime_execution_service_returns_failed_for_http_transport_error(
    tmp_path: Path,
) -> None:
    http_request = HTTPRequestActionRequest.model_validate(
        {
            "kind": "http_request",
            "task": "Call a remote API.",
            "cwd": tmp_path,
            "method": "GET",
            "url": "https://example.com/api",
        }
    )
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
        http_client=StubHTTPClient(
            exc=httpx.ConnectError(
                "connection failed",
                request=httpx.Request("GET", "https://example.com/api"),
            )
        ),
    )

    result = service.execute(http_request)

    assert result.execution_outcome == "failed"
    assert result.failure_class == "connection"
    assert [event.event_kind for event in evidence_store.events] == ["decision", "failed"]


def test_runtime_execution_service_runs_allowed_http_request(tmp_path: Path) -> None:
    http_request = HTTPRequestActionRequest.model_validate(
        {
            "kind": "http_request",
            "task": "Call a healthy remote API.",
            "cwd": tmp_path,
            "method": "GET",
            "url": "https://example.com/api",
        }
    )
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
        http_client=StubHTTPClient(status_code=204),
    )

    result = service.execute(http_request)

    assert result.execution_outcome == "allowed"
    assert isinstance(result.result_metadata, HTTPRequestExecutionMetadata)
    assert result.result_metadata.status_code == 204
    assert result.result_metadata.duration_ms >= 0.0
    assert [event.event_kind for event in evidence_store.events] == ["decision", "allowed"]


def test_runtime_execution_service_returns_failed_for_http_status_error(
    tmp_path: Path,
) -> None:
    http_request = HTTPRequestActionRequest.model_validate(
        {
            "kind": "http_request",
            "task": "Call an unhealthy remote API.",
            "cwd": tmp_path,
            "method": "GET",
            "url": "https://example.com/api",
        }
    )
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
        http_client=StubHTTPClient(status_code=429),
    )

    result = service.execute(http_request)

    assert result.execution_outcome == "failed"
    assert result.failure_class == "http_status"
    assert isinstance(result.result_metadata, HTTPRequestExecutionMetadata)
    assert result.result_metadata.status_code == 429
    assert result.result_metadata.duration_ms >= 0.0
    assert [event.event_kind for event in evidence_store.events] == ["decision", "failed"]


def test_runtime_execution_service_fails_closed_when_confirmer_is_missing(tmp_path: Path) -> None:
    target_path = tmp_path / "guarded.txt"
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("confirm"),
        evidence_store=evidence_store,
    )

    result = service.execute(
        FileWriteActionRequest(
            kind="file_write",
            task="Write a confirmed file.",
            cwd=tmp_path,
            path=Path(target_path.name),
            content="payload",
        )
    )

    assert result.execution_outcome == "failed"
    assert result.confirmation_outcome == "unavailable"
    assert result.failure_class == "confirmation_unavailable"
    assert target_path.exists() is False
    assert [event.event_kind for event in evidence_store.events] == ["decision", "failed"]


def test_runtime_execution_service_returns_failed_when_confirmer_raises(tmp_path: Path) -> None:
    target_path = tmp_path / "guarded.txt"
    evidence_store = StubEvidenceStore()

    def raise_from_confirmer(_: RuntimeDecisionResult) -> bool:
        raise ValueError("prompt failed")

    service = RuntimeExecutionService(
        decision_service=StubDecisionService("confirm"),
        evidence_store=evidence_store,
        confirmer=raise_from_confirmer,
    )

    result = service.execute(
        FileWriteActionRequest(
            kind="file_write",
            task="Write a confirmed file.",
            cwd=tmp_path,
            path=Path(target_path.name),
            content="payload",
        )
    )

    assert result.execution_outcome == "failed"
    assert result.confirmation_outcome == "unavailable"
    assert result.failure_class == "confirmation_error"
    assert target_path.exists() is False
    assert [event.event_kind for event in evidence_store.events] == ["decision", "failed"]


def test_runtime_execution_service_raises_when_initial_evidence_persistence_fails(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "notes.txt"
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=StubEvidenceStore(fail_on_call=1),
    )

    with pytest.raises(RuntimeEvidencePersistenceError, match="initial runtime decision evidence"):
        service.execute(
            FileWriteActionRequest(
                kind="file_write",
                task="Write without initial evidence.",
                cwd=tmp_path,
                path=Path(target_path.name),
                content="payload",
            )
        )

    assert target_path.exists() is False


def test_runtime_execution_service_raises_when_terminal_evidence_persistence_fails(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "notes.txt"
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=StubEvidenceStore(fail_on_call=2),
    )

    with pytest.raises(RuntimeEvidencePersistenceError, match="terminal runtime evidence"):
        service.execute(
            FileWriteActionRequest(
                kind="file_write",
                task="Write with failing terminal evidence.",
                cwd=tmp_path,
                path=Path(target_path.name),
                content="payload",
            )
        )

    assert target_path.read_text(encoding="utf-8") == "payload"


def test_runtime_execution_service_redacts_file_content_from_results_and_evidence(
    tmp_path: Path,
) -> None:
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
    )

    result = service.execute(
        FileWriteActionRequest(
            kind="file_write",
            task="Write a file without persisting the body.",
            cwd=tmp_path,
            path=Path("notes.txt"),
            content="super-secret-body",
        )
    )

    result_payload = result.model_dump(mode="json")
    event_payloads = [event.model_dump(mode="json") for event in evidence_store.events]

    assert "content" not in result_payload["request"]
    assert all("content" not in payload["request"] for payload in event_payloads)


def test_runtime_execution_service_does_not_persist_stdout_or_stderr(tmp_path: Path) -> None:
    evidence_store = StubEvidenceStore()
    service = RuntimeExecutionService(
        decision_service=StubDecisionService("allow"),
        evidence_store=evidence_store,
    )

    result = service.execute(
        ShellCommandActionRequest(
            kind="shell_command",
            task="Run a command that writes to stderr.",
            cwd=tmp_path,
            command=[
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('out'); sys.stderr.write('err'); raise SystemExit(1)",
            ],
        )
    )

    result_payload = result.model_dump(mode="json")
    event_payloads = [event.model_dump(mode="json") for event in evidence_store.events]

    assert result.execution_outcome == "failed"
    assert "stdout" not in result_payload
    assert "stderr" not in result_payload
    assert all("stdout" not in payload for payload in event_payloads)
    assert all("stderr" not in payload for payload in event_payloads)
