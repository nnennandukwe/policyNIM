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
