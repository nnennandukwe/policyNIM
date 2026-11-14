"""Public job cancellation, deadline, and shutdown evidence across owned cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import TextResourceContents
from test_evaluation_jobs import case_result, evaluator_factory, job_setup, principal
from test_grounded_mcp import generation as generation_fixture
from typer.testing import CliRunner

from policynim.errors import ProviderError
from policynim.interfaces import evaluation_jobs_cli as cli_module
from policynim.interfaces import evaluation_jobs_mcp as mcp_jobs
from policynim.interfaces import mcp as mcp_module
from policynim.interfaces.cli import app
from policynim.job_types import EvaluationJobInfo, EvaluationJobReportManifest
from policynim.operations import current_budget
from policynim.providers.lifecycle import NVIDIAClients, shared_client
from policynim.services import evaluation_jobs as service_module
from policynim.services.evaluation_jobs import EvaluationJobWorker
from policynim.storage.file_lock import try_exclusive_file_lock
from policynim.storage.operation_store import OperationStore

generation = generation_fixture

_WORKER_PROCESS = """
import json, os, sys, time
from pathlib import Path
from test_evaluation_jobs import case_result
from policynim.interfaces import evaluation_jobs_cli as cli_jobs
from policynim.interfaces.cli import main
from policynim.operations import current_budget
from policynim.services.evaluation_jobs import EvaluationJobService, EvaluationJobWorker
from policynim.settings import Settings

settings_path, evidence_path, boundary, phase = sys.argv[1:]
evidence = Path(evidence_path)
settings = Settings(**json.loads(Path(settings_path).read_text()), _env_file=None)
service = EvaluationJobService(settings)

def append(value):
    "Persist fixture effects independently of job result persistence."
    with evidence.open("a") as output:
        output.write(value + "\\n")

def interrupt_commit(observed):
    "Exit on the selected side of the actual SQLite COMMIT."
    if observed == phase.removeprefix("interrupt_"):
        if phase.startswith("interrupt_"):
            raise KeyboardInterrupt
        os._exit(41)

def wrap_commit(name):
    "Arm the transaction fault only inside the selected persistence operation."
    original = getattr(service.store, name)
    def wrapped(*args, **kwargs):
        "Keep the real storage operation and its commit boundary."
        service.store.fault = interrupt_commit
        return original(*args, **kwargs)
    setattr(service.store, name, wrapped)

if boundary == "activation_interrupt":
    from policynim.services.operation_admission import OperationAdmissionService
    retained_admissions = []
    def interrupt_before_execution(self, *args, **kwargs):
        "Interrupt after activation commits and before its execution finally starts."
        retained_admissions.append(self)
        append("activation_committed")
        raise KeyboardInterrupt
    OperationAdmissionService._execute = interrupt_before_execution
elif boundary == "completion_interrupt":
    from policynim.storage.operation_store import OperationStore
    original_finish = OperationStore.finish
    def interrupt_after_completion(self, operation_id, outcome):
        "Interrupt after the quota outcome commits, without changing its history."
        original_finish(self, operation_id, outcome)
        if outcome == "completed":
            append("completion_committed")
            raise KeyboardInterrupt
    OperationStore.finish = interrupt_after_completion
elif boundary != "signal":
    wrap_commit(boundary)

class Evaluator:
    "Replace only evaluation effects with independently recorded local evidence."
    def __init__(self, settings):
        "Keep the worker's restored generation for the typed result."
        self.settings = settings
    def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
        "Persist one reserved effect before any case result can commit."
        current_budget().reserve_provider_call()
        append(case_id)
        if boundary == "signal":
            time.sleep(60)
        return case_result(self.settings, suite, case_id)
    def close(self):
        "Record cleanup even when Ctrl-C unwinds the provider boundary."
        append("closed")
        if boundary == "signal" and phase == "cleanup_failure":
            raise RuntimeError("Fixture close failure.")

class Worker(EvaluationJobWorker):
    "Retain the public foreground worker command and real storage."
    def __init__(self, service):
        "Inject only the deterministic evaluator."
        super().__init__(service, evaluator_factory=Evaluator)

cli_jobs._service = lambda: service
cli_jobs.EvaluationJobWorker = Worker
sys.argv = ["policynim", "eval-jobs", "worker", "--once"]
main()
"""


def _worker_process(service, tmp_path, boundary, phase):
    """Start the actual CLI with fixture settings and no inherited provider credentials."""
    settings_path = tmp_path / "worker-settings.json"
    settings_path.write_text(service.settings.model_dump_json())
    evidence = tmp_path / "effects.txt"
    root = Path(__file__).resolve().parents[1]
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join([str(root / "src"), str(root / "tests")]),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", _WORKER_PROCESS, str(settings_path), str(evidence), boundary, phase],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process, evidence


@pytest.mark.parametrize("reason", ["cancelled", "deadline_exhausted", "interrupt"])
@pytest.mark.parametrize("cleanup_failure", ["evaluator", "clients", "both"])
def test_cli_worker_retains_stop_reason_through_cleanup_failure(
    generation, tmp_path, monkeypatch, caplog, reason, cleanup_failure
):
    """A close error mustn't replace a known stop reason or leave Ctrl-C work running."""
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key=reason, principal=principal())
    calls = []
    clients = []

    class Pool(NVIDIAClients):
        """Exercise the actual pool close after the evaluator's close hook."""

        def close(self):
            """Close all pooled HTTP clients before injecting a cleanup failure."""
            super().close()
            calls.append("pool-closed")
            if cleanup_failure in {"clients", "both"}:
                raise RuntimeError("Bearer private-fixture /Users/private/pool-close")

    class Evaluator:
        """Interrupt at the evaluator boundary after durably reserving an attempt."""

        def __init__(self, settings):
            """Borrow actual SDK and HTTP clients without sending any request."""
            settings.nvidia_api_key = "fixture"
            self.settings = settings
            clients.extend([shared_client(settings), shared_client(settings, retrieval=True)])

        def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
            """Persist the effect before the injected stop reaches worker cleanup."""
            current_budget().reserve_provider_call()
            calls.append(case_id)
            if reason == "interrupt":
                raise KeyboardInterrupt
            if reason == "cancelled":
                service.cancel(accepted.job_id, principal=principal())
            raise ProviderError("Fixture stop.", failure_class=reason)

        def close(self):
            """Fail only after the owned fixture resource has been released."""
            assert shared_client(self.settings) is clients[0]
            calls.append("closed")
            if cleanup_failure in {"evaluator", "both"}:
                raise RuntimeError("Bearer private-fixture /Users/private/evaluator-close")

    monkeypatch.setattr(service_module, "NVIDIAClients", Pool)
    monkeypatch.setattr(cli_module, "_service", lambda: service)
    monkeypatch.setattr(cli_module, "local_job_principal", principal)
    monkeypatch.setattr(
        cli_module,
        "EvaluationJobWorker",
        lambda value: EvaluationJobWorker(value, evaluator_factory=Evaluator),
    )
    worked = CliRunner().invoke(app, ["eval-jobs", "worker", "--once"])
    assert worked.exit_code == 0, worked.output
    inspected = CliRunner().invoke(app, ["eval-jobs", "inspect", accepted.job_id])
    state = json.loads(inspected.stdout)
    expected = {
        "cancelled": "cancelled",
        "deadline_exhausted": "deadline_exceeded",
        "interrupt": "interrupted",
    }[reason]
    assert state["state"] == expected
    assert state["failure_code"] == ("worker_stopped" if reason == "interrupt" else reason)
    result = CliRunner().invoke(app, ["eval-jobs", "result", accepted.job_id])
    report = json.loads(result.stdout)["report"]
    assert report["state"] == expected and report["uncertain_case_id"] == "case-0"
    assert report["usage"]["provider_attempts"] == 1
    assert calls == ["case-0", "closed", "pool-closed"]
    assert clients[0].is_closed() and clients[1].is_closed
    cleanup_events = [
        json.loads(record.message)
        for record in caplog.records
        if '"evaluation.job_cleanup_failure"' in record.message
    ]
    assert [event["resource_kind"] for event in cleanup_events] == (
        ["evaluator", "provider_clients"]
        if cleanup_failure == "both"
        else ["evaluator"]
        if cleanup_failure == "evaluator"
        else ["provider_clients"]
    )
    assert all(event["job_id"] == accepted.job_id for event in cleanup_events)
    assert "private-fixture" not in caplog.text and "/Users/private" not in caplog.text
    quota = OperationStore.report(service.settings.beta_auth_db_path)
    assert quota.active_operations == 0
    assert quota.outcomes == {
        "interrupted"
        if reason == "interrupt"
        else "cancelled"
        if reason == "cancelled"
        else "failed": 1
    }


def test_cli_worker_interrupt_finishes_before_restart(generation, tmp_path, monkeypatch):
    """Ctrl-C during foreground execution publishes interruption after owned cleanup."""
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="ctrl-c", principal=principal())
    calls = []

    class Evaluator:
        """Raise the same interrupt delivered to the foreground worker by Ctrl-C."""

        def __init__(self, settings):
            """Avoid constructing a live provider."""

        def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
            """Leave durable uncertain work for terminal interruption evidence."""
            current_budget().reserve_provider_call()
            raise KeyboardInterrupt

        def close(self):
            """Record the cleanup before terminal reporting."""
            calls.append("closed")

    monkeypatch.setattr(cli_module, "_service", lambda: service)
    monkeypatch.setattr(
        cli_module,
        "EvaluationJobWorker",
        lambda value: EvaluationJobWorker(value, evaluator_factory=Evaluator),
    )
    worked = CliRunner().invoke(app, ["eval-jobs", "worker", "--once"])
    assert worked.exit_code == 0, worked.output
    assert calls == ["closed"]
    assert service.inspect(accepted.job_id, principal=principal()).state == "interrupted"
    assert OperationStore.report(service.settings.beta_auth_db_path).outcomes == {"interrupted": 1}


def test_mcp_queued_cancel_retry_and_owner_denial(generation, tmp_path):
    """SDK tools retain one cancelled identity and deny every other-principal operation."""
    service, suite, generation_id = job_setup(generation, tmp_path)
    calls = []
    worker = EvaluationJobWorker(service, evaluator_factory=evaluator_factory(calls))
    server = MCPServer("queued lifecycle audit")
    caller = [principal()]
    runtime = mcp_module._ToolExecutor(settings=service.settings)

    async def run_tool(name, operation, ctx):
        """Keep real SDK validation, dispatch and service authorization in the path."""
        return await mcp_module._run_logged_tool(
            name, operation, ctx=ctx, executor=runtime, principal=caller[0], charge_usage=False
        )

    mcp_jobs.register_evaluation_jobs(
        server, get_principal=lambda _: caller[0], get_worker=lambda: worker, run_tool=run_tool
    )

    async def exercise():
        """Cancel under a competing worker lease, then repeat and inspect retained evidence."""
        async with Client(server) as client:
            submission = {"suite": suite.model_dump(mode="json"), "idempotency_key": "queued"}
            submitted = await client.call_tool("eval_submit", submission)
            assert not submitted.is_error
            accepted = EvaluationJobInfo.model_validate(submitted.structured_content)
            assert accepted.state == "queued"
            assert accepted.generation_id == generation_id
            assert (await client.call_tool("eval_result", {"job_id": accepted.job_id})).is_error
            caller[0] = principal("other")
            for operation in ("eval_inspect", "eval_cancel", "eval_result"):
                denied = await client.call_tool(operation, {"job_id": accepted.job_id})
                assert denied.is_error and "job_unavailable" in str(denied.content)
            caller[0] = principal()
            cancelled = await client.call_tool("eval_cancel", {"job_id": accepted.job_id})
            state = EvaluationJobInfo.model_validate(cancelled.structured_content)
            assert state.state == "cancelled"
            for _ in range(2):
                repeated = await client.call_tool("eval_submit", submission)
                assert EvaluationJobInfo.model_validate(repeated.structured_content) == state
                repeated = await client.call_tool("eval_cancel", {"job_id": state.job_id})
                assert EvaluationJobInfo.model_validate(repeated.structured_content) == state
            changed = suite.model_copy(update={"name": "changed"})
            conflict = await client.call_tool(
                "eval_submit", {**submission, "suite": changed.model_dump(mode="json")}
            )
            assert conflict.is_error and "idempotency_conflict" in str(conflict.content)
            output = await client.call_tool("eval_result", {"job_id": state.job_id})
            manifest = EvaluationJobReportManifest.model_validate(output.structured_content)
            fragments = []
            for uri in manifest.part_uris:
                resource = await client.read_resource(uri)
                assert isinstance(resource.contents[0], TextResourceContents)
                fragments.append(json.loads(resource.contents[0].text)["content"])
            raw = "".join(fragments)
            assert manifest.report_sha256 == "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
            report = json.loads(raw)
            assert report["snapshot"]["suite"] == suite.model_dump(mode="json")
            assert report["snapshot_digest"] == accepted.snapshot_digest
            assert report["unfinished_case_ids"] == ["case-0"]
            assert report["usage"]["provider_attempts"] == 0

    async def owned_exercise():
        """Close the tool executor on the event loop that served its requests."""
        try:
            await exercise()
        finally:
            await runtime.close()

    try:
        with try_exclusive_file_lock(worker.lock_path) as acquired:
            assert acquired
            asyncio.run(owned_exercise())
        assert worker.run_once() is None and calls == []
        quota = OperationStore.report(service.settings.beta_auth_db_path)
        assert quota.retained_rows == quota.reserved_work_units == 1
        assert quota.active_operations == 0 and quota.outcomes == {"cancelled": 1}
    finally:
        worker.close()


@pytest.mark.parametrize("reason", ["cancelled", "deadline_exceeded", "interrupted"])
def test_mcp_running_http_work_drains_before_terminal_state(
    generation, tmp_path, monkeypatch, reason
):
    """Cancellation, deadline and shutdown drain real local HTTP before releasing admission."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from policynim.providers.nvidia import NVIDIAEmbedder, OpenAI

    service, suite, _ = job_setup(generation, tmp_path, cases=2)
    started, release = Event(), Event()
    budgets, clients, calls = [], [], []

    class Handler(BaseHTTPRequestHandler):
        """Hold a deterministic embedding response until the public control operation arrives."""

        def do_POST(self):
            """Return measured usage after the caller has observed active work."""
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            started.set()
            assert release.wait(10)
            payload = json.dumps(
                {
                    "object": "list",
                    "model": "fixture",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "usage": {"prompt_tokens": 9, "total_tokens": 9},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            """Keep fixture request headers and bodies out of logs."""

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http_thread = Thread(target=http.serve_forever)
    http_thread.start()

    class Evaluator:
        """Use the real NVIDIA adapter with an injected loopback SDK client."""

        def __init__(self, settings):
            """Retain generation identity while directing the fixture's HTTP to loopback."""
            self.settings = settings
            client = OpenAI(
                api_key="fixture",
                base_url=f"http://127.0.0.1:{http.server_port}/v1",
                max_retries=0,
            )
            clients.append(client)
            self.embedder = NVIDIAEmbedder(
                api_key="fixture",
                model=settings.nvidia_embed_model,
                base_url=settings.nvidia_base_url,
                batch_size=1,
                timeout_seconds=10,
                client=client,
                max_retries=0,
            )

        def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
            """Cross the real dispatch and response budget checks with durable usage."""
            budgets.append(current_budget())
            self.embedder.embed_query("local fixture query")
            return case_result(self.settings, suite, case_id)

        def close(self):
            """Release the injected client before surfacing a secondary cleanup fault."""
            self.embedder.close()
            clients[0].close()
            calls.append("closed")
            raise RuntimeError("Fixture close failure after HTTP drain.")

    worker = EvaluationJobWorker(service, evaluator_factory=Evaluator)
    server = MCPServer("running lifecycle audit")
    runtime = mcp_module._ToolExecutor(settings=service.settings)

    async def run_tool(name, operation, ctx):
        """Dispatch ordinary public tools without replacing durable job behavior."""
        return await mcp_module._run_logged_tool(
            name, operation, ctx=ctx, executor=runtime, principal=principal(), charge_usage=False
        )

    mcp_jobs.register_evaluation_jobs(
        server, get_principal=lambda _: principal(), get_worker=lambda: worker, run_tool=run_tool
    )

    async def exercise():
        """Observe requested control while the local HTTP request still owns capacity."""
        async with Client(server) as client:
            submitted = await client.call_tool(
                "eval_submit",
                {
                    "suite": suite.model_dump(mode="json"),
                    "idempotency_key": "running",
                },
            )
            accepted = EvaluationJobInfo.model_validate(submitted.structured_content)
            assert await asyncio.to_thread(started.wait, 5)
            shutdown = None
            if reason == "cancelled":
                cancelled = await client.call_tool("eval_cancel", {"job_id": accepted.job_id})
                info = EvaluationJobInfo.model_validate(cancelled.structured_content)
                assert info.state == "running" and info.cancel_requested
            elif reason == "deadline_exceeded":
                budgets[0].clock = lambda: budgets[0].deadline + 1
            else:
                shutdown = asyncio.create_task(asyncio.to_thread(worker.close))
                assert await asyncio.to_thread(worker._stop.wait, 5)
            assert not clients[0].is_closed()
            assert OperationStore.report(service.settings.beta_auth_db_path).active_operations == 1
            pending = await client.call_tool("eval_result", {"job_id": accepted.job_id})
            assert pending.is_error and "result_pending" in str(pending.content)
            release.set()
            if shutdown is not None:
                await asyncio.wait_for(shutdown, 5)
            async with asyncio.timeout(5):
                while True:
                    inspected = await client.call_tool("eval_inspect", {"job_id": accepted.job_id})
                    if (
                        EvaluationJobInfo.model_validate(inspected.structured_content).state
                        == reason
                    ):
                        break
                    await asyncio.sleep(0.01)
            report = service.result(accepted.job_id, principal=principal()).report
            assert report.state == reason and report.uncertain_case_id == "case-0"
            assert report.completed_cases == []
            assert report.unfinished_case_ids == ["case-0", "case-1"]
            assert report.usage.provider_attempts == 1 and report.usage.measured_tokens == 9

    async def owned_exercise():
        """Drain the request executor before leaving its event loop."""
        try:
            await exercise()
        finally:
            await runtime.close()

    try:
        asyncio.run(owned_exercise())
        assert calls == ["/v1/embeddings", "closed"] and clients[0].is_closed()
        quota = OperationStore.report(service.settings.beta_auth_db_path)
        assert quota.active_operations == 0
        assert quota.outcomes == {"failed" if reason == "deadline_exceeded" else reason: 1}
    finally:
        release.set()
        worker.close()
        http.shutdown()
        http.server_close()
        http_thread.join(3)


@pytest.mark.parametrize("boundary", ["claim", "begin_case", "save_case", "finish"])
@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_cli_process_exit_at_job_commits_never_replays_work(
    generation, tmp_path, monkeypatch, boundary, phase
):
    """Public CLI recovery consumes committed evidence after real process exits at each stage."""
    service, suite, generation_id = job_setup(generation, tmp_path)
    monkeypatch.setattr(cli_module, "_service", lambda: service)
    monkeypatch.setattr(cli_module, "local_job_principal", principal)
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(suite.model_dump_json())
    runner = CliRunner()
    submission = ["eval-jobs", "submit", str(suite_path), "--idempotency-key", "commit-audit"]
    submitted = runner.invoke(app, submission)
    assert submitted.exit_code == 0, submitted.output
    accepted = EvaluationJobInfo.model_validate_json(submitted.stdout)
    process, evidence = _worker_process(service, tmp_path, boundary, phase)
    try:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 41, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    calls = []
    monkeypatch.setattr(
        cli_module,
        "EvaluationJobWorker",
        lambda value: EvaluationJobWorker(value, evaluator_factory=evaluator_factory(calls)),
    )
    recovered = runner.invoke(app, ["eval-jobs", "worker", "--once"])
    assert recovered.exit_code == 0, recovered.output
    unclaimed = boundary == "claim" and phase == "before_commit"
    committed_report = boundary == "finish" and phase == "after_commit"
    expected_state = "completed" if unclaimed or committed_report else "interrupted"
    inspected = runner.invoke(app, ["eval-jobs", "inspect", accepted.job_id])
    info = EvaluationJobInfo.model_validate_json(inspected.stdout)
    assert info.state == expected_state and info.generation_id == generation_id
    repeated = runner.invoke(app, submission)
    assert EvaluationJobInfo.model_validate_json(repeated.stdout) == info
    result = runner.invoke(app, ["eval-jobs", "result", accepted.job_id])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)["report"]
    saved_case = (
        unclaimed
        or committed_report
        or boundary == "finish"
        or (boundary == "save_case" and phase == "after_commit")
    )
    assert [case["case_id"] for case in report["completed_cases"]] == (
        ["case-0"] if saved_case else []
    )
    assert report["uncertain_case_id"] == (
        "case-0" if boundary == "save_case" and phase == "before_commit" else None
    )
    assert report["snapshot_digest"] == accepted.snapshot_digest
    assert calls == (["case-0", "closed"] if unclaimed else [])
    effects = evidence.read_text().splitlines() if evidence.exists() else []
    assert effects.count("case-0") == (1 if boundary in {"save_case", "finish"} else 0)
    assert (
        runner.invoke(app, ["eval-jobs", "worker", "--once"]).stdout.strip() == '{"state":"idle"}'
    )
    quota = OperationStore.report(service.settings.beta_auth_db_path)
    assert quota.retained_rows == quota.reserved_work_units == 1 and quota.active_operations == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT delivery to a foreground CLI process")
@pytest.mark.parametrize("cleanup", ["clean", "cleanup_failure"])
def test_foreground_cli_sigint_drains_and_records_interruption(generation, tmp_path, cleanup):
    """An actual OS interrupt retains one uncertain attempt and an interrupted quota outcome."""
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key="sigint", principal=principal())
    process, evidence = _worker_process(service, tmp_path, "signal", cleanup)
    try:
        deadline = time.monotonic() + 10
        while not evidence.exists():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)
        assert json.loads(stdout)["state"] == "interrupted"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert evidence.read_text().splitlines() == ["case-0", "closed"]
    report = service.result(accepted.job_id, principal=principal()).report
    assert report.state == "interrupted" and report.failure_code == "worker_stopped"
    assert report.uncertain_case_id == "case-0" and report.usage.provider_attempts == 1
    quota = OperationStore.report(service.settings.beta_auth_db_path)
    assert quota.active_operations == 0 and quota.outcomes == {"interrupted": 1}


@pytest.mark.parametrize("boundary", ["activation_interrupt", "completion_interrupt"])
def test_cli_interrupt_outside_owned_execution_requires_recovery_without_replay(
    generation, tmp_path, monkeypatch, boundary
):
    """A foreground accounting interrupt preserves durable facts until a fresh worker recovers."""
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key=boundary, principal=principal())
    process, evidence = _worker_process(service, tmp_path, boundary, "interrupt")
    try:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert not stdout, stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert service.inspect(accepted.job_id, principal=principal()).state == "running"
    completed = boundary == "completion_interrupt"
    quota = OperationStore.report(service.settings.beta_auth_db_path)
    assert quota.active_operations == 0
    assert quota.outcomes == {"completed" if completed else "interrupted": 1}
    calls = []
    monkeypatch.setattr(cli_module, "_service", lambda: service)
    monkeypatch.setattr(cli_module, "local_job_principal", principal)
    monkeypatch.setattr(
        cli_module,
        "EvaluationJobWorker",
        lambda value: EvaluationJobWorker(value, evaluator_factory=evaluator_factory(calls)),
    )
    runner = CliRunner()
    recovered = runner.invoke(app, ["eval-jobs", "worker", "--once"])
    assert recovered.exit_code == 0, recovered.output
    result = runner.invoke(app, ["eval-jobs", "result", accepted.job_id])
    report = json.loads(result.stdout)["report"]
    assert report["state"] == "interrupted" and report["uncertain_case_id"] is None
    assert len(report["completed_cases"]) == int(completed)
    assert report["usage"]["provider_attempts"] == int(completed)
    assert calls == []
    assert evidence.read_text().splitlines().count("case-0") == int(completed)
    quota = OperationStore.report(service.settings.beta_auth_db_path)
    assert quota.outcomes == {"completed" if completed else "interrupted": 1}
    assert quota.reserved_work_units == quota.retained_rows == 1


@pytest.mark.parametrize("phase", ["interrupt_before_commit", "interrupt_after_commit"])
def test_cli_report_commit_interrupt_preserves_confirmed_terminal_history(
    generation, tmp_path, monkeypatch, phase
):
    """A report COMMIT interrupt preserves either the running summary or committed report."""
    service, suite, _ = job_setup(generation, tmp_path)
    accepted = service.submit(suite, idempotency_key=phase, principal=principal())
    process, _ = _worker_process(service, tmp_path, "finish", phase)
    try:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0 and not stdout, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    committed = phase == "interrupt_after_commit"
    info = service.inspect(accepted.job_id, principal=principal())
    assert info.state == ("completed" if committed else "running")
    assert OperationStore.report(service.settings.beta_auth_db_path).outcomes == {"completed": 1}
    calls = []
    monkeypatch.setattr(cli_module, "_service", lambda: service)
    monkeypatch.setattr(
        cli_module,
        "EvaluationJobWorker",
        lambda value: EvaluationJobWorker(value, evaluator_factory=evaluator_factory(calls)),
    )
    worked = CliRunner().invoke(app, ["eval-jobs", "worker", "--once"])
    assert worked.exit_code == 0, worked.output
    stored = service.result(accepted.job_id, principal=principal())
    assert stored.report.state == ("completed" if committed else "interrupted")
    assert len(stored.report.completed_cases) == 1 and calls == []
    if committed:
        assert stored.sha256 == info.report_sha256
