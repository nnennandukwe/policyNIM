"""Terminal accounting survives failed cancellation reads through both public paths."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from policynim.errors import ProviderError
from policynim.services.authorization import AuthenticatedPrincipal, principal_identity
from policynim.services.operation_admission import OperationAdmissionService
from policynim.settings import Settings


@pytest.fixture
def admission(tmp_path: Path):
    """Own a real quota database with one active slot and enough retained work units."""
    service = OperationAdmissionService(
        Settings().model_copy(
            update={
                "beta_auth_db_path": tmp_path / "quota.sqlite3",
                "mcp_principal_daily_operation_quota": 20,
                "mcp_max_concurrent_operations": 1,
                "mcp_max_concurrent_operations_per_principal": 1,
            }
        )
    )
    try:
        yield service
    finally:
        service.store.close()


def _principal() -> AuthenticatedPrincipal:
    """Use claims without any token or provider credential."""
    return AuthenticatedPrincipal(
        issuer="https://issuer.example",
        subject="alice",
        client_id="test",
        scopes=frozenset({"policies:read", "policies:evaluate"}),
        source="oauth",
    )


def _run(
    admission: OperationAdmissionService,
    prepaid: bool,
    operation: Callable[[], str],
    cancelled: Callable[[], bool],
) -> str:
    """Exercise either interactive admission or activation of three prepaid units."""
    if prepaid:
        accepted = admission.accept_work(
            principal=_principal(),
            operation_name="policy_search",
            reservation_key="a" * 64,
            request_digest="b" * 64,
            work_units=3,
        )
        return admission.run_accepted(
            operation,
            operation_id=accepted.operation_id,
            principal=_principal(),
            operation_name="policy_search",
            cancelled=cancelled,
        )
    return admission.run(
        operation,
        principal=_principal(),
        operation_name="policy_search",
        cancelled=cancelled,
    )


@pytest.mark.parametrize("prepaid", [False, True])
@pytest.mark.parametrize("work_fails", [False, True])
def test_locked_cancellation_read_still_terminalizes_and_releases_capacity(
    tmp_path: Path,
    admission: OperationAdmissionService,
    prepaid: bool,
    work_fails: bool,
) -> None:
    """A separate locked job database can't strand quota slots or mask a provider failure."""
    job_path = tmp_path / "jobs.sqlite3"
    primary_error = ProviderError("Provider work failed.")
    with sqlite3.connect(job_path) as writer, sqlite3.connect(job_path, timeout=0.01) as reader:
        writer.execute("CREATE TABLE cancellation (requested INTEGER)")
        writer.execute("INSERT INTO cancellation VALUES (0)")
        writer.commit()

        def cancelled() -> bool:
            """Read the actual separate job database, including its real lock failure."""
            return bool(reader.execute("SELECT requested FROM cancellation").fetchone()[0])

        def operation() -> str:
            """Hold an exclusive job-state lock only after quota admission has succeeded."""
            writer.execute("BEGIN EXCLUSIVE")
            if work_fails:
                raise primary_error
            return "provider completed"

        try:
            expected = ProviderError if work_fails else sqlite3.OperationalError
            with pytest.raises(expected) as caught:
                _run(admission, prepaid, operation, cancelled)
            if work_fails:
                assert caught.value is primary_error
                assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
        finally:
            writer.rollback()
    usage = admission.store.usage(principal_identity(_principal().issuer, "alice"))
    assert (usage.accepted, usage.active, usage.failed, usage.work_units) == (
        1,
        0,
        1,
        3 if prepaid else 1,
    )
    assert (
        admission.run(
            lambda: "next work",
            principal=_principal(),
            operation_name="policy_search",
            cancelled=lambda: False,
        )
        == "next work"
    )


@pytest.mark.parametrize("prepaid", [False, True])
def test_cancelled_after_drain_wins_without_refund(
    admission: OperationAdmissionService,
    prepaid: bool,
) -> None:
    """Completed provider work can retain a cancelled caller outcome and its reserved units."""
    drained = False

    def operation() -> str:
        """Signal cancellation after the provider operation finishes."""
        nonlocal drained
        drained = True
        return "provider completed"

    assert _run(admission, prepaid, operation, lambda: drained) == "provider completed"
    usage = admission.store.usage(principal_identity(_principal().issuer, "alice"))
    assert (usage.active, usage.cancelled, usage.work_units) == (0, 1, 3 if prepaid else 1)


@pytest.mark.parametrize("prepaid", [False, True])
def test_cancellation_probe_interruption_still_releases_slot(
    admission: OperationAdmissionService,
    prepaid: bool,
) -> None:
    """An interrupted terminal probe propagates only after persisted slot release."""
    drained = False
    interruption = KeyboardInterrupt()

    def operation() -> str:
        """Finish accepted provider work before the cancellation probe interrupts."""
        nonlocal drained
        drained = True
        return "provider completed"

    def cancelled() -> bool:
        """Interrupt only the terminal read, leaving pre-admission checks successful."""
        if drained:
            raise interruption
        return False

    with pytest.raises(KeyboardInterrupt) as caught:
        _run(admission, prepaid, operation, cancelled)
    assert caught.value is interruption
    usage = admission.store.usage(principal_identity(_principal().issuer, "alice"))
    assert (usage.active, usage.failed, usage.work_units) == (0, 1, 3 if prepaid else 1)


@pytest.mark.parametrize("work_interrupts", [False, True])
def test_terminal_probe_preserves_interruption_priority(
    admission: OperationAdmissionService,
    work_interrupts: bool,
) -> None:
    """Preserve the original work error unless the terminal probe introduces an interruption."""
    work_error = KeyboardInterrupt() if work_interrupts else ProviderError("Provider failed.")
    probe_error = (
        RuntimeError("Cancellation read failed.") if work_interrupts else KeyboardInterrupt()
    )
    started = False

    def operation() -> str:
        """Fail only after acquiring the persisted active slot."""
        nonlocal started
        started = True
        raise work_error

    def cancelled() -> bool:
        """Introduce the second failure during terminal cleanup only."""
        if started:
            raise probe_error
        return False

    with pytest.raises(KeyboardInterrupt) as caught:
        _run(admission, False, operation, cancelled)
    assert caught.value is (work_error if work_interrupts else probe_error)
    assert caught.value.__cause__ is (probe_error if work_interrupts else work_error)
    usage = admission.store.usage(principal_identity(_principal().issuer, "alice"))
    assert (usage.active, usage.failed, usage.work_units) == (0, 1, 1)
