"""Durable job lifecycle through real SQLite, generation archives and worker ownership."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_grounded_mcp import generation as generation_fixture

from policynim.job_types import EvaluationJobLimits
from policynim.services.authorization import AuthenticatedPrincipal
from policynim.services.evaluation_jobs import EvaluationJobError, EvaluationJobService
from policynim.storage.evaluation_jobs import EvaluationJobStore
from policynim.types import EvalCase, EvalSuite

generation = generation_fixture


def principal(subject: str = "owner") -> AuthenticatedPrincipal:
    """Create a verified test principal without a bearer or provider secret."""
    return AuthenticatedPrincipal(
        issuer="https://issuer.example",
        subject=subject,
        client_id="test-client",
        scopes=frozenset({"policies:read", "policies:evaluate"}),
        source="oauth",
    )


def suite_for(store) -> EvalSuite:
    """Pin one authored scenario to the real generation catalog."""
    with store.pin() as snapshot:
        digest = snapshot.catalog.digest
    return EvalSuite(
        name="durable fixture",
        expected_catalog_digest=digest,
        cases=[
            EvalCase(
                case_id="case-one",
                kind="search",
                input="review",
                expected_insufficient_context=True,
            )
        ],
    )


def test_submission_deduplicates_and_survives_service_restart(generation, tmp_path: Path) -> None:
    """Repeated submissions return the original accepted identity without changing its inputs."""
    settings, generations, generation_id = generation
    settings = settings.model_copy(update={"eval_workspace_dir": tmp_path / "jobs"})
    service = EvaluationJobService(settings)
    suite = suite_for(generations)
    accepted = service.submit(suite, idempotency_key="first", principal=principal())
    assert accepted.state == "queued"
    assert accepted.generation_id == generation_id
    duplicate = service.submit(suite, idempotency_key="first", principal=principal())
    assert duplicate.job_id == accepted.job_id
    restarted = EvaluationJobService(settings)
    assert restarted.inspect(accepted.job_id, principal=principal()) == accepted
    changed = suite.model_copy(update={"name": "changed"})
    with pytest.raises(EvaluationJobError, match="idempotency_conflict"):
        service.submit(changed, idempotency_key="first", principal=principal())


def test_owner_isolation_covers_inspect_cancel_and_result(generation, tmp_path: Path) -> None:
    """Another principal can't inspect, cancel or read even a known job ID."""
    settings, generations, _ = generation
    settings = settings.model_copy(update={"eval_workspace_dir": tmp_path / "jobs"})
    service = EvaluationJobService(settings)
    accepted = service.submit(
        suite_for(generations), idempotency_key="first", principal=principal()
    )
    for operation in (service.inspect, service.cancel, service.result):
        with pytest.raises(EvaluationJobError, match="job_unavailable"):
            operation(accepted.job_id, principal=principal("another-owner"))
    assert service.inspect(accepted.job_id, principal=principal()).state == "queued"


def test_bounded_admission_and_queued_cancellation(generation, tmp_path: Path) -> None:
    """Queue rejection doesn't accept another job, and queued cancellation starts no provider."""
    settings, generations, _ = generation
    settings = settings.model_copy(update={"eval_workspace_dir": tmp_path / "jobs"})
    store = EvaluationJobStore(tmp_path / "jobs.sqlite3", queue_limit=1)
    service = EvaluationJobService(settings, store=store)
    suite = suite_for(generations)
    accepted = service.submit(suite, idempotency_key="first", principal=principal())
    with pytest.raises(EvaluationJobError, match="queue_full"):
        service.submit(suite, idempotency_key="second", principal=principal())
    cancelled = service.cancel(accepted.job_id, principal=principal())
    assert cancelled.state == "cancelled"
    report = service.result(accepted.job_id, principal=principal())
    assert report.report.unfinished_case_ids == ["case-one"]
    assert report.report.usage.provider_attempts == 0
    assert service.cancel(accepted.job_id, principal=principal()) == cancelled
    assert service.submit(suite, idempotency_key="second", principal=principal()).state == "queued"


def test_reject_invalid_limits_before_accepting_work() -> None:
    """No unbounded deadline, call count or retention can enter the durable schema."""
    from pydantic import ValidationError

    for values in (
        {"deadline_seconds": float("inf")},
        {"deadline_seconds": 0},
        {"max_provider_calls": 0},
        {"max_cases": 0},
        {"retention_seconds": 0},
    ):
        with pytest.raises(ValidationError):
            EvaluationJobLimits.model_validate(values)


def case_result(settings, suite: EvalSuite, case_id: str):
    """Build a typed evaluator response at the sole model/scoring injection boundary."""
    from policynim.types import EvalCaseMetrics, EvalCaseResult

    case = next(item for item in suite.cases if item.case_id == case_id)
    return EvalCaseResult(
        generation_id=settings.generation_id,
        case_id=case_id,
        kind=case.kind,
        input=case.input,
        top_k=case.top_k,
        rerank_enabled=True,
        passed=True,
        expected_insufficient_context=True,
        actual_insufficient_context=True,
        metrics=EvalCaseMetrics(insufficient_context_correct=True),
    )


def job_setup(generation_value, tmp_path: Path, *, cases: int = 1):
    """Create private real job/auth stores and a captured catalog-pinned suite."""
    settings, generations, generation_id = generation_value
    settings = settings.model_copy(
        update={
            "eval_workspace_dir": tmp_path / "evaluations",
            "beta_auth_db_path": tmp_path / "auth.sqlite3",
        }
    )
    suite = suite_for(generations)
    suite = suite.model_copy(
        update={
            "cases": [
                suite.cases[0].model_copy(update={"case_id": f"case-{index}"})
                for index in range(cases)
            ]
        }
    )
    return EvaluationJobService(settings), suite, generation_id


def evaluator_factory(calls: list[str]):
    """Return an injected evaluator with real usage accounting and generation inspection."""
    from policynim.operations import current_budget

    class Evaluator:
        """Replace only NVIDIA execution and scoring, preserving worker storage/lifecycle."""

        def __init__(self, settings):
            """Keep the restored immutable generation settings for public reads."""
            assert settings.profile is None
            self.settings = settings

        def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
            """Reserve and measure exactly one deterministic provider attempt."""
            calls.append(case_id)
            current_budget().reserve_provider_call()
            current_budget().record_provider_usage(7)
            return case_result(self.settings, suite, case_id)

        def close(self):
            """Record cleanup as part of the owned active-job lifecycle."""
            calls.append("closed")

    return Evaluator


def test_worker_uses_captured_generation_after_source_changes_and_purge(
    generation, tmp_path
) -> None:
    """Accepted work restores exact archived inputs even after public retention removes them."""
    from test_ingest_service import MockEmbedder

    from policynim.services.evaluation_jobs import EvaluationJobWorker
    from policynim.storage.evaluation_jobs import payload_digest

    service, suite, generation_id = job_setup(generation, tmp_path, cases=2)
    accepted = service.submit(suite, idempotency_key="archived", principal=principal())
    service.settings.profile = tmp_path / "changed-external-profile.toml"
    settings, generations, _ = generation
    source = Path(settings.corpus_dir) / "A.md"
    for index in range(2):
        source.write_text(source.read_text() + f"\nLater source revision {index}.\n")
        generations.build(settings, embedder=MockEmbedder())
    assert generation_id in generations.prune(keep_recent=0).removed
    assert (
        service.submit(suite, idempotency_key="archived", principal=principal()).job_id
        == accepted.job_id
    )
    restarted = EvaluationJobService(service.settings)
    calls: list[str] = []
    worker = EvaluationJobWorker(restarted, evaluator_factory=evaluator_factory(calls))
    completed = worker.run_once()
    assert completed is not None and completed.state == "completed"
    assert calls == ["case-0", "case-1", "closed"]
    report = restarted.result(accepted.job_id, principal=principal())
    assert report.report.snapshot.generation_id == generation_id
    assert report.report.snapshot.suite == suite
    assert report.sha256 == payload_digest(report.report.model_dump(mode="json"))
    assert report.report.usage.provider_attempts == 2
    assert report.report.usage.measured_tokens == 14
    assert report.report.usage.usage_status == "measured"
    assert report.report.unfinished_case_ids == []
    assert worker.run_once() is None
    assert calls == ["case-0", "case-1", "closed"]


def test_provider_budget_exhaustion_retains_completed_case(generation, tmp_path) -> None:
    """The second case can't reserve a provider effect after the accepted budget is consumed."""
    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path, cases=2)
    accepted = service.submit(
        suite,
        idempotency_key="bounded",
        principal=principal(),
        limits=EvaluationJobLimits(max_provider_calls=1),
    )
    calls: list[str] = []
    result = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls)).run_once()
    assert result is not None and result.state == "budget_exhausted"
    assert service.result(accepted.job_id, principal=principal()).report.uncertain_case_id is None
    report = service.result(accepted.job_id, principal=principal()).report
    assert [item.case_id for item in report.completed_cases] == ["case-0"]
    assert report.unfinished_case_ids == ["case-1"]
    assert report.usage.provider_attempts == 1
    assert report.usage.measured_tokens == 7


def test_queued_deadline_expires_before_provider_allocation(generation, tmp_path) -> None:
    """A restart can't reset the accepted wall-clock deadline."""
    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    now = [1000.0]
    service.store.clock = lambda: now[0]
    accepted = service.submit(
        suite,
        idempotency_key="deadline",
        principal=principal(),
        limits=EvaluationJobLimits(deadline_seconds=1),
    )
    now[0] += 2
    calls: list[str] = []
    finished = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls)).run_once()
    assert finished is not None and finished.state == "deadline_exceeded"
    assert calls == []
    assert (
        service.result(accepted.job_id, principal=principal()).report.usage.provider_attempts == 0
    )


def test_cancellation_during_provider_work_drains_before_terminal_state(
    generation, tmp_path
) -> None:
    """Cancellation remains requested until the active provider result and owned cleanup drain."""
    from threading import Event, Thread

    from policynim.operations import current_budget
    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="cancel", principal=principal())
    started, release = Event(), Event()
    closed: list[str] = []

    class Evaluator:
        """Hold the provider seam while another client requests cancellation."""

        def __init__(self, settings):
            """Capture the restored generation for the eventual fixture response."""
            self.settings = settings

        def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
            """Retain uncertain usage while blocked, then publish returned measured usage."""
            current_budget().reserve_provider_call()
            started.set()
            assert release.wait(5)
            current_budget().record_provider_usage(9)
            current_budget().remaining()
            return case_result(self.settings, suite, case_id)

        def close(self):
            """Prove provider ownership drains before terminal cancellation."""
            closed.append("closed")

    results: list = []
    worker = EvaluationJobWorker(service, evaluator_factory=Evaluator)
    thread = Thread(target=lambda: results.append(worker.run_once()))
    thread.start()
    assert started.wait(5)
    second_client = EvaluationJobService(service.settings)
    requested = second_client.cancel(accepted.job_id, principal=principal())
    assert requested.state == "running" and requested.cancel_requested
    assert closed == []
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert results[0].state == "cancelled"
    assert closed == ["closed"]
    report = service.result(accepted.job_id, principal=principal()).report
    assert report.uncertain_case_id == "case-0"
    assert report.usage.measured_tokens == 9
    assert report.unfinished_case_ids == ["case-0"]


def test_expiry_purges_only_retained_owner_payloads(generation, tmp_path) -> None:
    """Expired results fail explicitly while their digest and summary stay inspectable."""
    service, suite, _ = job_setup(generation, tmp_path)
    now = [1000.0]
    service.store.clock = lambda: now[0]
    accepted = service.submit(
        suite,
        idempotency_key="expiry",
        principal=principal(),
        limits=EvaluationJobLimits(retention_seconds=60),
    )
    cancelled = service.cancel(accepted.job_id, principal=principal())
    assert cancelled.report_sha256
    now[0] += 61
    with pytest.raises(EvaluationJobError, match="result_expired"):
        service.result(accepted.job_id, principal=principal())
    assert service.store.purge_expired() == 1
    summary = service.inspect(accepted.job_id, principal=principal())
    assert summary.result_expired and summary.report_sha256 == cancelled.report_sha256
    assert (
        service.submit(
            suite,
            idempotency_key="expiry",
            principal=principal(),
            limits=EvaluationJobLimits(retention_seconds=60),
        ).job_id
        == accepted.job_id
    )


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_submission_commit_failure_preserves_one_retry_path(generation, tmp_path, phase) -> None:
    """Failure on either side of SQLite COMMIT never creates a second accepted job."""
    import sqlite3

    service, suite, _ = job_setup(generation, tmp_path)
    injected = [False]

    def fault(observed: str) -> None:
        """Interrupt exactly one transaction at the chosen commit boundary."""
        if not injected[0] and observed == phase:
            injected[0] = True
            raise sqlite3.OperationalError("fixture transaction interruption")

    service.store.fault = fault
    with pytest.raises(EvaluationJobError, match="job_storage_unavailable"):
        service.submit(suite, idempotency_key="commit", principal=principal())
    service.store.fault = None
    expected = 0 if phase == "before_commit" else 1
    assert service.store.aggregate_counts().get("queued", 0) == expected
    accepted = service.submit(suite, idempotency_key="commit", principal=principal())
    assert accepted.state == "queued"
    assert service.store.aggregate_counts() == {"queued": 1}


def test_competing_workers_cannot_execute_the_same_job(generation, tmp_path) -> None:
    """A competing lock holder can't recover or run the active provider stage."""
    from threading import Event, Thread

    from policynim.operations import current_budget
    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="concurrent", principal=principal())
    started, release = Event(), Event()
    calls: list[str] = []

    class Evaluator:
        """Hold the sole supported worker's provider boundary."""

        def __init__(self, settings):
            """Capture accepted generation settings."""
            self.settings = settings

        def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
            """Reserve one effect, keeping the competing process outside this stage."""
            current_budget().reserve_provider_call()
            calls.append(case_id)
            started.set()
            assert release.wait(5)
            current_budget().record_provider_usage(1)
            return case_result(self.settings, suite, case_id)

        def close(self):
            """This fixture owns no additional resource."""

    first = EvaluationJobWorker(service, evaluator_factory=Evaluator)
    second = EvaluationJobWorker(
        EvaluationJobService(service.settings), evaluator_factory=Evaluator
    )
    results: list = []
    thread = Thread(target=lambda: results.append(first.run_once()))
    thread.start()
    assert started.wait(5)
    try:
        with pytest.raises(EvaluationJobError, match="worker_busy"):
            second.run_once()
        assert service.inspect(accepted.job_id, principal=principal()).state == "running"
    finally:
        release.set()
        thread.join(5)
    assert results[0].state == "completed"
    assert calls == ["case-0"]
    assert second.run_once() is None


def test_case_persistence_failure_retains_uncertain_work_without_replay(
    generation, tmp_path, monkeypatch
) -> None:
    """A response followed by a failed case commit can't cause an automatic provider repeat."""
    import sqlite3

    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="save-failure", principal=principal())
    calls: list[str] = []

    def fail_save(*args, **kwargs):
        """Inject failure after the fake provider has returned measured usage."""
        raise sqlite3.OperationalError("fixture persistence failure")

    monkeypatch.setattr(service.store, "save_case", fail_save)
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))
    result = worker.run_once()
    assert result is not None and result.state == "interrupted"
    report = service.result(accepted.job_id, principal=principal()).report
    assert report.uncertain_case_id == "case-0"
    assert report.unfinished_case_ids == ["case-0"]
    assert report.usage.measured_tokens == 7
    assert worker.run_once() is None
    assert calls == ["case-0", "closed"]


def test_corrupt_archive_fails_without_provider_work(generation, tmp_path) -> None:
    """Archived generation validation fails before a worker can allocate providers."""
    import sqlite3

    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="corrupt", principal=principal())
    with sqlite3.connect(service.store.path) as connection:
        connection.execute("UPDATE evaluation_job_archives SET archive=?", (b"altered",))
    calls: list[str] = []
    result = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls)).run_once()
    assert result is not None and result.state == "failed"
    assert result.failure_code == "archive_invalid"
    assert calls == []
    assert service.result(accepted.job_id, principal=principal()).report.unfinished_case_ids == [
        "case-0"
    ]


def test_actual_worker_process_interruption_retains_unknown_provider_outcome(
    generation, tmp_path
) -> None:
    """A killed process releases worker ownership; recovery never replays its uncertain call."""
    import json
    import os
    import subprocess
    import sys
    import time

    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="process", principal=principal())
    ready = tmp_path / "provider-started"
    settings_path = tmp_path / "worker-settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "eval_workspace_dir": str(service.settings.eval_workspace_dir),
                "beta_auth_db_path": str(service.settings.beta_auth_db_path),
                "generations_dir": str(service.settings.generations_dir),
                "nvidia_embed_model": service.settings.nvidia_embed_model,
                "nvidia_base_url": service.settings.nvidia_base_url,
            }
        )
    )
    script = """
import json, sys, time
from pathlib import Path
from policynim.settings import Settings
from policynim.operations import current_budget
from policynim.services.evaluation_jobs import EvaluationJobService, EvaluationJobWorker
class Evaluator:
    def __init__(self, settings):
        "Keep the subprocess fixture free of provider clients."
    def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
        "Persist an attempt, then hold the process at its simulated provider boundary."
        current_budget().reserve_provider_call()
        Path(sys.argv[2]).write_text("started")
        time.sleep(60)
    def close(self):
        "The isolated fixture owns no additional resource."
settings = Settings(**json.loads(Path(sys.argv[1]).read_text()), _env_file=None)
EvaluationJobWorker(EvaluationJobService(settings), evaluator_factory=Evaluator).run_once()
"""
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(settings_path), str(ready)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                pytest.fail("isolated worker didn't reach its provider boundary")
            time.sleep(0.01)
        assert service.inspect(accepted.job_id, principal=principal()).state == "running"
    finally:
        process.kill()
        process.communicate(timeout=5)
    calls: list[str] = []
    replacement = EvaluationJobWorker(
        EvaluationJobService(service.settings), evaluator_factory=evaluator_factory(calls)
    )
    assert replacement.run_once() is None
    recovered = service.inspect(accepted.job_id, principal=principal())
    assert recovered.state == "interrupted"
    report = service.result(accepted.job_id, principal=principal()).report
    assert report.uncertain_case_id == "case-0"
    assert report.usage.provider_attempts == 1
    assert report.usage.unavailable_usage_attempts == 1
    assert report.usage.usage_status == "unavailable"
    assert calls == []


def test_actual_nvidia_retry_boundary_respects_the_call_budget() -> None:
    """The actual SDK can't retry after its accepted attempt budget is consumed."""
    import httpx
    from test_provider_recovery import invoke

    from policynim.errors import ProviderError
    from policynim.operations import OperationBudget, operation_scope

    calls: list[str] = []
    recorded: list[dict[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Return retryable failure at the actual wire boundary."""
        calls.append(request.url.path)
        return httpx.Response(503, json={"error": {"message": "fixture busy"}})

    budget = OperationBudget(5, max_provider_calls=1, usage_callback=recorded.append)
    with pytest.raises(ProviderError) as failure:
        with operation_scope(budget):
            invoke("query", handler)
    assert failure.value.failure_class == "budget_exhausted"
    assert len(calls) == 1
    assert recorded[-1] == {
        "provider_attempts": 1,
        "measured_tokens": 0,
        "unavailable_usage_attempts": 1,
    }


def test_invalid_snapshot_is_quarantined_without_blocking_other_jobs(
    generation, tmp_path: Path
) -> None:
    """A damaged interrupted job can't wedge recovery or yield a fabricated report."""
    import sqlite3

    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    damaged = service.submit(suite, idempotency_key="damaged", principal=principal())
    intact = service.submit(suite, idempotency_key="intact", principal=principal())
    with sqlite3.connect(service.store.path) as connection:
        connection.execute(
            "UPDATE evaluation_jobs SET state='running',snapshot_json='{}' WHERE job_id=?",
            (damaged.job_id,),
        )
    calls: list[str] = []
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))
    try:
        completed = worker.run_once()
        assert completed is not None and completed.job_id == intact.job_id
        assert completed.state == "completed"
        failed = service.inspect(damaged.job_id, principal=principal())
        assert failed.state == "failed" and failed.failure_code == "job_artifact_invalid"
        assert failed.report_sha256 is None
        with pytest.raises(EvaluationJobError, match="report_unavailable"):
            service.result(damaged.job_id, principal=principal())
        assert worker.run_once() is None
    finally:
        worker.close()
    assert calls == ["case-0", "closed"]


def test_operator_backlog_read_doesnt_create_storage_or_expose_callers(
    generation, tmp_path: Path
) -> None:
    """Operator counts reflect durable state while absent storage stays absent."""
    from policynim.storage.evaluation_jobs import inspect_job_backlog

    absent = tmp_path / "absent" / "jobs.sqlite3"
    assert inspect_job_backlog(absent) is None and not absent.parent.exists()
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="operator", principal=principal())
    assert inspect_job_backlog(service.store.path) == {"queued": 1}
    service.cancel(accepted.job_id, principal=principal())
    assert inspect_job_backlog(service.store.path) == {"cancelled": 1}


def test_busy_worker_prunes_expired_payloads_before_queue_becomes_empty(
    generation, tmp_path: Path, monkeypatch
) -> None:
    """Retention cleanup still runs while another accepted job's queued."""
    import sqlite3
    from threading import Event

    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    expired = service.submit(suite, idempotency_key="expired", principal=principal())
    service.cancel(expired.job_id, principal=principal())
    with sqlite3.connect(service.store.path) as db:
        db.execute("UPDATE evaluation_jobs SET expires_at=0 WHERE job_id=?", (expired.job_id,))
    service.submit(suite, idempotency_key="first", principal=principal())
    waiting = service.submit(suite, idempotency_key="second", principal=principal())
    calls: list[str] = []
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))
    purge = service.store.purge_expired
    cleaned = Event()

    def stop_after_cleanup() -> int:
        """Stop after the first real cleanup while retaining a second queued job."""
        count = purge()
        worker._stop.set()
        cleaned.set()
        return count

    monkeypatch.setattr(service.store, "purge_expired", stop_after_cleanup)
    worker.start()
    try:
        assert cleaned.wait(5)
    finally:
        worker.close()
    with sqlite3.connect(service.store.path) as db:
        snapshot = db.execute(
            "SELECT snapshot_json FROM evaluation_jobs WHERE job_id=?", (expired.job_id,)
        ).fetchone()[0]
    assert snapshot is None
    assert service.inspect(waiting.job_id, principal=principal()).state == "queued"


@pytest.mark.parametrize(
    "invalid", ["blank-input", "blank-domain", "revision", "policy", "chunk", "case-id"]
)
def test_submission_rejects_invalid_case_before_acceptance_or_quota(
    generation, tmp_path: Path, invalid: str
) -> None:
    """Invalid case contracts and absent evidence can't enter an admitted worker job."""
    from pydantic import ValidationError

    from policynim.errors import PolicyNIMError

    service, suite, _ = job_setup(generation, tmp_path)
    updates = {
        "case-id": {"case_id": "x" * 257},
        "blank-input": {"input": "   "},
        "blank-domain": {"domain": " "},
        "policy": {
            "expected_policy_ids": ["missing-policy"],
            "expected_insufficient_context": False,
        },
        "chunk": {"expected_chunk_ids": ["missing-chunk"], "expected_insufficient_context": False},
    }
    if invalid == "revision":
        suite = suite.model_copy(update={"expected_policy_revisions": {"A": "sha256:" + "0" * 64}})
    else:
        suite = suite.model_copy(
            update={"cases": [suite.cases[0].model_copy(update=updates[invalid])]}
        )
    with pytest.raises((PolicyNIMError, ValidationError)):
        service.submit(suite, idempotency_key="invalid", principal=principal())
    assert service.store.aggregate_counts() == {}
    assert not service.settings.beta_auth_db_path.exists()


def test_concurrent_schema_upgrade_keeps_accepted_jobs(generation, tmp_path: Path) -> None:
    """Competing process-style constructors serialize migration against the same durable row."""
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="before-upgrade", principal=principal())
    with sqlite3.connect(service.store.path) as db:
        db.execute("ALTER TABLE evaluation_jobs DROP COLUMN active_case_start_attempts")
        db.execute("PRAGMA user_version=1")
    with ThreadPoolExecutor(max_workers=6) as pool:
        stores = list(pool.map(lambda _: EvaluationJobStore(service.store.path), range(6)))
    for store in stores:
        assert store.inspect(accepted.job_id, accepted.owner_id).job_id == accepted.job_id
    with sqlite3.connect(service.store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        names = [row[1] for row in db.execute("PRAGMA table_info(evaluation_jobs)")]
    assert names.count("active_case_start_attempts") == 1


def test_concurrent_submissions_deduplicate_and_keep_owner_queue_bound(
    generation, tmp_path: Path
) -> None:
    """Parallel callers share one accepted key and can't race past the durable owner limit."""
    from concurrent.futures import ThreadPoolExecutor

    service, suite, _ = job_setup(generation, tmp_path)

    def submit(key: str):
        """Capture only a typed accepted handle or its expected saturation code."""
        try:
            return service.submit(suite, idempotency_key=key, principal=principal())
        except EvaluationJobError as error:
            assert error.code in {"queue_full", "principal_busy"}
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        repeated = list(pool.map(submit, ["same-key"] * 6))
    assert any(item is not None for item in repeated)
    repeated = [submit("same-key") for _ in repeated]
    assert len({item.job_id for item in repeated if item is not None}) == 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        bounded = list(pool.map(submit, [f"unique-{index}" for index in range(6)]))
    assert len([item for item in bounded if item is not None]) == 1
    assert service.store.aggregate_counts() == {"queued": 2}
    assert not service.settings.beta_auth_db_path.exists()


def test_archive_capture_is_bounded_per_owner_before_global_worker_work(
    generation, tmp_path: Path, monkeypatch
) -> None:
    """A blocked capture rejects that owner's overlap while another principal can submit."""
    from threading import Event, Thread, current_thread

    from policynim.services.generations import GenerationStore

    service, suite, _ = job_setup(generation, tmp_path)
    entered, release = Event(), Event()
    exported = GenerationStore.export_bundle

    def blocked_capture(self, *args, **kwargs):
        """Hold only the first owner's real export at its public archive boundary."""
        if current_thread().name == "capturing-owner":
            entered.set()
            assert release.wait(5)
        return exported(self, *args, **kwargs)

    monkeypatch.setattr(GenerationStore, "export_bundle", blocked_capture)
    accepted: list = []
    thread = Thread(
        name="capturing-owner",
        target=lambda: accepted.append(
            service.submit(suite, idempotency_key="first", principal=principal())
        ),
    )
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(EvaluationJobError, match="principal_busy"):
            service.submit(suite, idempotency_key="overlap", principal=principal())
        another = service.submit(suite, idempotency_key="other", principal=principal("other"))
        assert another.state == "queued"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and len(accepted) == 1
    assert service.submit(suite, idempotency_key="first", principal=principal()) == accepted[0]
    assert not service.settings.beta_auth_db_path.exists()


def test_job_execution_mints_fresh_correlation_for_admission_and_provider(
    generation, tmp_path: Path, caplog
) -> None:
    """A recovered worker context doesn't reuse its submitting caller's correlation identity."""
    import json
    import logging
    import sqlite3

    from policynim import operations
    from policynim.operations import current_correlation_id, operation_correlation
    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    observed: list[str | None] = []
    base = evaluator_factory([])

    class Evaluator(base):
        """Observe only the public execution context at the provider scoring seam."""

        def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
            """Keep evaluator behavior identical while recording its opaque context."""
            observed.append(current_correlation_id())
            return super().evaluate_case(suite, case_id, rerank_enabled=rerank_enabled)

    operations._LOG.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO), operation_correlation() as submitting:
            accepted = service.submit(
                suite, idempotency_key="fresh-correlation", principal=principal()
            )
            result = EvaluationJobWorker(service, evaluator_factory=Evaluator).run_once()
            assert result is not None and result.state == "completed"
            assert current_correlation_id() == submitting
    finally:
        operations._LOG.removeHandler(caplog.handler)
    assert len(observed) == 1 and observed[0] is not None and observed[0] != submitting
    with sqlite3.connect(service.settings.beta_auth_db_path) as db:
        persisted = db.execute("SELECT correlation_id FROM operation_usage").fetchone()[0]
    assert persisted == observed[0]
    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.getMessage().startswith("{")
    ]
    event = next(item for item in events if item["event"] == "evaluation.job")
    assert event == {
        "event": "evaluation.job",
        "correlation_id": persisted,
        "job_id": accepted.job_id,
        "phase": "admission",
    }


def test_worker_pauses_queue_after_quota_accounting_failure(
    generation, tmp_path: Path, monkeypatch
) -> None:
    """A new per-job admission service can't bypass this process's accounting pause."""
    import sqlite3

    from policynim.services.evaluation_jobs import EvaluationJobWorker
    from policynim.storage.operation_store import OperationStore

    service, suite, _ = job_setup(generation, tmp_path)
    service.submit(suite, idempotency_key="uncertain-accounting", principal=principal())
    queued = service.submit(suite, idempotency_key="wait-for-recovery", principal=principal())
    calls: list[str] = []
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))

    def fail_accounting(*args, **kwargs):
        """Leave an accepted quota row uncertain after the provider's completed work."""
        raise sqlite3.OperationalError("fixture terminal accounting lock")

    with monkeypatch.context() as fault:
        fault.setattr(OperationStore, "finish", fail_accounting)
        failed = worker.run_once()
    assert failed is not None and failed.failure_code == "accounting_incomplete"
    with pytest.raises(EvaluationJobError, match="accounting_incomplete"):
        worker.run_once()
    with pytest.raises(EvaluationJobError, match="accounting_incomplete"):
        worker.start()
    assert service.inspect(queued.job_id, principal=principal()).state == "queued"
    assert calls == ["case-0", "closed"]
    worker.close()


@pytest.mark.parametrize("code", ["server_busy", "principal_busy", "admission_busy"])
def test_transient_pre_provider_admission_retries_without_duplicate_charge(
    generation, tmp_path: Path, monkeypatch, code: str
) -> None:
    """Accepted work waits durably for capacity; no provider attempt or quota charge repeats."""
    import sqlite3

    from policynim.services.evaluation_jobs import EvaluationJobWorker
    from policynim.services.operation_admission import (
        OperationAdmissionError,
        OperationAdmissionService,
    )

    service, suite, _ = job_setup(generation, tmp_path)
    now = [service.store.clock()]
    monkeypatch.setattr(service.store, "clock", lambda: now[0])
    accepted = service.submit(suite, idempotency_key="retry-capacity", principal=principal())
    calls: list[str] = []
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))

    def reject(*args, **kwargs):
        """Inject only an admission outcome before the evaluator closure can run."""
        raise OperationAdmissionError(code + ": Retry shortly.")

    with monkeypatch.context() as busy:
        busy.setattr(OperationAdmissionService, "run", reject)
        deferred = worker.run_once()
    assert deferred is not None and deferred.state == "queued" and deferred.failure_code == code
    assert deferred.next_attempt_at == now[0] + 1
    assert deferred.deadline_at == accepted.deadline_at
    assert calls == [] and worker.run_once() is None
    with sqlite3.connect(service.settings.beta_auth_db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM operation_usage").fetchone()[0] == 0
    now[0] += 2
    completed = worker.run_once()
    assert completed is not None and completed.state == "completed"
    assert completed.failure_code is None
    assert calls == ["case-0", "closed"]
    report = service.result(accepted.job_id, principal=principal()).report
    assert report.usage.provider_attempts == 1 and report.uncertain_case_id is None
    with sqlite3.connect(service.settings.beta_auth_db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM operation_usage").fetchone()[0] == 1
    worker.close()


def test_storage_cant_defer_a_started_case(generation, tmp_path: Path) -> None:
    """The storage transition itself forbids replay after a case was marked active."""
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="no-replay", principal=principal())
    claimed = service.store.claim("test-worker")
    assert claimed is not None
    service.store.begin_case(accepted.job_id, "test-worker", suite.cases[0].case_id)
    with pytest.raises(EvaluationJobError, match="unsafe_job_retry"):
        service.store.defer_admission(accepted.job_id, "test-worker", "admission_busy")
    assert service.inspect(accepted.job_id, principal=principal()).state == "running"


def test_real_principal_capacity_defers_accepted_job_until_owner_releases(
    generation, tmp_path: Path, monkeypatch
) -> None:
    """Actual quota storage and kernel ownership keep a busy principal's job queued safely."""
    from policynim.services.evaluation_jobs import EvaluationJobWorker
    from policynim.services.operation_admission import (
        OperationAdmissionService,
        admit_operation,
        principal_identity,
    )

    service, suite, _ = job_setup(generation, tmp_path)
    service.settings.mcp_max_concurrent_operations_per_principal = 1
    now = [service.store.clock()]
    monkeypatch.setattr(service.store, "clock", lambda: now[0])
    caller = principal()
    held = OperationAdmissionService(service.settings)
    admitted = admit_operation(
        held.store,
        principal_id=principal_identity(caller.issuer, caller.subject),
        operation="policy_search",
        daily_limit=500,
        principal_limit=1,
        global_limit=10,
    )
    assert admitted.operation_id is not None
    accepted = service.submit(suite, idempotency_key="actual-capacity", principal=caller)
    calls: list[str] = []
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))
    try:
        waiting = worker.run_once()
        assert waiting is not None and waiting.state == "queued"
        assert waiting.failure_code == "principal_busy" and calls == []
        held.store.finish(admitted.operation_id, "completed")
        now[0] += 2
        finished = worker.run_once()
        assert finished is not None and finished.state == "completed"
        assert service.result(accepted.job_id, principal=caller).report.usage.provider_attempts == 1
        assert calls == ["case-0", "closed"]
    finally:
        worker.close()
        held.store.close()


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_terminal_report_commit_fault_preserves_cases_and_restart_outcome(
    generation, tmp_path: Path, phase: str
) -> None:
    """A terminal COMMIT fault keeps saved cases and never replays their provider work."""
    import sqlite3

    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, generation_id = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="terminal-fault", principal=principal())
    claimed = service.store.claim("fixture-worker")
    assert claimed is not None and claimed.job_id == accepted.job_id
    service.store.begin_case(accepted.job_id, "fixture-worker", "case-0")
    service.store.save_case(
        accepted.job_id,
        "fixture-worker",
        case_result(
            service.settings.model_copy(update={"generation_id": generation_id}), suite, "case-0"
        ),
    )

    def interrupt(observed: str) -> None:
        """Fail at the actual SQLite commit boundary after report construction."""
        if observed == phase:
            raise sqlite3.OperationalError("fixture terminal commit interruption")

    service.store.fault = interrupt
    with pytest.raises(sqlite3.OperationalError, match="terminal commit"):
        service.store.finish(accepted.job_id, "fixture-worker", "completed")
    service.store.fault = None
    stored = service.inspect(accepted.job_id, principal=principal())
    assert stored.completed_case_count == 1
    assert stored.state == ("running" if phase == "before_commit" else "completed")
    calls: list[str] = []
    restarted = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))
    assert restarted.run_once() is None
    report = service.result(accepted.job_id, principal=principal()).report
    assert report.state == ("interrupted" if phase == "before_commit" else "completed")
    assert [case.case_id for case in report.completed_cases] == ["case-0"]
    assert report.unfinished_case_ids == [] and report.uncertain_case_id is None
    assert calls == []


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_retention_commit_fault_keeps_owner_payloads_atomic(
    generation, tmp_path: Path, phase: str
) -> None:
    """Retention atomically removes expired payloads while preserving another owner."""
    import sqlite3

    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, _ = job_setup(generation, tmp_path)
    now = [1000.0]
    service.store.clock = lambda: now[0]
    expired = service.submit(
        suite,
        idempotency_key="short",
        principal=principal(),
        limits=EvaluationJobLimits(retention_seconds=60),
    )
    retained = service.submit(
        suite,
        idempotency_key="long",
        principal=principal("other"),
        limits=EvaluationJobLimits(retention_seconds=120),
    )
    calls: list[str] = []
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))
    first = worker.run_once()
    second = worker.run_once()
    assert first is not None and first.state == "completed"
    assert second is not None and second.state == "completed"
    retained_report = service.result(retained.job_id, principal=principal("other"))
    original_digest = service.inspect(expired.job_id, principal=principal()).report_sha256
    now[0] += 61

    def interrupt(observed: str) -> None:
        """Fail before or after the transaction's actual durable COMMIT."""
        if observed == phase:
            raise sqlite3.OperationalError("fixture retention interruption")

    service.store.fault = interrupt
    with pytest.raises(sqlite3.OperationalError, match="retention interruption"):
        service.store.purge_expired()
    service.store.fault = None
    with sqlite3.connect(service.store.path) as connection:
        row = connection.execute(
            "SELECT snapshot_json,report_json,result_expired FROM evaluation_jobs WHERE job_id=?",
            (expired.job_id,),
        ).fetchone()
        case_count = connection.execute(
            "SELECT COUNT(*) FROM evaluation_job_cases WHERE job_id=?", (expired.job_id,)
        ).fetchone()[0]
        assert (row[0] is None, row[1] is None, row[2], case_count) == (
            (False, False, 0, 1) if phase == "before_commit" else (True, True, 1, 0)
        )
        assert connection.execute("SELECT COUNT(*) FROM evaluation_job_archives").fetchone()[0] == 1
    assert service.result(retained.job_id, principal=principal("other")) == retained_report
    assert service.store.purge_expired() == (1 if phase == "before_commit" else 0)
    assert service.inspect(expired.job_id, principal=principal()).report_sha256 == original_digest
    now[0] += 60
    assert service.store.purge_expired() == 1
    with sqlite3.connect(service.store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evaluation_job_archives").fetchone()[0] == 0
    assert calls == ["case-0", "closed", "case-0", "closed"]


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_case_commit_fault_recovers_only_unconfirmed_provider_result(
    generation, tmp_path: Path, phase: str
) -> None:
    """Case COMMIT recovery separates an unsaved response from a fully saved case."""
    import sqlite3

    from policynim.job_types import EvaluationJobUsage
    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, generation_id = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="case-commit", principal=principal())
    assert service.store.claim("fixture-worker") is not None
    service.store.begin_case(accepted.job_id, "fixture-worker", "case-0")
    service.store.save_usage(
        accepted.job_id,
        "fixture-worker",
        EvaluationJobUsage(provider_attempts=1, measured_tokens=7, usage_status="measured"),
    )

    def interrupt(observed: str) -> None:
        """Interrupt the actual result transaction on either side of its COMMIT."""
        if observed == phase:
            raise sqlite3.OperationalError("fixture case commit interruption")

    service.store.fault = interrupt
    with pytest.raises(sqlite3.OperationalError, match="case commit"):
        service.store.save_case(
            accepted.job_id,
            "fixture-worker",
            case_result(
                service.settings.model_copy(update={"generation_id": generation_id}),
                suite,
                "case-0",
            ),
        )
    service.store.fault = None
    calls: list[str] = []
    assert (
        EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls)).run_once() is None
    )
    report = service.result(accepted.job_id, principal=principal()).report
    assert report.state == "interrupted" and report.usage.measured_tokens == 7
    assert report.uncertain_case_id == ("case-0" if phase == "before_commit" else None)
    assert [case.case_id for case in report.completed_cases] == (
        [] if phase == "before_commit" else ["case-0"]
    )
    assert calls == []


def test_delayed_quarantine_retains_damaged_inputs_for_the_accepted_retention_window(
    generation, tmp_path: Path
) -> None:
    """Recovery after long downtime leaves damaged artifacts available for bounded inspection."""
    import sqlite3

    from policynim.services.evaluation_jobs import EvaluationJobWorker

    service, suite, generation_id = job_setup(generation, tmp_path)
    now = [1000.0]
    service.store.clock = lambda: now[0]
    accepted = service.submit(
        suite,
        idempotency_key="late-quarantine",
        principal=principal(),
        limits=EvaluationJobLimits(deadline_seconds=1, retention_seconds=60),
    )
    assert service.store.claim("crashed-worker") is not None
    service.store.begin_case(accepted.job_id, "crashed-worker", "case-0")
    service.store.save_case(
        accepted.job_id,
        "crashed-worker",
        case_result(
            service.settings.model_copy(update={"generation_id": generation_id}), suite, "case-0"
        ),
    )
    with sqlite3.connect(service.store.path) as connection:
        connection.execute(
            "UPDATE evaluation_jobs SET snapshot_json='{}' WHERE job_id=?", (accepted.job_id,)
        )
    now[0] = accepted.expires_at + 100
    calls: list[str] = []
    assert (
        EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls)).run_once() is None
    )
    failed = service.inspect(accepted.job_id, principal=principal())
    assert failed.failure_code == "job_artifact_invalid" and not failed.result_expired
    assert failed.expires_at == now[0] + 60
    assert service.store.purge_expired() == 0
    with sqlite3.connect(service.store.path) as connection:
        assert (
            connection.execute(
                "SELECT snapshot_json FROM evaluation_jobs WHERE job_id=?", (accepted.job_id,)
            ).fetchone()[0]
            == "{}"
        )
        assert connection.execute("SELECT COUNT(*) FROM evaluation_job_cases").fetchone()[0] == 1
    with pytest.raises(EvaluationJobError, match="report_unavailable"):
        service.result(accepted.job_id, principal=principal())
    now[0] += 61
    assert service.store.purge_expired() == 1
    assert service.inspect(accepted.job_id, principal=principal()).result_expired
    assert calls == []
