"""Keyed interruption cleanup through real quota ownership and SQLite failures."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread

import pytest
from test_weighted_operation_admission import accept, principal, service

from policynim.services.authorization import principal_identity
from policynim.services.operation_admission import OperationAdmissionError
from policynim.storage.operation_store import OperationStore


@pytest.mark.parametrize("mismatch", ["owner", "principal", "operation"])
def test_interrupt_cant_finish_another_active_acceptance(tmp_path, monkeypatch, mismatch):
    """Interrupting a rejected activation never changes a live owner's scoped acceptance."""
    first = service(tmp_path)
    second = service(tmp_path)
    accepted = accept(first)
    started, release = Event(), Event()
    caller = second if mismatch == "owner" else first

    def held_work():
        """Keep one real quota owner live while the other activation is interrupted."""
        started.set()
        assert release.wait(5)

    def interrupt_activation(**kwargs):
        """Interrupt before the storage binding check can confirm this activation."""
        raise KeyboardInterrupt

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(
                first.run_accepted,
                held_work,
                operation_id=accepted.operation_id,
                principal=principal(),
                operation_name="policy_search",
                cancelled=lambda: False,
            )
            try:
                assert started.wait(3)
                monkeypatch.setattr(caller.store, "activate", interrupt_activation)
                with pytest.raises(KeyboardInterrupt):
                    caller.run_accepted(
                        lambda: pytest.fail("Rejected work can't run."),
                        operation_id=accepted.operation_id,
                        principal=principal("other") if mismatch == "principal" else principal(),
                        operation_name="policy_compile"
                        if mismatch == "operation"
                        else "policy_search",
                        cancelled=lambda: False,
                    )
                report = OperationStore.report(first.store.path)
                assert report.active_operations == 1 and report.outcomes == {"accepted": 1}
            finally:
                release.set()
            running.result(timeout=3)
        report = OperationStore.report(first.store.path)
        assert report.active_operations == 0 and report.outcomes == {"completed": 1}
        assert report.reserved_work_units == 3
    finally:
        release.set()
        first.store.close()
        second.store.close()


def test_interrupt_cleanup_write_failure_pauses_until_recovery(tmp_path, monkeypatch, caplog):
    """A real failed quota update preserves the interrupt and its live ownership evidence."""
    admission = service(tmp_path)
    accepted = accept(admission)

    def interrupt_before_body(*args, **kwargs):
        """Interrupt after the real activation transaction and before execution starts."""
        raise KeyboardInterrupt

    monkeypatch.setattr(admission, "_execute", interrupt_before_body)
    with sqlite3.connect(admission.store.path) as connection:
        connection.execute(
            "CREATE TRIGGER deny_interrupt BEFORE UPDATE OF outcome ON operation_usage "
            "WHEN NEW.outcome='interrupted' BEGIN "
            "SELECT RAISE(FAIL, 'private-accounting-fixture'); END"
        )
    try:
        with pytest.raises(KeyboardInterrupt):
            admission.run_accepted(
                lambda: pytest.fail("Interrupted work can't start."),
                operation_id=accepted.operation_id,
                principal=principal(),
                operation_name="policy_search",
                cancelled=lambda: False,
            )
        assert admission._accounting_incomplete.is_set()
        report = OperationStore.report(admission.store.path)
        assert report.active_operations == 1 and report.outcomes == {"accepted": 1}
        assert "operation.storage_failure" in caplog.text
        assert "private-accounting-fixture" not in caplog.text
        with pytest.raises(OperationAdmissionError, match="accounting_incomplete"):
            admission.run_accepted(
                lambda: pytest.fail("Paused work can't start."),
                operation_id=accepted.operation_id,
                principal=principal(),
                operation_name="policy_search",
                cancelled=lambda: False,
            )
    finally:
        with sqlite3.connect(admission.store.path) as connection:
            connection.execute("DROP TRIGGER deny_interrupt")
        assert (
            admission.store.finish_accepted(
                operation_id=accepted.operation_id,
                principal_id=principal_identity(principal().issuer, principal().subject),
                operation="policy_search",
                outcome="interrupted",
                release_owned=True,
            )
            == "interrupted"
        )
        admission.store.close()
    report = OperationStore.report(admission.store.path)
    assert report.active_operations == 0 and report.outcomes == {"interrupted": 1}
    assert report.reserved_work_units == 3


def test_interrupt_cleanup_retries_a_real_busy_writer(tmp_path, monkeypatch):
    """A proven writer lock retries owned cleanup before returning the original interrupt."""
    admission = service(tmp_path)
    accepted = accept(admission)
    locked, release = Event(), Event()
    attempts = []
    finish = admission.store.finish_accepted

    def hold_writer():
        """Hold a separate connection until interruption cleanup observes its contention."""
        with sqlite3.connect(admission.store.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            assert release.wait(5)

    writer = Thread(target=hold_writer)

    def interrupt_before_body(*args, **kwargs):
        """Acquire the contending lock only after the real activation has committed."""
        writer.start()
        assert locked.wait(3)
        raise KeyboardInterrupt

    def finalize(**kwargs):
        """Release the writer after the first actual SQLite cleanup attempt returns."""
        attempts.append(True)
        try:
            return finish(**kwargs)
        finally:
            release.set()

    monkeypatch.setattr(admission, "_execute", interrupt_before_body)
    monkeypatch.setattr(admission.store, "finish_accepted", finalize)
    try:
        with pytest.raises(KeyboardInterrupt):
            admission.run_accepted(
                lambda: pytest.fail("Interrupted work can't start."),
                operation_id=accepted.operation_id,
                principal=principal(),
                operation_name="policy_search",
                cancelled=lambda: False,
            )
        assert len(attempts) >= 2 and not admission._accounting_incomplete.is_set()
        report = OperationStore.report(admission.store.path)
        assert report.active_operations == 0 and report.outcomes == {"interrupted": 1}
        assert report.reserved_work_units == 3
    finally:
        release.set()
        writer.join(3)
        admission.store.close()
