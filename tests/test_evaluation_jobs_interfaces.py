"""CLI and real authenticated MCP transports for durable application evaluation jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from threading import Event

import pytest
from mcp import Client
from mcp.types import TextResourceContents
from test_evaluation_jobs import evaluator_factory, generation_fixture, job_setup, principal
from typer.testing import CliRunner

from policynim.interfaces import evaluation_jobs_cli as cli_module
from policynim.interfaces import evaluation_jobs_mcp as mcp_jobs
from policynim.interfaces import mcp as mcp_module
from policynim.interfaces.cli import app
from policynim.job_types import (
    EvaluationJobInfo,
    EvaluationJobReportManifest,
    EvaluationJobReportPart,
)
from policynim.operations import current_budget
from policynim.services import evaluation_jobs as service_module
from policynim.services.evaluation_jobs import EvaluationJobWorker

generation = generation_fixture


# This fixture alias keeps real immutable generation preparation shared with service tests.
@pytest.fixture
def job_context(generation, tmp_path):
    """Use real SQLite/auth storage and replace only the actual provider scoring seam."""
    return job_setup(generation, tmp_path)


def test_cli_submit_inspect_worker_result_and_repeat_are_usable(
    job_context, tmp_path, monkeypatch
) -> None:
    """Run public commands against durable state and pinned generation archives."""
    service, suite, generation_id = job_context
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_service", lambda: service)
    monkeypatch.setattr(cli_module, "local_job_principal", principal)
    monkeypatch.setattr(
        cli_module,
        "EvaluationJobWorker",
        lambda value: EvaluationJobWorker(value, evaluator_factory=evaluator_factory(calls)),
    )
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(suite.model_dump_json())
    runner = CliRunner()
    command = ["eval-jobs", "submit", str(suite_path), "--idempotency-key", "cli-demo"]
    accepted = runner.invoke(app, command)
    assert accepted.exit_code == 0, accepted.output
    info = EvaluationJobInfo.model_validate_json(accepted.stdout)
    assert info.state == "queued" and info.generation_id == generation_id
    repeated = runner.invoke(app, command)
    assert json.loads(repeated.stdout)["job_id"] == info.job_id
    inspected = runner.invoke(app, ["eval-jobs", "inspect", info.job_id])
    assert inspected.exit_code == 0 and json.loads(inspected.stdout)["state"] == "queued"
    pending = runner.invoke(app, ["eval-jobs", "result", info.job_id])
    assert pending.exit_code == 1 and "result_pending" in pending.output
    worked = runner.invoke(app, ["eval-jobs", "worker", "--once"])
    assert worked.exit_code == 0, worked.output
    assert json.loads(worked.stdout)["state"] == "completed"
    result = runner.invoke(app, ["eval-jobs", "result", info.job_id])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["report"]["snapshot"]["generation_id"] == generation_id
    assert report["report"]["usage"]["measured_tokens"] == 7
    assert calls == ["case-0", "closed"]
    idle = runner.invoke(app, ["eval-jobs", "worker", "--once"])
    assert idle.exit_code == 0 and json.loads(idle.stdout)["state"] == "idle"
    assert calls == ["case-0", "closed"]


def test_authenticated_http_job_survives_disconnect_and_retains_owner_reports(
    generation, tmp_path, monkeypatch
) -> None:
    """A disconnected MCP request can't cancel accepted worker-owned evaluation work."""
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.exceptions import MCPError
    from test_evaluation_jobs import case_result, suite_for
    from test_mcp_authorization import install_issuer, oauth_settings, token_claims
    from test_mcp_protocol import _serve_http

    base, generations, generation_id = generation
    settings = oauth_settings(
        tmp_path,
        corpus_dir=base.corpus_dir,
        generations_dir=base.generations_dir,
        nvidia_embed_model=base.nvidia_embed_model,
        nvidia_base_url=base.nvidia_base_url,
        eval_workspace_dir=tmp_path / "http-evaluations",
    )
    claims = token_claims()
    install_issuer(monkeypatch, settings, claims)
    monkeypatch.setattr(mcp_jobs, "_REPORT_PART_SIZE", 1024)
    started, release = Event(), Event()
    calls: list[str] = []

    class Evaluator:
        """Keep the provider operation alive after its submitting HTTP session ends."""

        def __init__(self, accepted_settings):
            """Record the worker's restored immutable generation selection."""
            self.settings = accepted_settings

        def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
            """Wait at the actual provider boundary independently from the network request."""
            current_budget().reserve_provider_call()
            calls.append(case_id)
            started.set()
            assert release.wait(8)
            current_budget().record_provider_usage(11)
            return case_result(self.settings, suite, case_id)

        def close(self):
            """Record cleanup before the worker can publish completion."""
            calls.append("closed")

    class Worker(EvaluationJobWorker):
        """Inject only the evaluator while retaining real job storage and lifecycle."""

        def __init__(self, service):
            """Use deterministic provider work with the ordinary worker implementation."""
            super().__init__(service, evaluator_factory=Evaluator)

    monkeypatch.setattr(service_module, "EvaluationJobWorker", Worker)
    suite = suite_for(generations)

    async def exercise() -> None:
        """Cross TCP, issuer verification, SDK dispatch, disconnect, reconnect and owner guards."""
        application = mcp_module._build_streamable_http_app(settings)
        async with _serve_http(application) as origin:
            try:
                async with httpx2.AsyncClient(
                    headers={"Authorization": "Bearer fixture"}, trust_env=False
                ) as http:
                    async with Client(
                        streamable_http_client(origin + "/mcp", http_client=http)
                    ) as client:
                        names = {tool.name for tool in (await client.list_tools()).tools}
                        assert {
                            "eval_submit",
                            "eval_inspect",
                            "eval_result",
                            "eval_cancel",
                        } <= names
                        assert (
                            "io.modelcontextprotocol/tasks"
                            not in client.server_capabilities.model_dump_json()
                        )
                        submitted = await client.call_tool(
                            "eval_submit",
                            {
                                "suite": suite.model_dump(mode="json"),
                                "idempotency_key": "disconnect",
                            },
                        )
                        assert not submitted.is_error
                        accepted = EvaluationJobInfo.model_validate(submitted.structured_content)
                        async with asyncio.timeout(5):
                            while not started.is_set():
                                await asyncio.sleep(0.01)
                assert calls == ["case-one"]
                async with httpx2.AsyncClient(
                    headers={"Authorization": "Bearer fixture"}, trust_env=False
                ) as http:
                    async with Client(
                        streamable_http_client(origin + "/mcp", http_client=http)
                    ) as client:
                        progress = await client.call_tool(
                            "eval_inspect", {"job_id": accepted.job_id}
                        )
                        assert (
                            EvaluationJobInfo.model_validate(progress.structured_content).state
                            == "running"
                        )
                        duplicate = await client.call_tool(
                            "eval_submit",
                            {
                                "suite": suite.model_dump(mode="json"),
                                "idempotency_key": "disconnect",
                            },
                        )
                        assert (
                            EvaluationJobInfo.model_validate(duplicate.structured_content).job_id
                            == accepted.job_id
                        )
                        release.set()
                        async with asyncio.timeout(5):
                            while True:
                                progress = await client.call_tool(
                                    "eval_inspect", {"job_id": accepted.job_id}
                                )
                                if (
                                    EvaluationJobInfo.model_validate(
                                        progress.structured_content
                                    ).state
                                    == "completed"
                                ):
                                    break
                                await asyncio.sleep(0.02)
                        output = await client.call_tool("eval_result", {"job_id": accepted.job_id})
                        manifest = EvaluationJobReportManifest.model_validate(
                            output.structured_content
                        )
                        assert manifest.generation_id == generation_id
                        assert len(manifest.part_uris) > 1
                        fragments: list[str] = []
                        for uri in manifest.part_uris:
                            read = await client.read_resource(uri)
                            assert isinstance(read.contents[0], TextResourceContents)
                            part = EvaluationJobReportPart.model_validate_json(
                                read.contents[0].text
                            )
                            assert (
                                part.part_sha256
                                == "sha256:" + hashlib.sha256(part.content.encode()).hexdigest()
                            )
                            fragments.append(part.content)
                        payload = "".join(fragments)
                        assert (
                            manifest.report_sha256
                            == "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
                        )
                        assert json.loads(payload)["snapshot"]["generation_id"] == generation_id
                        claims["sub"] = "another-owner"
                        denied = await client.call_tool("eval_inspect", {"job_id": accepted.job_id})
                        assert denied.is_error and "job_unavailable" in str(denied.content)
                        with pytest.raises(MCPError, match="job_unavailable"):
                            await client.read_resource(manifest.part_uris[0])
                        claims["sub"] = "user-123"
            finally:
                release.set()

    asyncio.run(exercise())
    assert calls == ["case-one", "closed"]


@pytest.mark.parametrize("arguments", [["worker", "--once"], ["prune"]])
def test_cli_sqlite_lock_is_a_controlled_recoverable_failure(
    job_context, monkeypatch, arguments: list[str]
) -> None:
    """Actual SQLite write contention returns a nonzero recovery message without traceback."""
    import sqlite3

    service, suite, _ = job_context
    accepted = service.submit(suite, idempotency_key="locked-cli", principal=principal())
    monkeypatch.setattr(cli_module, "_service", lambda: service)
    monkeypatch.setattr(cli_module, "local_job_principal", principal)
    connect = service.store._connect

    def bounded_connection():
        """Shorten only SQLite's wait in this real lock-contention regression."""
        connection = connect()
        connection.execute("PRAGMA busy_timeout=10")
        return connection

    monkeypatch.setattr(service.store, "_connect", bounded_connection)
    with sqlite3.connect(service.store.path) as competing:
        competing.execute("BEGIN IMMEDIATE")
        result = CliRunner().invoke(app, ["eval-jobs", *arguments])
    assert result.exit_code == 1
    assert "job_storage_unavailable" in result.output and "Preserve storage" in result.output
    assert "Traceback" not in result.output and str(service.store.path) not in result.output
    assert service.inspect(accepted.job_id, principal=principal()).state == "queued"


def test_foreground_cli_reports_accounting_pause_and_exits(job_context, monkeypatch) -> None:
    """A paused background worker can't leave the foreground CLI claiming it's running."""
    import sqlite3
    import time
    from types import SimpleNamespace

    from policynim.storage.operation_store import OperationStore

    service, suite, _ = job_context
    accepted = service.submit(suite, idempotency_key="paused-cli", principal=principal())
    queued = service.submit(suite, idempotency_key="paused-next", principal=principal())
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_service", lambda: service)
    monkeypatch.setattr(
        cli_module,
        "EvaluationJobWorker",
        lambda value: EvaluationJobWorker(value, evaluator_factory=evaluator_factory(calls)),
    )

    def fail_accounting(*args, **kwargs):
        """Inject the actual quota terminal-write failure after provider completion."""
        raise sqlite3.OperationalError("fixture accounting write failure")

    sleeps: list[int] = []

    def wait_for_pause(_seconds):
        """Give real background recovery time; bound the old endless-loop behavior."""
        sleeps.append(1)
        if len(sleeps) > 1:
            raise KeyboardInterrupt
        deadline = time.monotonic() + 5
        while service.inspect(accepted.job_id, principal=principal()).failure_code is None:
            assert time.monotonic() < deadline
            time.sleep(0.01)

    monkeypatch.setattr(OperationStore, "finish", fail_accounting)
    monkeypatch.setattr(cli_module, "time", SimpleNamespace(sleep=wait_for_pause))
    result = CliRunner().invoke(app, ["eval-jobs", "worker"])
    assert result.exit_code == 1
    assert "accounting_incomplete" in result.output and "restart" in result.output
    assert "Traceback" not in result.output
    assert service.inspect(queued.job_id, principal=principal()).state == "queued"
    assert calls == ["case-0", "closed"]


def test_rejected_sdk_submission_doesnt_start_unrelated_queued_work(job_context) -> None:
    """A rejected request can't start the shared worker or execute another owner's job."""
    from mcp.server import MCPServer

    service, suite, _ = job_context
    accepted = service.submit(suite, idempotency_key="other-owner", principal=principal("other"))
    calls: list[str] = []
    started = Event()

    class Worker(EvaluationJobWorker):
        """Observe whether the rejected request starts the real worker."""

        def start(self) -> None:
            """Record an actual background worker startup before inspecting rejection."""
            super().start()
            started.set()

    worker = Worker(service, evaluator_factory=evaluator_factory(calls))
    server = MCPServer("job rejection fixture")

    async def run_tool(name, operation, ctx):
        """Keep SDK dispatch while executing the real submission boundary."""
        return operation()

    mcp_jobs.register_evaluation_jobs(
        server, get_principal=lambda _: principal(), get_worker=lambda: worker, run_tool=run_tool
    )
    rejected = suite.model_copy(update={"expected_catalog_digest": "sha256:" + "0" * 64})

    async def exercise() -> None:
        async with Client(server) as client:
            result = await client.call_tool(
                "eval_submit",
                {"suite": rejected.model_dump(mode="json"), "idempotency_key": "invalid"},
            )
            assert result.is_error

    try:
        asyncio.run(exercise())
        assert not started.is_set()
        assert service.inspect(accepted.job_id, principal=principal("other")).state == "queued"
        assert calls == []
    finally:
        worker.close()


def test_sdk_job_worker_initialization_failure_is_sanitized(job_context, caplog) -> None:
    """A lazy worker failure stays inside the same safe tool boundary as submission."""
    from mcp.server import MCPServer

    service, suite, _ = job_context
    sentinel = "Bearer private-token /Users/private/job-volume"
    runtime = mcp_module._ToolExecutor(settings=service.settings)
    server = MCPServer("job initialization fixture")

    def get_worker():
        """Fail before a worker exists with an intentionally private exception payload."""
        raise RuntimeError(sentinel)

    async def run_tool(name, operation, ctx):
        """Use the production error/logging boundary around SDK tool execution."""
        return await mcp_module._run_logged_tool(name, operation, ctx=ctx, executor=runtime)

    mcp_jobs.register_evaluation_jobs(
        server, get_principal=lambda _: principal(), get_worker=get_worker, run_tool=run_tool
    )

    async def exercise() -> None:
        """Request submission through SDK dispatch, then inspect its safe protocol error."""
        try:
            async with Client(server) as client:
                result = await client.call_tool(
                    "eval_submit",
                    {"suite": suite.model_dump(mode="json"), "idempotency_key": "init-failure"},
                )
                assert result.is_error
                assert "internal_error" in str(result.content)
                assert sentinel not in str(result.content)
        finally:
            await runtime.close()

    asyncio.run(exercise())
    assert sentinel not in caplog.text
    assert service.store.aggregate_counts() == {}
