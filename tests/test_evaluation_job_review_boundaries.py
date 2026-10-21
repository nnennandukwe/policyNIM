"""Public job operations across committed cancellation and bounded payload reads."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_evaluation_jobs import evaluator_factory, job_setup, principal
from test_grounded_mcp import generation as generation_fixture

from policynim.job_types import EvaluationJobError
from policynim.services.evaluation_jobs import EvaluationJobWorker
from policynim.services.generations import GenerationStore
from policynim.storage import evaluation_jobs as storage

generation = generation_fixture


@pytest.mark.parametrize("failure_target", ["quota", "acknowledgement"])
def test_committed_cancel_returns_its_state_and_reconciles_accounting_after_repair(
    generation, tmp_path, caplog, failure_target
):
    """A real quota-write failure can't hide an already committed public cancellation."""
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="cancel-ledger-fault", principal=principal())
    failure_path = (
        service.settings.beta_auth_db_path if failure_target == "quota" else service.store.path
    )
    target = (
        "outcome ON operation_usage"
        if failure_target == "quota"
        else "admission_finalized ON evaluation_jobs"
    )
    with sqlite3.connect(failure_path) as db:
        db.execute(
            f"CREATE TRIGGER reject_terminal BEFORE UPDATE OF {target} "
            "BEGIN SELECT RAISE(FAIL,'private-cancel-ledger-sentinel'); END"
        )
    cancelled = service.cancel(accepted.job_id, principal=principal())
    assert cancelled.state == "cancelled"
    assert service.inspect(accepted.job_id, principal=principal()) == cancelled
    report = service.result(accepted.job_id, principal=principal())
    assert report.report.state == "cancelled"
    assert report.report.usage.provider_attempts == 0
    assert [job.job_id for job in service.store.pending_admission_finalizations()] == [
        accepted.job_id
    ]
    assert "private-cancel-ledger-sentinel" not in caplog.text
    assert "evaluation.cancellation_accounting_pending" in caplog.text
    with sqlite3.connect(service.settings.beta_auth_db_path) as db:
        assert db.execute("SELECT outcome,work_units FROM operation_usage").fetchone() == (
            "accepted" if failure_target == "quota" else "cancelled",
            1,
        )
    with sqlite3.connect(failure_path) as db:
        db.execute("DROP TRIGGER reject_terminal")
    calls: list[str] = []
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))
    try:
        assert worker.run_once() is None
    finally:
        worker.close()
    assert calls == [] and service.store.pending_admission_finalizations() == []
    assert service.result(accepted.job_id, principal=principal()).sha256 == report.sha256
    with sqlite3.connect(service.settings.beta_auth_db_path) as db:
        assert db.execute("SELECT outcome,work_units FROM operation_usage").fetchone() == (
            "cancelled",
            1,
        )


def test_public_inspection_doesnt_read_retained_snapshot_or_report_columns(
    generation, tmp_path, monkeypatch
):
    """SQLite itself denies payload reads while the owner polls a real retained job."""
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="summary-only", principal=principal())
    cancelled = service.cancel(accepted.job_id, principal=principal())
    original = service.store._connect
    forbidden_reads: list[str] = []

    def summary_connection():
        """Keep the actual SQLite handle while denying only irrelevant payload columns."""
        connection = original()

        def authorize(action, table, column, database, caller):
            """Observe actual reads rather than inferring them from SQL text."""
            if (
                action == sqlite3.SQLITE_READ
                and table == "evaluation_jobs"
                and column in {"snapshot_json", "report_json"}
            ):
                forbidden_reads.append(column)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(service.store, "_connect", summary_connection)
    assert service.inspect(accepted.job_id, principal=principal()) == cancelled
    with pytest.raises(EvaluationJobError, match="job_unavailable"):
        service.inspect(accepted.job_id, principal=principal("another-owner"))
    assert forbidden_reads == []


def test_exported_over_limit_file_is_rejected_before_buffering_storage_or_charge(
    generation, tmp_path, monkeypatch
):
    """A real over-limit sparse capture file can't reach read_bytes or the quota COMMIT."""
    service, suite, _ = job_setup(generation, tmp_path)
    reads: list[Path] = []
    original_read = Path.read_bytes

    def oversized_export(store, destination, generation_id=None):
        """Inject the export boundary's size effect without allocating a 257 MiB buffer."""
        with destination.open("wb") as output:
            output.truncate(256 * 1024 * 1024 + 1)
        return "sha256:" + "0" * 64

    def reject_large_read(path):
        """An attempted full read would bypass the required public capture size guard."""
        if path.name == "generation.zip":
            reads.append(path)
            raise AssertionError("An oversized capture mustn't be buffered.")
        return original_read(path)

    monkeypatch.setattr(GenerationStore, "export_bundle", oversized_export)
    monkeypatch.setattr(Path, "read_bytes", reject_large_read)
    with pytest.raises(EvaluationJobError, match="archive_too_large"):
        service.submit(suite, idempotency_key="oversized-capture", principal=principal())
    assert reads == [] and service.store.aggregate_counts() == {}
    with sqlite3.connect(service.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM evaluation_job_archives").fetchone()[0] == 0
    with sqlite3.connect(service.settings.beta_auth_db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM operation_usage").fetchone()[0] == 0


def test_worker_rejects_retained_archive_size_before_loading_or_provider_work(
    generation, tmp_path, monkeypatch
):
    """A reduced bound tests actual accepted-input restoration without huge fixture buffers."""
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="retained-bound", principal=principal())
    monkeypatch.setattr(storage, "MAX_GENERATION_ARCHIVE_BYTES", 1, raising=False)
    calls: list[str] = []
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))
    try:
        result = worker.run_once()
    finally:
        worker.close()
    assert result is not None and result.job_id == accepted.job_id
    assert result.state == "failed" and result.failure_code == "archive_too_large"
    assert calls == []
    report = service.result(accepted.job_id, principal=principal())
    assert report.report.usage.provider_attempts == 0
    assert report.report.unfinished_case_ids == [suite.cases[0].case_id]


def test_public_submission_checks_captured_bytes_before_archive_insert(
    generation, tmp_path, monkeypatch
):
    """The store rejects actual captured bytes before INSERT even if its caller misses the cap."""
    service, suite, _ = job_setup(generation, tmp_path)
    monkeypatch.setattr(storage, "MAX_GENERATION_ARCHIVE_BYTES", 1, raising=False)
    with pytest.raises(EvaluationJobError, match="archive_too_large"):
        service.submit(suite, idempotency_key="captured-bytes-bound", principal=principal())
    assert service.store.aggregate_counts() == {}
    with sqlite3.connect(service.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM evaluation_job_archives").fetchone()[0] == 0
    with sqlite3.connect(service.settings.beta_auth_db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM operation_usage").fetchone()[0] == 0
