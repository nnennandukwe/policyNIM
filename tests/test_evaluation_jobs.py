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
        pass
    def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
        current_budget().reserve_provider_call()
        Path(sys.argv[2]).write_text("started")
        time.sleep(60)
    def close(self):
        pass
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
