"""Principal-aware hosted operation admission and caller recovery guidance."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import TypeVar

from policynim.errors import PolicyNIMError, ProviderError
from policynim.operations import current_correlation_id
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.authorization import (
    AuthenticatedPrincipal,
    AuthorizationError,
    authorize_operation,
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


def principal_identity(issuer: str, subject: str) -> str:
    """Keep one user's allowance stable across clients and key rotation."""
    return hashlib.sha256(json.dumps([issuer, subject]).encode()).hexdigest()


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
) -> OperationAdmission:
    """Apply service-owned admission policy to a snapshot protected by the store's transaction."""
    if min(daily_limit, principal_limit, global_limit) < 1:
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
        if snapshot.active >= global_limit:
            return "server_busy"
        if snapshot.principal_active >= principal_limit:
            return "principal_busy"
        if snapshot.daily_accepted >= daily_limit:
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
    )


class OperationAdmissionError(PolicyNIMError):
    """Reject work before provider execution with a stable recovery message."""


class OperationAdmissionService:
    """Own accounting independently from HTTP authentication and provider credentials."""

    def __init__(self, settings: Settings, *, store: OperationStore | None = None) -> None:
        """Open hosted operation state and recover only provably dead local processes."""
        self.settings = settings
        self.store: OperationStore
        created_store: OperationStore | None = None
        try:
            if store is None:
                created_store = OperationStore(resolve_runtime_path(settings.beta_auth_db_path))
                self.store = created_store
            else:
                self.store = store
            self.store.recover_interrupted()
        except (sqlite3.Error, OSError, ValueError) as exc:
            if created_store is not None:
                created_store.close()
            raise OperationAdmissionError(
                "admission_unavailable: No operation started. Ask the operator to check "
                "the hosted state volume and database locks, then retry."
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
            authorize_operation(principal, operation_name)
            if cancelled():
                raise ProviderError(
                    "Operation cancelled before admission.", failure_class="cancelled"
                )
            if check_ready is not None:
                check_ready()

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
                beta_account_id=int(principal.subject) if principal.source == "beta_key" else None,
                credential_id=principal.credential_id,
                correlation_id=current_correlation_id(),
                retention_days=self.settings.mcp_operation_retention_days,
                before_accept=require_ready,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise OperationAdmissionError(
                "admission_unavailable: No operation started. Ask the operator to check "
                "the hosted state volume and database locks, then retry."
            ) from exc
        if decision.rejection == "invalid_token":
            raise AuthorizationError(
                "invalid_token: Authenticate again before retrying.", code="invalid_token"
            )
        if decision.rejection is not None:
            messages = {
                "server_busy": (
                    "server_busy: PolicyNIM is at capacity. Retry after active work finishes."
                ),
                "principal_busy": (
                    "principal_busy: Your operation limit is active. "
                    "Retry after your work finishes."
                ),
                "quota_exceeded": (
                    "quota_exceeded: Accepted-operation allowance exhausted. Retry after 00:00 UTC."
                ),
            }
            raise OperationAdmissionError(messages[decision.rejection])
        assert decision.operation_id is not None
        outcome: OperationOutcome = "failed"
        try:
            result = operation()
            outcome = "completed"
            return result
        finally:
            if cancelled():
                outcome = "cancelled"
            try:
                self.store.finish(decision.operation_id, outcome)
            except (sqlite3.Error, OSError) as exc:
                raise OperationAdmissionError(
                    "accounting_incomplete: Work was accepted and may have completed. "
                    "Don't retry blindly; ask the operator to inspect operation state."
                ) from exc
