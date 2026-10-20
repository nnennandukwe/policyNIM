"""Noncharging quota preflight remains advisory and recognizes durable orphan acceptances."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import pytest

from policynim.errors import ProviderError
from policynim.services.authorization import (
    AuthenticatedPrincipal,
    AuthorizationError,
    legacy_principal,
)
from policynim.services.beta_auth import BetaAuthService
from policynim.services.operation_admission import (
    OperationAdmissionError,
    OperationAdmissionService,
)
from policynim.settings import Settings
from policynim.storage.auth_store import AuthStore


def principal() -> AuthenticatedPrincipal:
    """Use authenticated claims without external credentials."""
    return AuthenticatedPrincipal(
        issuer="https://issuer.example",
        subject="alice",
        client_id="test",
        scopes=frozenset({"policies:read"}),
        source="oauth",
    )


@pytest.fixture
def admission(tmp_path: Path):
    """Own a real one-unit quota database for the complete preflight/acceptance flow."""
    service = OperationAdmissionService(
        Settings().model_copy(
            update={
                "beta_auth_db_path": tmp_path / "quota.sqlite3",
                "mcp_principal_daily_operation_quota": 1,
            }
        )
    )
    try:
        yield service
    finally:
        service.store.close()


class WorkArguments(TypedDict):
    """Keep public acceptance keyword types checked throughout these runtime tests."""

    principal: AuthenticatedPrincipal
    operation_name: str
    reservation_key: str
    request_digest: str
    work_units: int


def arguments(*, key: str = "a", digest: str = "b", units: int = 1) -> WorkArguments:
    """Use fixed opaque identities at the public acceptance boundary."""
    return WorkArguments(
        principal=principal(),
        operation_name="policy_search",
        reservation_key=key * 64,
        request_digest=digest * 64,
        work_units=units,
    )


def test_preflight_doesnt_charge_or_guarantee_future_acceptance(
    admission: OperationAdmissionService,
) -> None:
    """Repeated advisory reads have no side effects; final atomic admission can still reject."""
    with sqlite3.connect(admission.store.path) as database:
        database.execute(
            "CREATE TRIGGER reject_insert BEFORE INSERT ON operation_usage "
            "BEGIN SELECT RAISE(ABORT, 'unexpected write'); END"
        )
    for _ in range(3):
        assert admission.check_work_allowance(**arguments()) is None
    with sqlite3.connect(admission.store.path) as database:
        assert database.execute("SELECT COUNT(*) FROM operation_usage").fetchone()[0] == 0
        database.execute("DROP TRIGGER reject_insert")
    admission.accept_work(**arguments(key="c"))
    with pytest.raises(OperationAdmissionError, match="quota_exceeded"):
        admission.accept_work(**arguments())
    with pytest.raises(OperationAdmissionError, match="quota_exceeded"):
        admission.check_work_allowance(**arguments())


def test_matching_orphan_acceptance_passes_after_allowance_is_spent(
    admission: OperationAdmissionService,
) -> None:
    """Retry after an uncertain job publication reuses the charge without another unit."""
    accepted = admission.accept_work(**arguments())
    assert admission.check_work_allowance(**arguments()) == accepted
    assert admission.accept_work(**arguments()) == accepted
    with sqlite3.connect(admission.store.path) as database:
        assert database.execute(
            "SELECT COUNT(*),SUM(work_units) FROM operation_usage"
        ).fetchone() == (1, 1)


@pytest.mark.parametrize(("digest", "units"), [("c", 1), ("b", 2)])
def test_mismatched_orphan_identity_is_a_conflict_before_quota_check(
    admission: OperationAdmissionService,
    digest: str,
    units: int,
) -> None:
    """A changed digest or weight can't reuse a fully charged acceptance identity."""
    admission.accept_work(**arguments())
    with pytest.raises(OperationAdmissionError, match="acceptance_conflict"):
        admission.check_work_allowance(**arguments(digest=digest, units=units))


@pytest.mark.parametrize("expired", [False, True])
def test_preflight_rechecks_scope_and_expiry(
    admission: OperationAdmissionService,
    expired: bool,
) -> None:
    """An existing keyed acceptance doesn't authorize a caller whose claims are invalid."""
    admission.accept_work(**arguments())
    invalid = (
        replace(principal(), expires_at=1) if expired else replace(principal(), scopes=frozenset())
    )
    options = arguments()
    options["principal"] = invalid
    with pytest.raises(AuthorizationError):
        admission.check_work_allowance(**options)


def test_revoked_beta_key_cant_use_an_existing_acceptance(tmp_path: Path) -> None:
    """Current persisted credential status is required even when the key matches reserved work."""
    settings = Settings().model_copy(update={"beta_auth_db_path": tmp_path / "auth.sqlite3"})
    auth_store = AuthStore(path=settings.beta_auth_db_path)
    auth = BetaAuthService(store=auth_store, settings=settings)
    account = auth_store.upsert_account_from_github(
        github_user_id=1,
        github_login="alice",
        email=None,
        now=datetime.now(UTC),
    )
    first = auth.issue_api_key(account_id=account.account_id)
    caller = legacy_principal(token=first.api_key, account_id=account.account_id)
    admission = OperationAdmissionService(settings)
    try:
        options = arguments()
        options["principal"] = caller
        admission.accept_work(**options)
        auth.issue_api_key(account_id=account.account_id)
        with pytest.raises(AuthorizationError, match="invalid_token"):
            admission.check_work_allowance(**options)
    finally:
        admission.store.close()


def test_busy_read_fails_closed_without_creating_an_acceptance(
    admission: OperationAdmissionService,
) -> None:
    """Actual exclusive SQLite contention rejects advisory work without claiming allowance."""
    with sqlite3.connect(admission.store.path) as database:
        database.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(OperationAdmissionError, match="admission_busy"):
                admission.check_work_allowance(**arguments())
        finally:
            database.rollback()
        assert database.execute("SELECT COUNT(*) FROM operation_usage").fetchone()[0] == 0


def test_deadline_is_rechecked_after_the_snapshot_without_a_charge(
    admission: OperationAdmissionService,
) -> None:
    """Readiness lost during an advisory read prevents expensive preparation afterward."""
    checks = 0

    def check_ready() -> None:
        """Expire only after the first authority/readiness check and actual database read."""
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ProviderError("Fixture deadline exhausted.", failure_class="deadline_exhausted")

    with pytest.raises(ProviderError) as caught:
        admission.check_work_allowance(**arguments(), check_ready=check_ready)
    assert caught.value.failure_class == "deadline_exhausted"
    with sqlite3.connect(admission.store.path) as database:
        assert database.execute("SELECT COUNT(*) FROM operation_usage").fetchone()[0] == 0
