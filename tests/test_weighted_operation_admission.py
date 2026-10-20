"""Weighted work acceptance through the public hosted admission service."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from policynim.services.authorization import AuthenticatedPrincipal, principal_identity
from policynim.services.operation_admission import (
    OperationAdmissionError,
    OperationAdmissionService,
)
from policynim.settings import Settings


def principal(subject: str = "alice") -> AuthenticatedPrincipal:
    """Create authenticated claims without credentials or provider access."""
    return AuthenticatedPrincipal(
        issuer="https://issuer.example",
        subject=subject,
        client_id="test",
        scopes=frozenset({"policies:read", "policies:evaluate"}),
        source="oauth",
    )


def service(tmp_path: Path, *, quota: int = 5) -> OperationAdmissionService:
    """Use an actual persistent quota database with a small allowance."""
    return OperationAdmissionService(
        Settings().model_copy(
            update={
                "beta_auth_db_path": tmp_path / "auth.sqlite3",
                "mcp_principal_daily_operation_quota": quota,
                "mcp_max_concurrent_operations": 1,
                "mcp_max_concurrent_operations_per_principal": 1,
            }
        )
    )


def accept(
    admission: OperationAdmissionService,
    key: str = "job",
    *,
    units: int = 3,
    subject: str = "alice",
    digest: str = "request",
):
    """Reserve validated units with opaque stable replay identities."""
    return admission.accept_work(
        principal=principal(subject),
        operation_name="policy_search",
        reservation_key=hashlib.sha256(key.encode()).hexdigest(),
        request_digest=hashlib.sha256(digest.encode()).hexdigest(),
        work_units=units,
    )


def test_weighted_acceptance_is_durable_idempotent_and_has_no_active_slot(tmp_path: Path) -> None:
    """A crash before job insertion reuses the charge and queued work leaves capacity free."""
    first = service(tmp_path)
    accepted = accept(first)
    first.store.close()
    resumed = service(tmp_path)
    replay = accept(resumed)
    assert replay.operation_id == accepted.operation_id and replay.work_units == 3
    usage = resumed.store.usage(principal_identity(principal().issuer, "alice"))
    assert usage.accepted == 1 and usage.work_units == 3 and usage.active == 0
    assert (
        resumed.run(
            lambda: "interactive",
            principal=principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )
        == "interactive"
    )
    with pytest.raises(OperationAdmissionError, match="quota_exceeded"):
        accept(resumed, "too-expensive", units=2)
    assert resumed.store.usage(principal_identity(principal().issuer, "alice")).work_units == 4
    resumed.store.close()


def test_activation_never_recharges_and_terminal_work_cant_reexecute(tmp_path: Path) -> None:
    """One job holds one slot and its retained terminal record blocks provider replay."""
    admission = service(tmp_path)
    accepted = accept(admission)
    executed = []
    assert (
        admission.run_accepted(
            lambda: executed.append(True),
            operation_id=accepted.operation_id,
            principal=principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )
        is None
    )
    with pytest.raises(OperationAdmissionError, match="acceptance_finalized"):
        admission.run_accepted(
            lambda: executed.append(False),
            operation_id=accepted.operation_id,
            principal=principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )
    assert executed == [True]
    usage = admission.store.usage(principal_identity(principal().issuer, "alice"))
    assert (usage.accepted, usage.work_units, usage.completed, usage.active) == (1, 3, 1, 0)
    admission.store.close()


def test_queued_cancel_preserves_units_and_terminal_outcome(tmp_path: Path) -> None:
    """Internal cleanup needs only bound identity and doesn't overwrite an existing outcome."""
    admission = service(tmp_path)
    accepted = accept(admission)
    owner = principal_identity(principal().issuer, "alice")
    assert (
        admission.finish_accepted(
            operation_id=accepted.operation_id,
            principal_id=owner,
            operation_name="policy_search",
            outcome="cancelled",
        )
        == "cancelled"
    )
    assert (
        admission.finish_accepted(
            operation_id=accepted.operation_id,
            principal_id=owner,
            operation_name="policy_search",
            outcome="failed",
        )
        == "cancelled"
    )
    usage = admission.store.usage(owner)
    assert (usage.accepted, usage.work_units, usage.cancelled, usage.active) == (1, 3, 1, 0)
    admission.store.close()


def test_acceptance_mismatch_and_foreign_identity_cant_change_reserved_work(tmp_path: Path) -> None:
    """Orphan acceptance replay rejects changed content and execution rejects another owner."""
    admission = service(tmp_path)
    accepted = accept(admission)
    with pytest.raises(OperationAdmissionError, match="acceptance_conflict"):
        accept(admission, digest="changed")
    with pytest.raises(OperationAdmissionError, match="acceptance_conflict"):
        accept(admission, units=1)
    with pytest.raises(OperationAdmissionError, match="acceptance_not_found"):
        admission.run_accepted(
            lambda: pytest.fail("foreign work executed"),
            operation_id=accepted.operation_id,
            principal=principal("bob"),
            operation_name="policy_search",
            cancelled=lambda: False,
        )
    admission.store.close()


def test_weighted_schema_migration_keeps_historical_charge_one(tmp_path: Path) -> None:
    """The former ledger schema migrates without rewriting logical outcomes."""
    path = tmp_path / "auth.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE operation_usage (operation_id TEXT PRIMARY KEY, principal_id TEXT "
            "NOT NULL, "
            "operation TEXT NOT NULL, usage_date TEXT NOT NULL, outcome TEXT NOT NULL, "
            "process_id INTEGER NOT NULL, "
            "started_at REAL NOT NULL, finished_at REAL, owner_id TEXT NOT NULL, "
            "correlation_id TEXT)"
        )
        connection.execute(
            "INSERT INTO operation_usage VALUES ('old','owner','policy_search','2026-10-20',"
            "'completed',1,1,2,'old-owner',NULL)"
        )
    admission = service(tmp_path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT work_units, active_slot, reservation_key FROM operation_usage"
        ).fetchone() == (1, 1, None)
    admission.store.close()


def test_concurrent_weighted_acceptance_never_overspends(tmp_path: Path) -> None:
    """Three-unit concurrent jobs share a seven-unit allowance across real connections."""
    from concurrent.futures import ThreadPoolExecutor

    services = [service(tmp_path, quota=7) for _ in range(4)]

    def submit(index: int) -> bool:
        """Return accepted or quota rejection through the real bounded storage retries."""
        try:
            accept(services[index % 4], str(index), units=3)
            return True
        except OperationAdmissionError as error:
            assert str(error).startswith("quota_exceeded:")
            return False

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            assert sum(executor.map(submit, range(12))) == 2
        usage = services[0].store.usage(principal_identity(principal().issuer, "alice"))
        assert (usage.accepted, usage.work_units, usage.inactive_accepted, usage.active) == (
            2,
            6,
            2,
            0,
        )
    finally:
        for admission in services:
            admission.store.close()


def test_active_reservation_cant_be_reactivated_or_finalized_by_queue_cleanup(
    tmp_path: Path,
) -> None:
    """Independent service handles can't release or duplicate a live job's execution slot."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    first = service(tmp_path)
    other = service(tmp_path)
    accepted = accept(first)
    started, release = Event(), Event()

    def execute() -> None:
        """Keep the provider boundary active until competing lifecycle calls finish."""
        started.set()
        assert release.wait(5)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            task = executor.submit(
                first.run_accepted,
                execute,
                operation_id=accepted.operation_id,
                principal=principal(),
                operation_name="policy_search",
                cancelled=lambda: False,
            )
            try:
                assert started.wait(3)
                with pytest.raises(OperationAdmissionError, match="principal_busy"):
                    other.run_accepted(
                        lambda: pytest.fail("replayed"),
                        operation_id=accepted.operation_id,
                        principal=principal(),
                        operation_name="policy_search",
                        cancelled=lambda: False,
                    )
                with pytest.raises(OperationAdmissionError, match="principal_busy"):
                    other.finish_accepted(
                        operation_id=accepted.operation_id,
                        principal_id=principal_identity(principal().issuer, "alice"),
                        operation_name="policy_search",
                        outcome="cancelled",
                    )
                queued = accept(other, "queued", units=1)
                with pytest.raises(OperationAdmissionError, match="server_busy"):
                    other.run_accepted(
                        lambda: pytest.fail("capacity bypass"),
                        operation_id=queued.operation_id,
                        principal=principal(),
                        operation_name="policy_search",
                        cancelled=lambda: False,
                    )
            finally:
                release.set()
            task.result(timeout=3)
        usage = first.store.usage(principal_identity(principal().issuer, "alice"))
        assert (
            usage.accepted,
            usage.work_units,
            usage.completed,
            usage.inactive_accepted,
            usage.active,
        ) == (
            2,
            4,
            1,
            1,
            0,
        )
    finally:
        first.store.close()
        other.store.close()


def test_keyed_terminal_acceptance_survives_retention_without_recharging(tmp_path: Path) -> None:
    """Permanent idempotency tombstones match durable jobs even after payload retention."""
    admission = service(tmp_path)
    accepted = accept(admission)
    owner = principal_identity(principal().issuer, "alice")
    admission.finish_accepted(
        operation_id=accepted.operation_id,
        principal_id=owner,
        operation_name="policy_search",
        outcome="cancelled",
    )
    with sqlite3.connect(admission.store.path) as connection:
        connection.execute("UPDATE operation_usage SET finished_at=1, usage_date='1970-01-01'")
    assert admission.store.prune(retention_days=1) == 0
    replay = accept(admission)
    assert replay.operation_id == accepted.operation_id and replay.outcome == "cancelled"
    admission.store.close()


def test_reports_distinguish_units_queue_and_execution_slots(tmp_path: Path) -> None:
    """The operator consumer reads actual weighted facts without creating a process lease."""
    from policynim.storage.operation_store import OperationStore

    admission = service(tmp_path)
    accepted = accept(admission)
    report = OperationStore.report(admission.store.path)
    assert (
        report.retained_rows,
        report.reserved_work_units,
        report.inactive_accepted_operations,
        report.active_operations,
        report.reserved_operations,
    ) == (1, 3, 1, 0, 1)
    admission.run_accepted(
        lambda: None,
        operation_id=accepted.operation_id,
        principal=principal(),
        operation_name="policy_search",
        cancelled=lambda: False,
    )
    report = OperationStore.report(admission.store.path)
    assert (
        report.retained_rows,
        report.reserved_work_units,
        report.inactive_accepted_operations,
        report.outcomes,
    ) == (1, 3, 0, {"completed": 1})
    admission.store.close()


def test_same_key_is_scoped_to_principal(tmp_path: Path) -> None:
    """Two users can choose an identical opaque key without sharing charge or result."""
    admission = service(tmp_path)
    alice = accept(admission)
    bob = accept(admission, subject="bob")
    assert alice.operation_id != bob.operation_id
    admission.store.close()


@pytest.mark.parametrize("units", [0, -1, True, 1.5])
def test_invalid_weights_cant_reach_durable_acceptance(tmp_path: Path, units: object) -> None:
    """Only validated positive integral work can create a charge."""
    admission = service(tmp_path)
    with pytest.raises(ValueError, match="positive integer"):
        admission.accept_work(
            principal=principal(),
            operation_name="policy_search",
            reservation_key="a" * 64,
            request_digest="b" * 64,
            work_units=units,  # type: ignore[arg-type]
        )
    assert admission.store.usage(principal_identity(principal().issuer, "alice")).accepted == 0
    admission.store.close()


def test_worker_process_loss_reconciles_charge_without_replaying(tmp_path: Path) -> None:
    """A real process exit retains accepted units and terminalizes its abandoned active slot."""
    import json
    import subprocess
    import sys

    code = """
import json, os, sys
from pathlib import Path
from policynim.services.authorization import AuthenticatedPrincipal
from policynim.services.operation_admission import OperationAdmissionService
from policynim.settings import Settings

worker_principal = AuthenticatedPrincipal(
    issuer="https://issuer.example",
    subject="alice",
    client_id="test",
    scopes=frozenset({"policies:read"}),
    source="oauth",
)
admission_service = OperationAdmissionService(
    Settings().model_copy(update={"beta_auth_db_path": Path(sys.argv[1])})
)
accepted_work = admission_service.accept_work(
    principal=worker_principal,
    operation_name="policy_search",
    reservation_key="a" * 64,
    request_digest="b" * 64,
    work_units=3,
)
print(json.dumps({"operation_id": accepted_work.operation_id}), flush=True)
admission_service.run_accepted(
    lambda: os._exit(9),
    operation_id=accepted_work.operation_id,
    principal=worker_principal,
    operation_name="policy_search",
    cancelled=lambda: False,
)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "auth.sqlite3")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 9
    operation_id = json.loads(result.stdout)["operation_id"]
    admission = service(tmp_path)
    owner = principal_identity(principal().issuer, "alice")
    usage = admission.store.usage(owner)
    assert (usage.accepted, usage.work_units, usage.interrupted, usage.active) == (1, 3, 1, 0)
    with pytest.raises(OperationAdmissionError, match="acceptance_finalized"):
        admission.run_accepted(
            lambda: pytest.fail("dead work replayed"),
            operation_id=operation_id,
            principal=principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )
    assert (
        admission.finish_accepted(
            operation_id=operation_id,
            principal_id=owner,
            operation_name="policy_search",
            outcome="cancelled",
        )
        == "interrupted"
    )
    admission.store.close()


def test_beta_portal_counts_weighted_units_and_revocation_blocks_activation(tmp_path: Path) -> None:
    """Legacy credentials share unit allowance while queued work can't bypass revocation."""
    from datetime import UTC, datetime

    from policynim.services.authorization import AuthorizationError, legacy_principal
    from policynim.services.beta_auth import BetaAuthService
    from policynim.storage.auth_store import AuthStore

    admission = service(tmp_path)
    settings = admission.settings.model_copy(update={"beta_daily_request_quota": 5})
    store = AuthStore(path=settings.beta_auth_db_path)
    auth = BetaAuthService(store=store, settings=settings)
    account = store.upsert_account_from_github(
        github_user_id=1, github_login="alice", email=None, now=datetime.now(UTC)
    )
    key = auth.issue_api_key(account_id=account.account_id)
    claims = legacy_principal(token=key.api_key, account_id=account.account_id)
    accepted = admission.accept_work(
        principal=claims,
        operation_name="policy_search",
        reservation_key="a" * 64,
        request_digest="b" * 64,
        work_units=3,
    )
    usage = auth.get_portal_usage(account.account_id)
    assert (usage.request_count, usage.remaining) == (3, 2)
    auth.issue_api_key(account_id=account.account_id)
    with pytest.raises(AuthorizationError, match="invalid_token"):
        admission.run_accepted(
            lambda: pytest.fail("revoked work executed"),
            operation_id=accepted.operation_id,
            principal=claims,
            operation_name="policy_search",
            cancelled=lambda: False,
        )
    owner = principal_identity(claims.issuer, claims.subject)
    assert (
        admission.finish_accepted(
            operation_id=accepted.operation_id,
            principal_id=owner,
            operation_name="policy_search",
            outcome="cancelled",
        )
        == "cancelled"
    )
    assert auth.get_portal_usage(account.account_id).request_count == 3
    admission.store.close()
    auth.close()


def test_simultaneous_duplicate_acceptance_has_one_charge(tmp_path: Path) -> None:
    """Independent submission transactions converge on one opaque acceptance identity."""
    from concurrent.futures import ThreadPoolExecutor

    services = [service(tmp_path) for _ in range(4)]
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda index: accept(services[index % 4]), range(12)))
        assert len({result.operation_id for result in results}) == 1
        usage = services[0].store.usage(principal_identity(principal().issuer, "alice"))
        assert (usage.accepted, usage.work_units, usage.inactive_accepted) == (1, 3, 1)
    finally:
        for admission in services:
            admission.store.close()


def test_queued_terminal_accounting_failure_pauses_new_acceptance(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed reconciliation remains visible after storage recovers and blocks new work."""
    import json
    import logging

    from policynim import operations

    operations._LOG.addHandler(caplog.handler)
    caplog.set_level(logging.ERROR)
    admission = service(tmp_path)
    accepted = accept(admission)
    with sqlite3.connect(admission.store.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_finish BEFORE UPDATE ON operation_usage "
            "BEGIN SELECT RAISE(ABORT, 'private provider content'); END"
        )
    with pytest.raises(OperationAdmissionError, match="accounting_incomplete"):
        admission.finish_accepted(
            operation_id=accepted.operation_id,
            principal_id=principal_identity(principal().issuer, "alice"),
            operation_name="policy_search",
            outcome="cancelled",
        )
    with sqlite3.connect(admission.store.path) as connection:
        connection.execute("DROP TRIGGER fail_finish")
    with pytest.raises(OperationAdmissionError, match="accounting_incomplete"):
        accept(admission, "new", units=1)
    admission.store.close()
    operations._LOG.removeHandler(caplog.handler)
    events = [
        json.loads(record.message) for record in caplog.records if record.message.startswith("{")
    ]
    failure = next(event for event in events if event.get("event") == "operation.storage_failure")
    assert failure["phase"] == "finish" and failure["operation_id"] == accepted.operation_id
    assert "private provider content" not in caplog.text


def test_queued_acceptance_survives_real_submitter_exit(tmp_path: Path) -> None:
    """A committed acceptance stays inactive and reusable after its submitting process exits."""
    import json
    import subprocess
    import sys

    code = """
import json, os, sys
from pathlib import Path
from policynim.services.authorization import AuthenticatedPrincipal
from policynim.services.operation_admission import OperationAdmissionService
from policynim.settings import Settings

worker_principal = AuthenticatedPrincipal(
    issuer="https://issuer.example",
    subject="alice",
    client_id="test",
    scopes=frozenset({"policies:read"}),
    source="oauth",
)
admission_service = OperationAdmissionService(
    Settings().model_copy(update={"beta_auth_db_path": Path(sys.argv[1])})
)
accepted_work = admission_service.accept_work(
    principal=worker_principal,
    operation_name="policy_search",
    reservation_key="a" * 64,
    request_digest="b" * 64,
    work_units=3,
)
print(json.dumps({"operation_id": accepted_work.operation_id}), flush=True)
os._exit(7)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "auth.sqlite3")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 7
    admission = service(tmp_path)
    replay = admission.accept_work(
        principal=principal(),
        operation_name="policy_search",
        reservation_key="a" * 64,
        request_digest="b" * 64,
        work_units=3,
    )
    assert replay.operation_id == json.loads(result.stdout)["operation_id"]
    usage = admission.store.usage(principal_identity(principal().issuer, "alice"))
    assert (usage.accepted, usage.work_units, usage.inactive_accepted, usage.interrupted) == (
        1,
        3,
        1,
        0,
    )
    admission.store.close()


def test_concurrent_historical_schema_migration_preserves_one_unit_default(tmp_path: Path) -> None:
    """Independent process handles serialize the migration rather than adding columns twice."""
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "auth.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE operation_usage (operation_id TEXT PRIMARY KEY, "
            "principal_id TEXT NOT NULL, operation TEXT NOT NULL, usage_date TEXT NOT NULL, "
            "outcome TEXT NOT NULL, process_id INTEGER NOT NULL, started_at REAL NOT NULL, "
            "finished_at REAL, owner_id TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO operation_usage VALUES "
            "('old','owner','policy_search','2026-10-20','completed',1,1,2,'old-owner')"
        )

    def open_and_close(_: int) -> None:
        """Race the public initializer on one former-schema database."""
        admission = service(tmp_path)
        admission.store.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(open_and_close, range(8)))
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*),SUM(work_units) FROM operation_usage"
        ).fetchone() == (1, 1)


def test_submission_deadline_rechecked_after_real_writer_wait_before_charge(tmp_path: Path) -> None:
    """Cancellation while waiting on SQLite can't commit a prepaid acceptance afterward."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from policynim.errors import ProviderError

    admission = service(tmp_path)
    checked, cancelled = Event(), Event()

    def check_ready() -> None:
        """Represent the public caller budget changing while another transaction owns the writer."""
        checked.set()
        if cancelled.is_set():
            raise ProviderError("Operation cancelled before admission.", failure_class="cancelled")

    with (
        sqlite3.connect(admission.store.path) as writer,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        writer.execute("BEGIN IMMEDIATE")
        pending = executor.submit(
            admission.accept_work,
            principal=principal(),
            operation_name="policy_search",
            reservation_key="a" * 64,
            request_digest="b" * 64,
            work_units=3,
            check_ready=check_ready,
        )
        assert checked.wait(2)
        cancelled.set()
        writer.commit()
        with pytest.raises(ProviderError, match="cancelled"):
            pending.result(timeout=3)
    assert admission.store.usage(principal_identity(principal().issuer, "alice")).accepted == 0
    admission.store.close()


@pytest.mark.parametrize("worker_context", [False, True])
def test_activation_keeps_correlation_when_worker_context_is_missing(
    tmp_path: Path, worker_context: bool
) -> None:
    """A fresh worker context replaces correlation, while absent context can't erase it."""
    from contextlib import nullcontext

    from policynim.operations import operation_correlation

    admission = service(tmp_path)
    with operation_correlation() as accepted_correlation:
        accepted = accept(admission)
    with operation_correlation() if worker_context else nullcontext() as worker_correlation:
        admission.run_accepted(
            lambda: None,
            operation_id=accepted.operation_id,
            principal=principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )
    with sqlite3.connect(admission.store.path) as connection:
        persisted = connection.execute("SELECT correlation_id FROM operation_usage").fetchone()[0]
    assert persisted == (worker_correlation if worker_context else accepted_correlation)
    admission.store.close()
