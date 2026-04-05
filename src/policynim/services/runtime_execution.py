"""Runtime execution service with durable evidence persistence."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import TracebackType
from uuid import uuid4

import httpx

from policynim.contracts import RuntimeEvidenceStoreProtocol
from policynim.errors import RuntimeEvidencePersistenceError
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.runtime_decision import (
    RuntimeDecisionService,
    create_runtime_decision_service,
)
from policynim.settings import Settings, get_settings
from policynim.storage import RuntimeEvidenceStore
from policynim.types import (
    FileWriteActionRequest,
    FileWriteExecutionMetadata,
    FileWriteExecutionRequest,
    HTTPRequestActionRequest,
    HTTPRequestExecutionMetadata,
    HTTPRequestExecutionRequest,
    RuntimeActionRequest,
    RuntimeConfirmationOutcome,
    RuntimeDecisionResult,
    RuntimeEvidenceEventKind,
    RuntimeExecutionEvidenceRecord,
    RuntimeExecutionMetadata,
    RuntimeExecutionOutcome,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    ShellCommandActionRequest,
    ShellCommandExecutionMetadata,
    ShellCommandExecutionRequest,
)

_DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class _ActionExecutionResult:
    """Internal result from one concrete action runner."""

    succeeded: bool
    metadata: RuntimeExecutionMetadata | None = None
    failure_class: str | None = None


class RuntimeExecutionService:
    """Enforce runtime decisions, execute actions, and persist evidence."""

    def __init__(
        self,
        *,
        decision_service: RuntimeDecisionService,
        evidence_store: RuntimeEvidenceStoreProtocol,
        confirmer: Callable[[RuntimeDecisionResult], bool] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._decision_service = decision_service
        self._evidence_store = evidence_store
        self._confirmer = confirmer
        self._http_client = http_client or httpx.Client(
            timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        self._owns_http_client = http_client is None

    def __enter__(self) -> RuntimeExecutionService:
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
        _close_component(self._decision_service)
        _close_component(self._evidence_store)
        if self._owns_http_client:
            self._http_client.close()

    def execute(self, request: RuntimeActionRequest) -> RuntimeExecutionResult:
        """Decide, optionally confirm, execute, and persist runtime evidence."""
        session_id = request.session_id or str(uuid4())
        execution_id = str(uuid4())
        request_with_session = request.model_copy(update={"session_id": session_id})
        decision_result = self._decision_service.decide(request_with_session)
        sanitized_request = _sanitize_request(decision_result.request)
        residual_uncertainty = _residual_uncertainty_for_decision(decision_result)

        initial_event = _build_evidence_record(
            execution_id=execution_id,
            session_id=session_id,
            event_kind="decision",
            request=sanitized_request,
            decision_result=decision_result,
            confirmation_outcome=_initial_confirmation_outcome(decision_result),
            execution_outcome=None,
            result_metadata=None,
            failure_class=None,
            residual_uncertainty=residual_uncertainty,
        )
        _append_initial_event(self._evidence_store, initial_event)

        if decision_result.decision == "block":
            result = _build_execution_result(
                execution_id=execution_id,
                session_id=session_id,
                request=sanitized_request,
                decision_result=decision_result,
                confirmation_outcome="not_required",
                execution_outcome="blocked",
                result_metadata=None,
                failure_class=None,
                residual_uncertainty=residual_uncertainty,
            )
            _append_terminal_event(
                self._evidence_store,
                result,
                event_kind="blocked",
                action_started=False,
            )
            return result

        confirmation_outcome: RuntimeConfirmationOutcome = "not_required"
        failure_class: str | None = None
        if decision_result.decision == "confirm":
            confirmation_outcome, failure_class = _run_confirmation(
                self._confirmer,
                decision_result=decision_result,
            )
            if confirmation_outcome == "refused":
                result = _build_execution_result(
                    execution_id=execution_id,
                    session_id=session_id,
                    request=sanitized_request,
                    decision_result=decision_result,
                    confirmation_outcome=confirmation_outcome,
                    execution_outcome="refused",
                    result_metadata=None,
                    failure_class=None,
                    residual_uncertainty=residual_uncertainty,
                )
                _append_terminal_event(
                    self._evidence_store,
                    result,
                    event_kind="refused",
                    action_started=False,
                )
                return result
            if confirmation_outcome == "unavailable":
                result = _build_execution_result(
                    execution_id=execution_id,
                    session_id=session_id,
                    request=sanitized_request,
                    decision_result=decision_result,
                    confirmation_outcome=confirmation_outcome,
                    execution_outcome="failed",
                    result_metadata=None,
                    failure_class=failure_class,
                    residual_uncertainty=residual_uncertainty,
                )
                _append_terminal_event(
                    self._evidence_store,
                    result,
                    event_kind="failed",
                    action_started=False,
                )
                return result

        runner_result = _run_action(self._http_client, request_with_session)
        execution_outcome: RuntimeExecutionOutcome
        if runner_result.succeeded:
            execution_outcome = "confirmed" if decision_result.decision == "confirm" else "allowed"
        else:
            execution_outcome = "failed"

        result = _build_execution_result(
            execution_id=execution_id,
            session_id=session_id,
            request=sanitized_request,
            decision_result=decision_result,
            confirmation_outcome=confirmation_outcome,
            execution_outcome=execution_outcome,
            result_metadata=runner_result.metadata,
            failure_class=runner_result.failure_class,
            residual_uncertainty=residual_uncertainty,
        )
        _append_terminal_event(
            self._evidence_store,
            result,
            event_kind=execution_outcome,
            action_started=True,
        )
        return result


def create_runtime_execution_service(
    settings: Settings | None = None,
    *,
    confirmer: Callable[[RuntimeDecisionResult], bool] | None = None,
) -> RuntimeExecutionService:
    """Build the default runtime execution service from application settings."""
    active_settings = settings or get_settings()
    return RuntimeExecutionService(
        decision_service=create_runtime_decision_service(active_settings),
        evidence_store=RuntimeEvidenceStore(
            path=resolve_runtime_path(active_settings.runtime_evidence_db_path)
        ),
        confirmer=confirmer,
    )


def _sanitize_request(request: RuntimeActionRequest) -> RuntimeExecutionRequest:
    if isinstance(request, ShellCommandActionRequest):
        return ShellCommandExecutionRequest.model_validate(request.model_dump(mode="json"))
    if isinstance(request, FileWriteActionRequest):
        payload = request.model_dump(mode="json", exclude={"content"})
        return FileWriteExecutionRequest.model_validate(payload)
    if isinstance(request, HTTPRequestActionRequest):
        return HTTPRequestExecutionRequest.model_validate(request.model_dump(mode="json"))
    raise TypeError(f"Unsupported runtime action request type: {type(request)!r}.")


def _initial_confirmation_outcome(
    decision_result: RuntimeDecisionResult,
) -> RuntimeConfirmationOutcome:
    if decision_result.decision == "confirm":
        return "unavailable"
    return "not_required"


def _run_confirmation(
    confirmer: Callable[[RuntimeDecisionResult], bool] | None,
    *,
    decision_result: RuntimeDecisionResult,
) -> tuple[RuntimeConfirmationOutcome, str | None]:
    if confirmer is None:
        return "unavailable", "confirmation_unavailable"
    try:
        accepted = bool(confirmer(decision_result))
    except Exception as exc:
        return "unavailable", _failure_class_from_error(exc) or "confirmation_error"
    if not accepted:
        return "refused", None
    return "confirmed", None


def _run_action(http_client: httpx.Client, request: RuntimeActionRequest) -> _ActionExecutionResult:
    if isinstance(request, ShellCommandActionRequest):
        return _run_shell_command(request)
    if isinstance(request, FileWriteActionRequest):
        return _run_file_write(request)
    if isinstance(request, HTTPRequestActionRequest):
        return _run_http_request(http_client, request)
    raise TypeError(f"Unsupported runtime action request type: {type(request)!r}.")


def _run_shell_command(request: ShellCommandActionRequest) -> _ActionExecutionResult:
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            request.command,
            cwd=request.cwd,
            check=False,
            capture_output=True,
        )
    except OSError:
        return _ActionExecutionResult(
            succeeded=False,
            metadata=None,
            failure_class="os_error",
        )
    metadata = ShellCommandExecutionMetadata(
        exit_code=completed.returncode,
        duration_ms=_elapsed_ms(start),
    )
    if completed.returncode != 0:
        return _ActionExecutionResult(
            succeeded=False,
            metadata=metadata,
            failure_class="non_zero_exit",
        )
    return _ActionExecutionResult(
        succeeded=True,
        metadata=metadata,
    )


def _run_file_write(request: FileWriteActionRequest) -> _ActionExecutionResult:
    target_path = _resolve_action_path(request.path, base=request.cwd)
    if not target_path.parent.exists():
        return _ActionExecutionResult(
            succeeded=False,
            metadata=FileWriteExecutionMetadata(path=target_path, bytes_written=0),
            failure_class="missing_parent",
        )
    encoded = request.content.encode("utf-8")
    staged_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(request.content)
            staged_path = Path(handle.name)
        staged_path.replace(target_path)
    except OSError:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)
        return _ActionExecutionResult(
            succeeded=False,
            metadata=FileWriteExecutionMetadata(path=target_path, bytes_written=0),
            failure_class="io_error",
        )
    return _ActionExecutionResult(
        succeeded=True,
        metadata=FileWriteExecutionMetadata(path=target_path, bytes_written=len(encoded)),
    )


def _run_http_request(
    http_client: httpx.Client,
    request: HTTPRequestActionRequest,
) -> _ActionExecutionResult:
    start = time.perf_counter()
    try:
        response = http_client.request(request.method, str(request.url))
    except httpx.TimeoutException:
        return _ActionExecutionResult(
            succeeded=False,
            metadata=HTTPRequestExecutionMetadata(
                status_code=None,
                duration_ms=_elapsed_ms(start),
            ),
            failure_class="timeout",
        )
    except httpx.NetworkError:
        return _ActionExecutionResult(
            succeeded=False,
            metadata=HTTPRequestExecutionMetadata(
                status_code=None,
                duration_ms=_elapsed_ms(start),
            ),
            failure_class="connection",
        )
    except httpx.HTTPError:
        return _ActionExecutionResult(
            succeeded=False,
            metadata=HTTPRequestExecutionMetadata(
                status_code=None,
                duration_ms=_elapsed_ms(start),
            ),
            failure_class="unexpected",
        )

    metadata = HTTPRequestExecutionMetadata(
        status_code=response.status_code,
        duration_ms=_elapsed_ms(start),
    )
    if response.status_code >= 400:
        return _ActionExecutionResult(
            succeeded=False,
            metadata=metadata,
            failure_class="http_status",
        )
    return _ActionExecutionResult(succeeded=True, metadata=metadata)


def _build_execution_result(
    *,
    execution_id: str,
    session_id: str,
    request: RuntimeExecutionRequest,
    decision_result: RuntimeDecisionResult,
    confirmation_outcome: RuntimeConfirmationOutcome,
    execution_outcome: RuntimeExecutionOutcome,
    result_metadata: RuntimeExecutionMetadata | None,
    failure_class: str | None,
    residual_uncertainty: str | None,
) -> RuntimeExecutionResult:
    return RuntimeExecutionResult(
        execution_id=execution_id,
        session_id=session_id,
        request=request,
        decision=decision_result.decision,
        summary=decision_result.summary,
        matched_rules=list(decision_result.matched_rules),
        citations=list(decision_result.citations),
        confirmation_outcome=confirmation_outcome,
        execution_outcome=execution_outcome,
        result_metadata=result_metadata,
        failure_class=failure_class,
        residual_uncertainty=residual_uncertainty,
    )


def _build_evidence_record(
    *,
    execution_id: str,
    session_id: str,
    event_kind: RuntimeEvidenceEventKind,
    request: RuntimeExecutionRequest,
    decision_result: RuntimeDecisionResult,
    confirmation_outcome: RuntimeConfirmationOutcome,
    execution_outcome: RuntimeExecutionOutcome | None,
    result_metadata: RuntimeExecutionMetadata | None,
    failure_class: str | None,
    residual_uncertainty: str | None,
) -> RuntimeExecutionEvidenceRecord:
    return RuntimeExecutionEvidenceRecord(
        event_id=str(uuid4()),
        execution_id=execution_id,
        session_id=session_id,
        created_at=datetime.now(tz=UTC),
        event_kind=event_kind,
        request=request,
        decision=decision_result.decision,
        summary=decision_result.summary,
        matched_rules=list(decision_result.matched_rules),
        citations=list(decision_result.citations),
        confirmation_outcome=confirmation_outcome,
        execution_outcome=execution_outcome,
        result_metadata=result_metadata,
        failure_class=failure_class,
        residual_uncertainty=residual_uncertainty,
    )


def _append_initial_event(
    evidence_store: RuntimeEvidenceStoreProtocol,
    record: RuntimeExecutionEvidenceRecord,
) -> None:
    try:
        evidence_store.append_event(record)
    except Exception as exc:
        raise RuntimeEvidencePersistenceError(
            "PolicyNIM could not persist the initial runtime decision evidence. "
            "The action was not executed.",
            failure_class="runtime_evidence_persistence",
        ) from exc


def _append_terminal_event(
    evidence_store: RuntimeEvidenceStoreProtocol,
    result: RuntimeExecutionResult,
    *,
    event_kind: RuntimeEvidenceEventKind,
    action_started: bool,
) -> None:
    record = RuntimeExecutionEvidenceRecord(
        event_id=str(uuid4()),
        execution_id=result.execution_id,
        session_id=result.session_id,
        created_at=datetime.now(tz=UTC),
        event_kind=event_kind,
        request=result.request,
        decision=result.decision,
        summary=result.summary,
        matched_rules=list(result.matched_rules),
        citations=list(result.citations),
        confirmation_outcome=result.confirmation_outcome,
        execution_outcome=result.execution_outcome,
        result_metadata=result.result_metadata,
        failure_class=result.failure_class,
        residual_uncertainty=result.residual_uncertainty,
    )
    try:
        evidence_store.append_event(record)
    except Exception as exc:
        if action_started:
            raise RuntimeEvidencePersistenceError(
                "Runtime execution completed, but PolicyNIM could not persist the terminal "
                "runtime evidence. Inspect the action outcome before retrying.",
                failure_class="runtime_evidence_persistence",
            ) from exc
        raise RuntimeEvidencePersistenceError(
            "PolicyNIM could not persist the terminal runtime evidence. "
            "The action was not executed.",
            failure_class="runtime_evidence_persistence",
        ) from exc


def _resolve_action_path(path: Path, *, base: Path) -> Path:
    if path.is_absolute():
        return path.resolve(strict=False)
    return (base / path).resolve(strict=False)


def _residual_uncertainty_for_decision(decision_result: RuntimeDecisionResult) -> str | None:
    if decision_result.decision == "allow":
        return "No explicit runtime rule matched this action."
    if decision_result.decision == "confirm":
        return "Execution required explicit confirmation before side effects."
    return None


def _failure_class_from_error(exc: BaseException) -> str | None:
    current: BaseException | None = exc
    while current is not None:
        failure_class = getattr(current, "failure_class", None)
        if isinstance(failure_class, str) and failure_class:
            return failure_class
        current = current.__cause__ or current.__context__
    return None


def _elapsed_ms(start_time: float) -> float:
    return round((time.perf_counter() - start_time) * 1000, 2)


def _close_component(component: object | None) -> None:
    close = getattr(component, "close", None)
    if callable(close):
        close()
