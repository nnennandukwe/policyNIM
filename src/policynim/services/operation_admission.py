"""Principal-aware hosted operation admission and caller recovery guidance."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import TypeVar

from policynim.errors import PolicyNIMError
from policynim.operations import current_correlation_id
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.authorization import (
    AuthenticatedPrincipal,
    AuthorizationError,
    authorize_operation,
)
from policynim.settings import Settings
from policynim.storage.operation_store import OperationOutcome, OperationStore

_Result = TypeVar("_Result")


def principal_identity(issuer: str, subject: str) -> str:
    """Keep one user's allowance stable across clients and key rotation."""
    return hashlib.sha256(json.dumps([issuer, subject]).encode()).hexdigest()


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
    ) -> _Result:
        """Charge admitted work once and retain its terminal outcome after cleanup."""
        authorize_operation(principal, operation_name)
        try:
            decision = self.store.admit(
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
