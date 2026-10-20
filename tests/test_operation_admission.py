"""Real SQLite and worker-boundary regression tests for accepted hosted work."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest

from policynim.interfaces.mcp import _ToolExecutor
from policynim.services.authorization import (
    AuthenticatedPrincipal,
    AuthorizationError,
    legacy_principal,
)
from policynim.services.beta_auth import BetaAuthService
from policynim.services.operation_admission import (
    OperationAdmissionError,
    OperationAdmissionService,
    principal_identity,
)
from policynim.settings import Settings
from policynim.storage.auth_store import AuthStore
from policynim.storage.operation_store import OperationStore


def _principal(subject: str = "alice") -> AuthenticatedPrincipal:
    """Use issuer claims without any access token or provider credential."""
    return AuthenticatedPrincipal(
        issuer="https://issuer.example/realm",
        subject=subject,
        client_id="native-client",
        scopes=frozenset({"policies:read", "policies:evaluate"}),
        source="oauth",
    )


def _settings(tmp_path: Path, **changes: object) -> Settings:
    """Keep all hosted state within one test directory."""
    return Settings().model_copy(
        update={
            "beta_auth_db_path": tmp_path / "auth.sqlite3",
            **changes,
        }
    )


def test_concurrent_daily_boundary_is_atomic_across_connections(tmp_path: Path) -> None:
    """Exactly seven out of 40 concurrent calls can commit accepted work."""
    path = tmp_path / "operations.sqlite3"
    stores = [OperationStore(path) for _ in range(4)]

    def attempt(index: int) -> bool:
        """Use independent connections and finish admitted work without refunding."""
        store = stores[index % len(stores)]
        result = store.admit(
            principal_id="alice",
            operation="policy_search",
            daily_limit=7,
            principal_limit=40,
            global_limit=40,
        )
        if result.operation_id is not None:
            store.finish(result.operation_id, "completed")
            return True
        assert result.rejection == "quota_exceeded"
        return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(attempt, range(40))) == 7
    usage = OperationStore(path).usage("alice")
    assert (usage.accepted, usage.active, usage.completed, usage.provider_usage) == (7, 0, 7, None)


def test_principal_and_global_active_limits_are_independent(tmp_path: Path) -> None:
    """One caller can't monopolize capacity, while another can use spare capacity."""
    store = OperationStore(tmp_path / "operations.sqlite3")

    def admit(subject: str):
        """Apply a shared two-slot cap and a one-slot principal limit."""
        return store.admit(
            principal_id=subject,
            operation="policy_search",
            daily_limit=10,
            principal_limit=1,
            global_limit=2,
        )

    first = admit("alice")
    assert first.operation_id
    assert admit("alice").rejection == "principal_busy"
    second = admit("bob")
    assert second.operation_id
    assert admit("carol").rejection == "server_busy"
    assert store.usage("carol").accepted == 0
    store.finish(first.operation_id, "failed")
    store.finish(first.operation_id, "completed")
    assert store.usage("alice").failed == 1
    assert admit("carol").operation_id


def test_utc_day_rollover_keeps_active_slots(tmp_path: Path) -> None:
    """Midnight renews daily allowance without losing running work."""
    store = OperationStore(tmp_path / "operations.sqlite3")
    before = datetime(2026, 10, 20, 23, 59, tzinfo=UTC).timestamp()
    after = datetime(2026, 10, 21, 0, 1, tzinfo=UTC).timestamp()
    first = store.admit(
        principal_id="alice",
        operation="policy_search",
        daily_limit=1,
        principal_limit=1,
        global_limit=2,
        now=before,
    )
    assert first.operation_id
    assert (
        store.admit(
            principal_id="alice",
            operation="policy_search",
            daily_limit=1,
            principal_limit=1,
            global_limit=2,
            now=after,
        ).rejection
        == "principal_busy"
    )
    store.finish(first.operation_id, "completed")
    assert store.admit(
        principal_id="alice",
        operation="policy_search",
        daily_limit=1,
        principal_limit=1,
        global_limit=2,
        now=after,
    ).operation_id


@pytest.mark.parametrize("mutation", ["revoke", "rotate", "suspend"])
def test_beta_authority_rechecked_at_admission(tmp_path: Path, mutation: str) -> None:
    """Revocation after successful HTTP authentication blocks provider execution."""
    settings = _settings(tmp_path)
    auth = BetaAuthService(store=AuthStore(path=settings.beta_auth_db_path), settings=settings)
    account = auth._store.upsert_account_from_github(
        github_user_id=1, github_login="alice", email=None, now=datetime.now(UTC)
    )
    issued = auth.issue_api_key(account_id=account.account_id)
    assert auth.authenticate_api_key(token=issued.api_key).status == "authorized"
    principal = legacy_principal(token=issued.api_key, account_id=account.account_id)
    service = OperationAdmissionService(settings)
    if mutation == "revoke":
        auth.revoke_api_key(github_login="alice")
    elif mutation == "rotate":
        auth.issue_api_key(account_id=account.account_id)
    else:
        auth.suspend_account(github_login="alice")
    started = Event()
    with pytest.raises(AuthorizationError, match="Authenticate again"):
        service.run(
            started.set,
            principal=principal,
            operation_name="policy_search",
            cancelled=lambda: False,
        )
    assert not started.is_set()
    assert auth.get_portal_usage(account.account_id).request_count == 0


def test_beta_portal_and_rotation_share_accepted_operation_usage(tmp_path: Path) -> None:
    """Authentication and legacy counters don't inflate the accepted-work display."""
    settings = _settings(tmp_path, beta_daily_request_quota=1)
    store = AuthStore(path=settings.beta_auth_db_path)
    auth = BetaAuthService(store=store, settings=settings)
    now = datetime.now(UTC)
    account = store.upsert_account_from_github(
        github_user_id=1, github_login="alice", email=None, now=now
    )
    first = auth.issue_api_key(account_id=account.account_id)
    for _ in range(3):
        assert auth.authenticate_api_key(token=first.api_key).status == "authorized"
    store.consume_daily_quota(
        account_id=account.account_id, usage_date=now.date(), quota=500, now=now
    )
    assert auth.get_portal_usage(account.account_id).request_count == 0
    service = OperationAdmissionService(settings)
    service.run(
        lambda: "result",
        principal=legacy_principal(token=first.api_key, account_id=account.account_id),
        operation_name="policy_search",
        cancelled=lambda: False,
    )
    assert auth.get_portal_usage(account.account_id).request_count == 1
    replacement = auth.issue_api_key(account_id=account.account_id)
    assert auth.authenticate_api_key(token=replacement.api_key).status == "authorized"
    with pytest.raises(OperationAdmissionError, match="00:00 UTC"):
        service.run(
            lambda: None,
            principal=legacy_principal(token=replacement.api_key, account_id=account.account_id),
            operation_name="policy_search",
            cancelled=lambda: False,
        )


def test_failed_work_keeps_charge_and_unknown_provider_usage(tmp_path: Path) -> None:
    """A provider failure has its own outcome and doesn't refund accepted work."""
    service = OperationAdmissionService(_settings(tmp_path, mcp_principal_daily_operation_quota=1))

    def fail() -> None:
        """Fail inside accepted provider work."""
        raise RuntimeError("provider double failed")

    with pytest.raises(RuntimeError):
        service.run(
            fail, principal=_principal(), operation_name="policy_search", cancelled=lambda: False
        )
    usage = service.store.usage(principal_identity(_principal().issuer, "alice"))
    assert (usage.accepted, usage.failed, usage.active, usage.provider_usage) == (1, 1, 0, None)
    with pytest.raises(OperationAdmissionError, match="quota_exceeded"):
        service.run(
            lambda: None,
            principal=_principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )


def test_wrong_scope_and_expired_principal_never_charge(tmp_path: Path) -> None:
    """Recheck authorization before any accepted row or provider work."""
    service = OperationAdmissionService(_settings(tmp_path))
    for scopes, expiry in [(frozenset(), None), (frozenset({"policies:read"}), 1)]:
        principal = AuthenticatedPrincipal(
            issuer="issuer",
            subject="alice",
            client_id="client",
            scopes=scopes,
            source="oauth",
            expires_at=expiry,
        )
        with pytest.raises(AuthorizationError):
            service.run(
                lambda: pytest.fail("Provider started"),
                principal=principal,
                operation_name="policy_search",
                cancelled=lambda: False,
            )
    assert service.store.usage(principal_identity("issuer", "alice")).accepted == 0


def test_locked_database_rejects_before_provider_work(tmp_path: Path) -> None:
    """A busy writer yields bounded, sanitized recovery guidance and no charge."""
    service = OperationAdmissionService(_settings(tmp_path))
    with sqlite3.connect(service.store.path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(OperationAdmissionError, match="database locks"):
            service.run(
                lambda: pytest.fail("Provider started"),
                principal=_principal(),
                operation_name="policy_search",
                cancelled=lambda: False,
            )
    assert service.store.usage(principal_identity(_principal().issuer, "alice")).accepted == 0


def test_insert_failure_rolls_back_charge(tmp_path: Path) -> None:
    """A failed insertion can't leave a partial admission or start a provider."""
    service = OperationAdmissionService(_settings(tmp_path))
    with sqlite3.connect(service.store.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_insert BEFORE INSERT ON operation_usage "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(OperationAdmissionError, match="No operation started"):
        service.run(
            lambda: pytest.fail("Provider started"),
            principal=_principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )
    assert service.store.usage(principal_identity(_principal().issuer, "alice")).accepted == 0


def test_dead_process_recovery_retains_accepted_work(tmp_path: Path) -> None:
    """A real process exit permits recovery without refunding the abandoned operation."""
    path = tmp_path / "operations.sqlite3"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; "
            "from policynim.storage.operation_store import OperationStore; "
            "import sys; s=OperationStore(Path(sys.argv[1])); "
            "s.admit(principal_id='alice', operation='policy_search', "
            "daily_limit=10, principal_limit=1, global_limit=1)",
            str(path),
        ],
        check=True,
    )
    store = OperationStore(path)
    assert store.recover_interrupted() == 1
    assert store.recover_interrupted() == 0
    usage = store.usage("alice")
    assert (usage.accepted, usage.active, usage.interrupted) == (1, 0, 1)
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT process_id FROM operation_usage").fetchone()[0]
            != os.getpid()
        )


def test_cancellation_drains_worker_before_releasing_principal_capacity(tmp_path: Path) -> None:
    """Cancellation retains the active slot until the synchronous worker exits."""
    settings = _settings(tmp_path, mcp_max_concurrent_operations_per_principal=1)
    started = Event()
    release = Event()

    def blocked() -> str:
        """Keep the provider double active until the test releases cleanup."""
        started.set()
        assert release.wait(5)
        return "result"

    async def scenario() -> None:
        """Cancel one request and inspect persisted admission while its worker drains."""
        executor = _ToolExecutor(settings=settings)
        pending = asyncio.create_task(
            executor.run(blocked, principal=_principal(), operation_name="policy_search")
        )
        try:
            assert await asyncio.to_thread(started.wait, 5)
            pending.cancel()
            await asyncio.sleep(0)
            with pytest.raises(OperationAdmissionError, match="principal_busy"):
                await executor.run(
                    lambda: "second", principal=_principal(), operation_name="policy_search"
                )
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert executor._operation_usage is not None
            usage = executor._operation_usage.store.usage(
                principal_identity(_principal().issuer, "alice")
            )
            assert (usage.accepted, usage.active, usage.cancelled) == (1, 0, 1)
            assert (
                await executor.run(
                    lambda: "third", principal=_principal(), operation_name="policy_search"
                )
                == "third"
            )
        finally:
            release.set()
            await executor.close()

    asyncio.run(scenario())


def test_outcome_write_failure_keeps_charge_and_blocks_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed provider call can't be presented as uncharged after storage failure."""
    service = OperationAdmissionService(
        _settings(tmp_path, mcp_max_concurrent_operations_per_principal=1)
    )

    def fail_finish(operation_id: str, outcome: str) -> None:
        """Fail only terminal persistence after the provider double returns."""
        raise sqlite3.OperationalError("injected write failure")

    monkeypatch.setattr(service.store, "finish", fail_finish)
    with pytest.raises(OperationAdmissionError, match="Don't retry blindly"):
        service.run(
            lambda: "provider completed",
            principal=_principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )
    usage = service.store.usage(principal_identity(_principal().issuer, "alice"))
    assert (usage.accepted, usage.active, usage.completed) == (1, 1, 0)
    with pytest.raises(OperationAdmissionError, match="principal_busy"):
        service.run(
            lambda: pytest.fail("Second provider started"),
            principal=_principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )


def test_late_provider_result_records_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker's final deadline guard runs before its accepted outcome is persisted."""
    from policynim.errors import ProviderError
    from policynim.operations import current_budget

    def expire() -> str:
        """Return only after the budget has expired without waiting on a real provider."""
        current_budget().deadline = 0
        return "late response"

    async def scenario() -> None:
        """Verify the executor records the rejected late response as failed work."""
        executor = _ToolExecutor(settings=_settings(tmp_path))
        try:
            with pytest.raises(ProviderError, match="deadline exhausted"):
                await executor.run(expire, principal=_principal(), operation_name="policy_search")
            assert executor._operation_usage is not None
            usage = executor._operation_usage.store.usage(
                principal_identity(_principal().issuer, "alice")
            )
            assert (usage.accepted, usage.active, usage.failed, usage.completed) == (1, 0, 1, 0)
        finally:
            await executor.close()

    asyncio.run(scenario())
