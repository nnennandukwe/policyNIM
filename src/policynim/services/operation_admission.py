"""Principal-aware hosted operation admission and caller recovery guidance."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Literal, TypeVar

from policynim.errors import PolicyNIMError, ProviderError
from policynim.operations import (
    current_correlation_id,
    emit_operation_event,
    safe_exception_diagnostics,
)
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.authorization import (
    AuthenticatedPrincipal,
    AuthorizationError,
    authorize_operation,
    principal_identity,
)
from policynim.settings import Settings
from policynim.storage.operation_store import (
    AdmissionRejection,
    OperationAdmission,
    OperationOutcome,
    OperationSnapshot,
    OperationStore,
)

_Result = TypeVar("_Result")


def _is_busy(error: BaseException) -> bool:
    """Classify SQLite writer contention from codes without parsing private messages."""
    code = getattr(error, "sqlite_errorcode", None)
    return isinstance(code, int) and code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}


def record_operation_storage_failure(
    error: BaseException,
    *,
    phase: Literal["initialization", "reservation", "finish", "beta_auth_initialization"],
    operation_id: str | None = None,
) -> None:
    """Record safe error codes and an opaque operation reference without payloads or paths."""
    code = getattr(error, "sqlite_errorcode", None)
    os_errno = getattr(error, "errno", None)
    emit_operation_event(
        level=logging.ERROR,
        event="operation.storage_failure",
        phase=phase,
        **safe_exception_diagnostics(error),
        sqlite_code=code if isinstance(code, int) else None,
        os_errno=os_errno if isinstance(os_errno, int) else None,
        operation_id=operation_id,
    )


def _admission_storage_error(error: BaseException) -> str:
    """Keep ordinary bounded writer contention separate from storage outages."""
    if _is_busy(error):
        return (
            "admission_busy: No operation started. Operation state is busy; retry shortly "
            "after active work drains."
        )
    return (
        "admission_unavailable: No operation started. Ask the operator to check "
        "the hosted state volume and database locks, then retry."
    )


def admit_operation(
    store: OperationStore,
    *,
    principal_id: str,
    operation: str,
    daily_limit: int,
    principal_limit: int,
    global_limit: int,
    beta_account_id: int | None = None,
    credential_id: str | None = None,
    now: float | None = None,
    before_accept: Callable[[], None] | None = None,
    correlation_id: str | None = None,
    retention_days: int = 30,
    work_units: int = 1,
    active_slot: bool = True,
    reservation_key: str | None = None,
    request_digest: str | None = None,
) -> OperationAdmission:
    """Apply service-owned admission policy to a snapshot protected by the store's transaction."""
    if min(daily_limit, principal_limit, global_limit, work_units) < 1:
        raise ValueError("Admission limits must be positive.")

    def decide(snapshot: OperationSnapshot) -> AdmissionRejection | None:
        """Recheck authority/cancellation before applying limits to locked persisted facts."""
        if before_accept is not None:
            before_accept()
        credential = snapshot.credential
        if beta_account_id is not None and (
            credential is None
            or credential.account_id != beta_account_id
            or credential.account_status != "active"
            or credential.revoked_at is not None
        ):
            return "invalid_token"
        if snapshot.reused:
            return None
        if active_slot and snapshot.active >= global_limit:
            return "server_busy"
        if active_slot and snapshot.principal_active >= principal_limit:
            return "principal_busy"
        if snapshot.daily_work_units + work_units > daily_limit:
            return "quota_exceeded"
        return None

    return store.reserve(
        principal_id=principal_id,
        operation=operation,
        decide=decide,
        credential_id=credential_id,
        now=now,
        correlation_id=correlation_id,
        retention_days=retention_days,
        work_units=work_units,
        active_slot=active_slot,
        reservation_key=reservation_key,
        request_digest=request_digest,
    )


class OperationAdmissionError(PolicyNIMError):
    """Reject work before provider execution with a stable recovery message."""


@dataclass(frozen=True)
class AcceptedWork:
    """Return the durable logical acceptance without optional rejection fields."""

    operation_id: str
    work_units: int
    outcome: str


class AcceptanceFinalizedError(OperationAdmissionError):
    """Expose an existing terminal outcome so job recovery can't repeat provider work."""

    def __init__(self, outcome: str) -> None:
        """Keep the stable outcome separate from the caller-facing recovery message."""
        self.outcome = outcome
        super().__init__(
            "acceptance_finalized: Accepted work already has a terminal outcome. "
            "Inspect the retained job result before retrying."
        )


class OperationAdmissionService:
    """Own accounting independently from HTTP authentication and provider credentials."""

    def __init__(self, settings: Settings, *, store: OperationStore | None = None) -> None:
        """Open hosted operation state and recover only provably dead local processes."""
        self.settings = settings
        self._accounting_incomplete = Event()
        self.store: OperationStore
        created_store: OperationStore | None = None
        try:
            if store is None:
                created_store = OperationStore(resolve_runtime_path(settings.beta_auth_db_path))
                self.store = created_store
            else:
                self.store = store
            self.store.recover_interrupted()
            self.store.prune_owner_leases()
        except (sqlite3.Error, OSError, ValueError) as exc:
            if created_store is not None:
                created_store.close()
            record_operation_storage_failure(exc, phase="initialization")
            raise OperationAdmissionError(_admission_storage_error(exc)) from exc

    def _require_ready(
        self,
        principal: AuthenticatedPrincipal,
        operation_name: str,
        cancelled: Callable[[], bool],
        check_ready: Callable[[], object] | None,
    ) -> None:
        """Recheck authority, accounting health and deadlines at each durable boundary."""
        authorize_operation(principal, operation_name)
        if self._accounting_incomplete.is_set():
            raise OperationAdmissionError(
                "accounting_incomplete: New work is paused after uncertain accounting. "
                "Ask the operator to inspect operation state and restart the hosted process."
            )
        if cancelled():
            raise ProviderError("Operation cancelled before admission.", failure_class="cancelled")
        if check_ready is not None:
            check_ready()

    @staticmethod
    def _check_decision(decision: OperationAdmission) -> OperationAdmission:
        """Translate storage facts into stable caller recovery errors."""
        if decision.rejection == "invalid_token":
            raise AuthorizationError(
                "invalid_token: Authenticate again before retrying.", code="invalid_token"
            )
        if decision.rejection == "acceptance_finalized":
            raise AcceptanceFinalizedError(decision.outcome)
        if decision.rejection is not None:
            messages = {
                "server_busy": "PolicyNIM is at capacity. Retry after active work finishes.",
                "principal_busy": "Your operation limit is active. Retry after your work finishes.",
                "quota_exceeded": "Work-unit allowance exhausted. Retry after 00:00 UTC.",
                "acceptance_conflict": (
                    "This submission key already reserved different work. "
                    "Use the original request or a new key."
                ),
                "acceptance_not_found": (
                    "The bound work reservation wasn't found. "
                    "Ask the operator to inspect the job state."
                ),
            }
            raise OperationAdmissionError(decision.rejection + ": " + messages[decision.rejection])
        return decision

    def _retry_storage(self, action: Callable[[], _Result]) -> _Result:
        """Bound writer contention while retaining durable acceptance across retries."""
        for attempt in range(3):
            try:
                return action()
            except (sqlite3.Error, OSError, ValueError) as exc:
                if _is_busy(exc) and attempt < 2:
                    time.sleep(0.05)
                    continue
                record_operation_storage_failure(exc, phase="reservation")
                raise OperationAdmissionError(_admission_storage_error(exc)) from exc
        raise AssertionError("Storage retry loop didn't return.")

    def accept_work(
        self,
        *,
        principal: AuthenticatedPrincipal,
        operation_name: str,
        reservation_key: str,
        request_digest: str,
        work_units: int,
    ) -> AcceptedWork:
        """Commit idempotent weighted acceptance without acquiring an execution slot."""
        if type(work_units) is not int or work_units < 1:
            raise ValueError("Work units must be a positive integer.")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (reservation_key, request_digest)
        ):
            raise ValueError("Acceptance key and request digest must be SHA256 hex values.")

        def accept() -> OperationAdmission:
            """Reserve against a write-locked daily allowance after checking current authority."""
            self._require_ready(principal, operation_name, lambda: False, None)
            return admit_operation(
                self.store,
                principal_id=principal_identity(principal.issuer, principal.subject),
                operation=operation_name,
                daily_limit=self.settings.beta_daily_request_quota
                if principal.source == "beta_key"
                else self.settings.mcp_principal_daily_operation_quota,
                principal_limit=self.settings.mcp_max_concurrent_operations_per_principal,
                global_limit=self.settings.mcp_max_concurrent_operations,
                beta_account_id=int(principal.subject) if principal.source == "beta_key" else None,
                credential_id=principal.credential_id,
                before_accept=lambda: self._require_ready(
                    principal, operation_name, lambda: False, None
                ),
                correlation_id=current_correlation_id(),
                retention_days=self.settings.mcp_operation_retention_days,
                work_units=work_units,
                active_slot=False,
                reservation_key=reservation_key,
                request_digest=request_digest,
            )

        decision = self._check_decision(self._retry_storage(accept))
        assert decision.operation_id is not None
        return AcceptedWork(decision.operation_id, decision.work_units, decision.outcome)

    def run_accepted(
        self,
        operation: Callable[[], _Result],
        *,
        operation_id: str,
        principal: AuthenticatedPrincipal,
        operation_name: str,
        cancelled: Callable[[], bool],
        check_ready: Callable[[], object] | None = None,
    ) -> _Result:
        """Activate previously accepted work once without charging its units again."""

        def activate() -> OperationAdmission:
            """Apply execution authority and active caps to one durable inactive acceptance."""
            self._require_ready(principal, operation_name, cancelled, check_ready)

            def decide(snapshot: OperationSnapshot) -> AdmissionRejection | None:
                """Use fresh credential facts while leaving the original daily charge intact."""
                self._require_ready(principal, operation_name, cancelled, check_ready)
                credential = snapshot.credential
                if principal.source == "beta_key" and (
                    credential is None
                    or credential.account_id != int(principal.subject)
                    or credential.account_status != "active"
                    or credential.revoked_at is not None
                ):
                    return "invalid_token"
                if snapshot.active >= self.settings.mcp_max_concurrent_operations:
                    return "server_busy"
                if (
                    snapshot.principal_active
                    >= self.settings.mcp_max_concurrent_operations_per_principal
                ):
                    return "principal_busy"
                return None

            return self.store.activate(
                operation_id=operation_id,
                principal_id=principal_identity(principal.issuer, principal.subject),
                operation=operation_name,
                decide=decide,
                credential_id=principal.credential_id,
                correlation_id=current_correlation_id(),
            )

        decision = self._check_decision(self._retry_storage(activate))
        assert decision.operation_id is not None
        return self._execute(operation, decision.operation_id, cancelled)

    def finish_accepted(
        self,
        *,
        operation_id: str,
        principal_id: str,
        operation_name: str,
        outcome: OperationOutcome,
    ) -> str:
        """Reconcile terminal jobs by bound identity even after their credential expires."""
        try:
            result = self._retry_storage(
                lambda: self.store.finish_accepted(
                    operation_id=operation_id,
                    principal_id=principal_id,
                    operation=operation_name,
                    outcome=outcome,
                )
            )
        except OperationAdmissionError as exc:
            self._accounting_incomplete.set()
            raise OperationAdmissionError(
                "accounting_incomplete: Accepted work still needs terminal reconciliation. "
                "Ask the operator to inspect operation state before resuming the worker."
            ) from exc
        if result is None:
            raise OperationAdmissionError(
                "acceptance_not_found: The bound work reservation wasn't found."
            )
        if result == "accepted":
            raise OperationAdmissionError(
                "principal_busy: Accepted work is still active; wait for its worker to drain."
            )
        return result

    def _execute(
        self, operation: Callable[[], _Result], operation_id: str, cancelled: Callable[[], bool]
    ) -> _Result:
        """Keep execution cleanup and uncertain terminal accounting identical for both paths."""
        outcome: OperationOutcome = "failed"
        try:
            result = operation()
            outcome = "completed"
            return result
        finally:
            if cancelled():
                outcome = "cancelled"
            try:
                self.store.finish(operation_id, outcome)
            except (sqlite3.Error, OSError) as exc:
                self._accounting_incomplete.set()
                record_operation_storage_failure(exc, phase="finish", operation_id=operation_id)
                raise OperationAdmissionError(
                    "accounting_incomplete: Work was accepted and may have completed. "
                    "Don't retry blindly; ask the operator to inspect operation state."
                ) from exc

    def run(
        self,
        operation: Callable[[], _Result],
        *,
        principal: AuthenticatedPrincipal,
        operation_name: str,
        cancelled: Callable[[], bool],
        check_ready: Callable[[], object] | None = None,
    ) -> _Result:
        """Charge admitted work once and retain its terminal outcome after cleanup."""

        def require_ready() -> None:
            """Reject authority withdrawn or cancellation/deadline reached before reservation."""
            self._require_ready(principal, operation_name, cancelled, check_ready)

        attempt = 0
        while True:
            require_ready()
            try:
                decision = admit_operation(
                    self.store,
                    principal_id=principal_identity(principal.issuer, principal.subject),
                    operation=operation_name,
                    daily_limit=(
                        self.settings.beta_daily_request_quota
                        if principal.source == "beta_key"
                        else self.settings.mcp_principal_daily_operation_quota
                    ),
                    principal_limit=self.settings.mcp_max_concurrent_operations_per_principal,
                    global_limit=self.settings.mcp_max_concurrent_operations,
                    beta_account_id=int(principal.subject)
                    if principal.source == "beta_key"
                    else None,
                    credential_id=principal.credential_id,
                    correlation_id=current_correlation_id(),
                    retention_days=self.settings.mcp_operation_retention_days,
                    before_accept=require_ready,
                )
                break
            except (sqlite3.Error, OSError, ValueError) as exc:
                if _is_busy(exc) and attempt < 2:
                    attempt += 1
                    time.sleep(0.05)
                    continue
                record_operation_storage_failure(exc, phase="reservation")
                raise OperationAdmissionError(_admission_storage_error(exc)) from exc
        self.store.prune_owner_leases()
        self._check_decision(decision)
        assert decision.operation_id is not None
        return self._execute(operation, decision.operation_id, cancelled)
