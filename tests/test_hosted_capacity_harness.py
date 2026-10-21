"""Real-process synthetic HTTP capacity and failure-path smoke coverage."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _coordinate_first_workload_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold one synthetic query through active snapshots bracketing a real health response."""
    from threading import Lock

    import httpx

    from policynim.storage.operation_store import OperationStore

    bootstrap = tmp_path / "coordinated-provider.py"
    bootstrap.write_text(
        '''
import json, runpy, sys
from pathlib import Path
from types import SimpleNamespace

script, config = sys.argv[1:]
namespace = runpy.run_path(script)
state = namespace["_serve"].__globals__
clock = state["time"]
release = Path(config).parent / "provider-probe-observed"
calls = 0

def sleep(seconds):
    """Keep accepted provider work active until its probe responses are bracketed."""
    global calls
    calls += 1
    if calls == 2:
        deadline = clock.monotonic() + 5
        while not release.exists():
            if clock.monotonic() >= deadline:
                raise RuntimeError("Controlled provider probe wasn't observed.")
            clock.sleep(0.005)
    clock.sleep(seconds)

state["time"] = SimpleNamespace(
    sleep=sleep, monotonic=clock.monotonic, perf_counter=clock.perf_counter
)
namespace["_serve"](Path(config))
''',
        encoding="utf-8",
    )
    original_report = OperationStore.report
    original_get = httpx.AsyncClient.get
    first_root: Path | None = None
    active_before_health = False
    health_returned = False
    probe_lock = Lock()

    class ControlledProcess(subprocess.Popen[str]):
        """Keep the real process class contract while routing one fixture through its gate."""

        def __init__(self, command: list[str], *args: Any, **kwargs: Any) -> None:
            """Change only the first synthetic server's provider timing."""
            nonlocal first_root
            if "--serve-config" in command and first_root is None:
                config = Path(command[command.index("--serve-config") + 1])
                first_root = config.parent
                command = [command[0], str(bootstrap), command[1], str(config)]
            super().__init__(command, *args, **kwargs)

    def report(path: Path):
        """Release only after an actual response lies between two active snapshots."""
        nonlocal active_before_health, health_returned
        observed = original_report(path)
        if path.parent == first_root:
            with probe_lock:
                if active_before_health and health_returned and observed.active_operations > 0:
                    (path.parent / "provider-probe-observed").touch()
                active_before_health = observed.active_operations > 0
                health_returned = False
        return observed

    async def get(client, *args, **kwargs):
        """Mark a successful health response without replacing its real transport or payload."""
        nonlocal health_returned
        response = await original_get(client, *args, **kwargs)
        if args and str(args[0]) == "/healthz" and response.status_code == 200:
            with probe_lock:
                health_returned = True
        return response

    monkeypatch.setattr(subprocess, "Popen", ControlledProcess)
    monkeypatch.setattr(OperationStore, "report", staticmethod(report))
    monkeypatch.setattr(httpx.AsyncClient, "get", get)


def test_provider_gate_waits_for_one_bracketed_probe_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep work active through the post-snapshot when two cycles share active observations."""
    import asyncio
    import runpy
    import threading
    import time

    import httpx

    from policynim.storage.operation_store import OperationStore

    actual_report = OperationStore.report
    _coordinate_first_workload_probe(tmp_path, monkeypatch)
    coordinated_report = OperationStore.report
    original_post, original_get = httpx.AsyncClient.post, httpx.AsyncClient.get
    zero_observed = threading.Event()
    ledger_paths: list[Path] = []
    active_trace: list[int] = []
    calls, health_calls = 0, 0

    def report(path: Path):
        """Retain actual public snapshots and start the request only after an empty pre-probe."""
        observed = coordinated_report(path)
        if not ledger_paths:
            ledger_paths.append(path)
        active_trace.append(observed.active_operations)
        if observed.active_operations == 0:
            zero_observed.set()
        return observed

    async def post(client, *args, **kwargs):
        """Delay submission until the first cycle's real pre-probe has no active work."""
        nonlocal calls
        if kwargs.get("json", {}).get("method") == "tools/call":
            calls += 1
            if calls > 1:
                deadline = time.monotonic() + 5
                while not zero_observed.is_set():
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.005)
        return await original_post(client, *args, **kwargs)

    async def get(client, *args, **kwargs):
        """Force a zero/active first cycle and pause the next response beyond provider delay."""
        nonlocal health_calls
        response = await original_get(client, *args, **kwargs)
        if calls > 1:
            health_calls += 1
            if health_calls == 1:
                deadline = time.monotonic() + 5
                while (
                    await asyncio.to_thread(actual_report, ledger_paths[0])
                ).active_operations == 0:
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.005)
            elif health_calls == 2:
                await asyncio.sleep(0.25)
        return response

    monkeypatch.setattr(OperationStore, "report", staticmethod(report))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    script = Path(__file__).resolve().parents[1] / "scripts/measure_hosted_capacity.py"
    output = tmp_path / "bracketed-capacity.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--output",
            str(output),
            "--users",
            "1",
            "--chunks",
            "20",
            "--requests-per-user",
            "1",
            "--scenarios",
            "baseline",
        ],
    )
    status = runpy.run_path(str(script))["main"]()
    scenario = json.loads(output.read_text())["scenarios"][0]
    assert active_trace[:3] == [0, 1, 1]
    assert scenario["outcomes"] == {"ok": 1}
    assert status == 0, {"active_trace": active_trace, "scenario": scenario}
    assert scenario["paired_active_work_probe_observed"] is True


def test_readiness_loss_after_a_paired_probe_cant_complete_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain a real transient missing-generation 503 after an earlier successful bracket."""
    import runpy

    import httpx

    from policynim.storage.operation_store import OperationStore

    _coordinate_first_workload_probe(tmp_path, monkeypatch)
    original_report, original_get = OperationStore.report, httpx.AsyncClient.get
    fixture_roots: list[Path] = []
    injected = False

    def report(path: Path):
        """Locate only the fixture whose public operation report is being measured."""
        if not fixture_roots:
            fixture_roots.append(path.parent)
        return original_report(path)

    async def get(client, *args, **kwargs):
        """Remove and restore the actual active pointer around one later health request."""
        nonlocal injected
        if (
            fixture_roots
            and not injected
            and (fixture_roots[0] / "provider-probe-observed").exists()
        ):
            injected = True
            pointer = fixture_roots[0] / "generations" / "active.json"
            previous = pointer.read_bytes()
            pointer.unlink()
            try:
                response = await original_get(client, *args, **kwargs)
                assert response.status_code == 503
                return response
            finally:
                pointer.write_bytes(previous)
        return await original_get(client, *args, **kwargs)

    monkeypatch.setattr(OperationStore, "report", staticmethod(report))
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    script = Path(__file__).resolve().parents[1] / "scripts/measure_hosted_capacity.py"
    output = tmp_path / "readiness-loss.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--output",
            str(output),
            "--users",
            "1",
            "--chunks",
            "20",
            "--requests-per-user",
            "1",
            "--provider-delay",
            "0.5",
            "--scenarios",
            "baseline",
        ],
    )
    status = runpy.run_path(str(script))["main"]()
    scenario = json.loads(output.read_text())["scenarios"][0]
    assert injected
    assert scenario["paired_active_work_probe_observed"] is True
    assert scenario["cheap_probe_outcomes"]["health_503"] == 1
    assert scenario["unexpected_probe_failure_counts"] == {"health:health_503": 1}
    assert scenario["outcomes"] == {"ok": 1}
    assert status == 1 and scenario["measurement_complete"] is False
    assert scenario["managed_identity_verified"] is False


@pytest.mark.parametrize("mutation", ["generation", "count", "domain"])
def test_bad_warmup_retains_the_scenario_and_earlier_measurements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """Keep an earlier valid scenario when a later real warmup response has invalid evidence."""
    import runpy

    import httpx

    _coordinate_first_workload_probe(tmp_path, monkeypatch)
    original_post = httpx.AsyncClient.post
    calls: dict[str, int] = {}

    async def post(client, *args, **kwargs):
        """Corrupt only the second server's first completed search response."""
        response = await original_post(client, *args, **kwargs)
        if kwargs.get("json", {}).get("method") == "tools/call":
            server = str(client.base_url)
            calls[server] = calls.get(server, 0) + 1
            if len(calls) == 2 and calls[server] == 1:
                envelope = response.json()
                content = envelope["result"]["structuredContent"]
                if mutation == "generation":
                    content["generation_id"] = "b" * 64
                elif mutation == "count":
                    content["hits"] = []
                else:
                    content["hits"][0]["policy"]["domain"] = "domain-1"
                return httpx.Response(200, json=envelope)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    script = Path(__file__).resolve().parents[1] / "scripts/measure_hosted_capacity.py"
    output = tmp_path / "warmup-failure.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--output",
            str(output),
            "--users",
            "1",
            "--chunks",
            "20",
            "--requests-per-user",
            "1",
            "--scenarios",
            "baseline",
            "slow-provider",
        ],
    )
    status = runpy.run_path(str(script))["main"]()
    result = json.loads(output.read_text())
    assert status == 1 and result["measurement_complete"] is False
    first, second = result["scenarios"]
    assert first["measurement_complete"] is True
    assert second["measurement_complete"] is False
    assert second["warmup_outcome"] == (
        "generation_identity_mismatch" if mutation == "generation" else "retrieval_result_mismatch"
    )
    assert second["operator_report"]["outcomes"]["completed"] == 2


@pytest.mark.parametrize(
    ("distribution", "chunks", "fault"),
    [
        ("uniform", 10000, "none"),
        ("skewed", 10000, "none"),
        ("uniform", 20, "pointer"),
        ("uniform", 20, "manifest"),
    ],
)
def test_fixture_uses_managed_health_and_search_with_recovery(
    tmp_path: Path, distribution: str, chunks: int, fault: str
) -> None:
    """Exercise the actual application routes and managed reader failures in an owned child."""
    root = Path(__file__).resolve().parents[1]
    control = r'''
import json, runpy, sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from starlette.testclient import TestClient
from policynim.services.generations import GenerationStore
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import EmbeddingIdentity

script, directory, distribution, count, fault = sys.argv[1:]
root = Path(directory)
config = {
    "chunks": int(count),
    "distribution": distribution,
    "tokens": ["fixture"],
    "port": 12345,
    "delay": 0,
}
config_path = root / "fixture.json"
config_path.write_text(json.dumps(config))


def serve(application, **kwargs):
    """Inspect normal HTTP routes before, during and after owned generation corruption."""
    with TestClient(application, base_url="http://127.0.0.1:12345") as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        generation_id = health.json()["generation_id"]
        assert generation_id is not None, "Harness bypasses managed-generation health."
        assert health.json()["row_count"] == int(count)
        identity = EmbeddingIdentity(
            model="synthetic-orthogonal-4", endpoint="https://synthetic.invalid"
        )
        generations = GenerationStore(root=root / "generations", embedding=identity)
        with generations.pin() as snapshot:
            assert snapshot.generation_id == generation_id
            index = SQLiteVecIndexStore(
                path=snapshot.index_path, embedding_identity=identity
            )
            try:
                observed = Counter(chunk.policy.domain for chunk in index.list_chunks())
            finally:
                index.close()
        expected = (
            {f"domain-{domain}": int(count) // 4 for domain in range(4)}
            if distribution == "uniform"
            else {"domain-0": 9000, "domain-1": 334, "domain-2": 333, "domain-3": 333}
        )
        assert observed == expected
        headers = {
            "Authorization": "Bearer fixture",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": "policy_search",
        }
        discovery_headers = {**headers, "Mcp-Method": "tools/list"}
        discovery_headers.pop("Mcp-Name")
        discovered = client.post(
            "/mcp",
            headers=discovery_headers,
            json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/list",
                "params": {"_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }},
            },
        ).json()["result"]
        assert {tool["name"] for tool in discovered["tools"]} == {
            "policy_preflight", "policy_search", "policy_compile",
            "policy_preflight_evidence", "eval_submit", "eval_inspect",
            "eval_cancel", "eval_result",
        }
        assert (root / "eval-workspace" / "jobs" / "jobs.sqlite3").is_file()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
                "name": "policy_search",
                "arguments": {
                    "query": "synthetic-domain-0",
                    "domain": "domain-0",
                    "top_k": 5,
                },
            },
        }
        response = client.post("/mcp", headers=headers, json=payload).json()["result"]
        assert not response.get("isError", False), response
        assert response["structuredContent"]["generation_id"] == generation_id
        assert len(response["structuredContent"]["hits"]) == 5
        if fault != "none":
            target = (
                root / "generations" / "active.json"
                if fault == "pointer"
                else root / "generations" / "objects" / generation_id / "manifest.json"
            )
            original = target.read_bytes()
            if fault == "pointer":
                target.unlink()
            else:
                target.write_bytes(b"invalid-generation-manifest")
            try:
                assert client.get("/healthz").status_code == 503
                rejected = client.post("/mcp", headers=headers, json=payload).json()[
                    "result"
                ]
                assert rejected["isError"] is True
            finally:
                target.write_bytes(original)
            recovered = client.get("/healthz")
            assert recovered.status_code == 200
            assert recovered.json()["generation_id"] == generation_id
            response = client.post("/mcp", headers=headers, json=payload).json()["result"]
            assert not response.get("isError", False)
            assert response["structuredContent"]["generation_id"] == generation_id
        print(
            json.dumps(
                {"managed_fixture_verified": True, "chunks": int(count), "fault": fault}
            )
        )


with patch("uvicorn.run", serve):
    runpy.run_path(script)["_serve"](config_path)
'''
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            control,
            str(root / "scripts/measure_hosted_capacity.py"),
            str(tmp_path),
            distribution,
            str(chunks),
            fault,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("generation_id", [None, "b" * 64, "a" * 64])
def test_rpc_binds_success_to_the_published_generation(generation_id: str | None) -> None:
    """A valid HTTP search envelope from a different or legacy index isn't successful evidence."""
    import asyncio
    import runpy

    import httpx

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/measure_hosted_capacity.py")
    )

    async def inspect() -> tuple[float, str, int]:
        """Exercise the existing HTTP response parser with a typed search payload."""
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "content": [],
                        "structuredContent": {
                            "query": "synthetic-domain-0",
                            "top_k": 5,
                            "hits": [],
                            "generation_id": generation_id,
                        },
                    },
                },
            )
        )
        async with httpx.AsyncClient(base_url="http://fixture", transport=transport) as client:
            return await namespace["_rpc"](client, "fixture", expected_generation_id="a" * 64)

    _, outcome, count = asyncio.run(inspect())
    assert outcome == ("ok" if generation_id == "a" * 64 else "generation_identity_mismatch")
    assert count == 0


@pytest.mark.parametrize(
    ("generation_id", "row_count", "outcome"),
    [
        ("a" * 64, 20, "health_200"),
        (None, 20, "health_identity_mismatch"),
        ("b" * 64, 20, "health_identity_mismatch"),
        ("a" * 64, 21, "health_identity_mismatch"),
    ],
)
def test_health_binds_readiness_to_published_generation_and_count(
    generation_id: str | None, row_count: int, outcome: str
) -> None:
    """Neither a legacy response nor a different managed fixture can establish readiness."""
    import runpy

    import httpx

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/measure_hosted_capacity.py")
    )
    response = httpx.Response(
        200,
        json={
            "status": "ok",
            "ready": True,
            "table_name": "policy_chunks",
            "row_count": row_count,
            "generation_id": generation_id,
        },
    )
    result, _ = namespace["_inspect_health"](
        response, {"generation_id": "a" * 64, "chunk_count": 20}
    )
    assert result == outcome


@pytest.mark.parametrize(
    "failure",
    [
        "invalid_rpc_response",
        "jsonrpc_error",
        "generation_identity_mismatch",
        "empty_hits",
        "wrong_domain",
        "empty_discovery",
    ],
)
def test_invalid_server_response_cant_complete_managed_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Reject invalid server responses after real warmup and accepted HTTP work."""
    import runpy

    import httpx

    _coordinate_first_workload_probe(tmp_path, monkeypatch)
    original_post = httpx.AsyncClient.post
    calls = 0

    async def corrupt_workload(client, *args, **kwargs):
        """Keep real server execution and corrupt only the measured response after warmup."""
        nonlocal calls
        response = await original_post(client, *args, **kwargs)
        payload = kwargs.get("json", {})
        if failure == "empty_discovery" and payload.get("method") == "tools/list":
            envelope = response.json()
            envelope["result"]["tools"] = []
            return httpx.Response(200, json=envelope)
        if payload.get("method") == "tools/call" and failure != "empty_discovery":
            calls += 1
            if calls > 1:
                if failure in {"empty_hits", "wrong_domain"}:
                    envelope = response.json()
                    content = envelope["result"]["structuredContent"]
                    if failure == "empty_hits":
                        content["hits"] = []
                    else:
                        content["hits"][0]["policy"]["domain"] = "domain-1"
                    return httpx.Response(200, json=envelope)
                if failure == "jsonrpc_error":
                    return httpx.Response(
                        200,
                        json={
                            "jsonrpc": "2.0",
                            "id": payload["id"],
                            "error": {"code": -32603, "message": "private-response-sentinel"},
                        },
                    )
                if failure == "generation_identity_mismatch":
                    envelope = response.json()
                    envelope["result"]["structuredContent"]["generation_id"] = "b" * 64
                    return httpx.Response(200, json=envelope)
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {"content": [], "structuredContent": {"hits": []}},
                    },
                )
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", corrupt_workload)
    output = tmp_path / "malformed-capacity.json"
    script = Path(__file__).resolve().parents[1] / "scripts/measure_hosted_capacity.py"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--output",
            str(output),
            "--users",
            "1",
            "--chunks",
            "20",
            "--requests-per-user",
            "1",
            "--scenarios",
            "baseline",
        ],
    )
    status = runpy.run_path(str(script))["main"]()
    scenario = json.loads(output.read_text())["scenarios"][0]
    if failure == "empty_discovery":
        assert scenario["paired_active_work_probe_observed"] is False
        assert scenario["cheap_probe_outcomes"]["discovery_tool_mismatch"] > 0
        assert scenario["outcomes"] == {"ok": 1}
    else:
        assert scenario["paired_active_work_probe_observed"] is True
        outcome = (
            "retrieval_result_mismatch" if failure in {"empty_hits", "wrong_domain"} else failure
        )
        assert scenario["outcomes"] == {outcome: 1}
    assert scenario["operator_report"]["outcomes"]["completed"] == 2
    assert status == 1 and scenario["measurement_complete"] is False
    assert scenario["managed_identity_verified"] is False
    assert "private-response-sentinel" not in output.read_text()


def test_real_http_capacity_harness_records_limits_and_failure_outcomes(tmp_path: Path) -> None:
    """Exercise real sockets/SQLite while keeping NVIDIA and production outside the test."""
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "capacity.json"
    process = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "measure_hosted_capacity.py"),
            "--output",
            str(output),
            "--users",
            "12",
            "--chunks",
            "100",
            "--requests-per-user",
            "1",
            "--provider-delay",
            "0.2",
            "--scenarios",
            "baseline",
            "saturation",
            "slow-provider",
            "locked-database",
            "disconnect",
            "shutdown",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_text())
    assert report["evidence_kind"] == "synthetic_provider_real_http_and_sqlite_vec"
    assert report["live_nvidia_calls"] == 0
    assert report["production_capacity_verified"] is False
    assert report["configuration"]["index_scope"] == "managed_generation"
    scenarios = {item["scenario"]: item for item in report["scenarios"]}
    failure_summary = json.dumps(
        {
            name: {
                key: value.get(key)
                for key in (
                    "measurement_complete",
                    "outcomes",
                    "attempted_requests",
                    "completed_client_responses",
                    "response_latency_ms",
                    "rejected_response_latency_ms",
                    "observed_peak_active_operations",
                    "provider_stage_counts",
                    "state_read_failure_counts",
                    "server_exit_code",
                    "server_failure_diagnostics",
                )
            }
            for name, value in scenarios.items()
        },
        sort_keys=True,
    )
    assert scenarios["disconnect"]["drain_observation"] == "terminal_outcome_observed"
    assert scenarios["disconnect"]["intervention_started_with_active_work"] is True
    assert scenarios["shutdown"]["intervention_started_with_active_work"] is True
    assert scenarios["disconnect"]["client_concurrency"] == 1
    assert scenarios["shutdown"]["client_concurrency"] == 1
    assert scenarios["baseline"]["outcomes"] == {"ok": 12}, failure_summary
    assert scenarios["baseline"]["successful_search_hit_counts"] == {"5": 12}, failure_summary
    assert scenarios["saturation"]["outcomes"].get("server_busy", 0) >= 1
    assert scenarios["locked-database"]["outcomes"] == {"admission_busy": 12}, failure_summary
    assert scenarios["locked-database"]["operator_report"]["retained_rows"] == 1  # warmup only
    assert scenarios["locked-database"]["provider_stage_counts"]["embed_query"] == 1
    assert scenarios["locked-database"]["paired_active_work_probe_observed"] is False
    assert scenarios["baseline"]["paired_active_work_probe_observed"] is True
    assert scenarios["saturation"]["paired_active_work_probe_observed"] is True
    assert scenarios["slow-provider"]["paired_active_work_probe_observed"] is True
    assert scenarios["locked-database"]["server_failure_diagnostics"]["tool_failure_counts"] == {
        "admission_busy": 12
    }, failure_summary
    assert scenarios["locked-database"]["server_failure_diagnostics"]["storage_failures"], (
        failure_summary
    )
    for scenario in scenarios.values():
        fixture = scenario["fixture_publication"]
        assert fixture["chunk_count"] == 100
        assert scenario["managed_identity_verified"] is True
        assert scenario["observed_health_generation_ids"] == [fixture["generation_id"]]
        assert set(scenario["observed_health_row_counts"]) == {"100"}
        assert scenario["successful_search_identity_validation"] == "published_generation"
        assert scenario["observed_peak_active_operations"] <= 10
        assert scenario["cheap_probe_outcomes"].get("health_200", 0) > 0
        assert scenario["operator_report"]["active_operations"] == 0
        assert scenario["live_provider_usage"] is None
        assert scenario["sampled_peak_server_rss_bytes"] > 0
        assert scenario["peak_server_rss_bytes"] > 0
        assert scenario["peak_server_rss_scope"] == "process_max_at_application_shutdown"
        assert scenario["server_exit_code"] in {0, -15}


@pytest.mark.parametrize("probe_failure", ["none", "transport", "unexpected"])
def test_completed_client_isnt_reported_as_disconnected(
    tmp_path, monkeypatch, probe_failure
) -> None:
    """A request that already finished can't be evidence of a cancelled connection."""
    import asyncio
    import runpy
    from unittest.mock import AsyncMock, Mock

    import httpx

    from policynim.storage.operation_store import OperationStore

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "measure_hosted_capacity.py")
    )
    measure = namespace["_measure"]
    client = AsyncMock()
    client.__aenter__.return_value = client
    calls = 0

    def health(*args: object, **kwargs: object) -> httpx.Response:
        """Keep readiness healthy, then inject a probe-only failure with a private payload."""
        nonlocal calls
        calls += 1
        if calls > 1 and probe_failure == "transport":
            raise httpx.ConnectError("private-probe-payload")
        if calls > 1 and probe_failure == "unexpected":
            raise RuntimeError("private-probe-payload")
        return httpx.Response(200)

    client.get.side_effect = health
    monkeypatch.setattr(httpx, "AsyncClient", Mock(return_value=client))
    monkeypatch.setitem(measure.__globals__, "_rpc", AsyncMock(return_value=(1.0, "ok", 5)))
    monkeypatch.setitem(measure.__globals__, "_sample_rss", lambda pid: 1)
    OperationStore(tmp_path / "auth.sqlite3")
    process = Mock(pid=123)
    process.poll.return_value = None
    if probe_failure == "unexpected":
        with pytest.raises(
            RuntimeError, match="Synthetic probe sampling failed: RuntimeError"
        ) as error:
            asyncio.run(
                measure(
                    {
                        "port": 1234,
                        "tokens": ["fixture"],
                        "users": 25,
                        "requests_per_user": 2,
                        "delay": 0,
                    },
                    tmp_path,
                    process,
                    "disconnect",
                )
            )
        assert "private-probe-payload" not in str(error.value)
        return
    result = asyncio.run(
        measure(
            {"port": 1234, "tokens": ["fixture"], "users": 25, "requests_per_user": 2, "delay": 0},
            tmp_path,
            process,
            "disconnect",
        )
    )
    assert result["intervention"] == "client_task_already_completed"
    assert result["intervention_started_with_active_work"] is False
    assert result["client_concurrency"] == result["attempted_requests"] == 1

    if probe_failure == "transport":
        assert result["cheap_probe_outcomes"]["health_transport_failure"] >= 1
        assert result["intervention_probe_observed"] is False
        assert result["measurement_complete"] is False


@pytest.mark.parametrize(
    ("envelope", "discovery", "expected"),
    [
        (
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "private"}},
            False,
            "jsonrpc_error",
        ),
        ({"jsonrpc": "2.0", "id": 1}, False, "invalid_rpc_response"),
        ([], False, "invalid_rpc_response"),
        ({"jsonrpc": "2.0", "id": 2, "result": {}}, False, "invalid_rpc_response"),
        ({"jsonrpc": "1.0", "id": 1, "result": {}}, False, "invalid_rpc_response"),
        (
            {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": -1, "message": "private"}},
            False,
            "invalid_rpc_response",
        ),
        ({"jsonrpc": "2.0", "id": 1, "result": {"content": []}}, False, "invalid_rpc_response"),
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [], "structuredContent": {"hits": []}},
            },
            False,
            "invalid_rpc_response",
        ),
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [],
                    "structuredContent": {"query": "fixture", "top_k": 5, "hits": []},
                },
            },
            False,
            "ok",
        ),
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": "admission_busy: retry shortly"}],
                    "isError": True,
                },
            },
            False,
            "admission_busy",
        ),
        ({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}, True, "ok"),
        ({"jsonrpc": "2.0", "id": 1, "result": {}}, True, "invalid_rpc_response"),
    ],
)
def test_rpc_doesnt_count_protocol_or_missing_search_results_as_success(
    envelope: object, discovery: bool, expected: str
) -> None:
    """HTTP success alone can't inflate measured successful tool work or warmup readiness."""
    import asyncio
    import runpy

    import httpx

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "measure_hosted_capacity.py")
    )

    async def request() -> tuple[float, str, int]:
        """Exercise the real HTTP parser with a controlled response envelope."""
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=envelope))
        async with httpx.AsyncClient(base_url="http://fixture", transport=transport) as client:
            return await namespace["_rpc"](client, "fixture", discovery=discovery)

    elapsed, outcome, hits = asyncio.run(request())
    assert elapsed >= 0 and outcome == expected and hits == 0


def test_public_harness_preserves_prior_scenarios_when_final_state_is_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real HTTP work and SQLite contention retain incomplete evidence for every scenario."""
    import runpy
    import sqlite3

    from policynim.storage.operation_store import HostedOperationReport, OperationStore

    _coordinate_first_workload_probe(tmp_path, monkeypatch)
    original = OperationStore.report
    first: Path | None = None

    def lock_later_scenario(path: Path) -> HostedOperationReport:
        """Keep the first scenario healthy and hold an actual exclusive lock during later reads."""
        nonlocal first
        if first is None:
            first = path
        if path == first:
            return original(path)
        with sqlite3.connect(path, timeout=0.1) as writer:
            writer.execute("PRAGMA journal_mode=DELETE")
            writer.execute("BEGIN EXCLUSIVE")
            return original(path)

    monkeypatch.setattr(OperationStore, "report", staticmethod(lock_later_scenario))
    output = tmp_path / "incomplete-capacity.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "measure_hosted_capacity.py"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--output",
            str(output),
            "--users",
            "1",
            "--chunks",
            "20",
            "--requests-per-user",
            "1",
            "--scenarios",
            "baseline",
            "saturation",
            "shutdown",
        ],
    )
    namespace = runpy.run_path(str(script))
    assert namespace["main"]() == 1
    report = json.loads(output.read_text())
    assert report["measurement_complete"] is False
    assert len(report["scenarios"]) == 3
    assert report["scenarios"][0]["measurement_complete"] is True
    failed = report["scenarios"][1]
    assert failed["measurement_complete"] is False and failed["operator_report"] is None
    assert failed["state_read_failure_counts"]["final:database_busy"] == 3
    assert failed["attempted_requests"] == 1
    assert str(tmp_path) not in output.read_text()

    unknown = report["scenarios"][2]
    assert unknown["intervention_started_with_active_work"] is None
    assert unknown["intervention"] == "SIGTERM_without_active_work_observation"
    assert unknown["measurement_complete"] is False


def test_public_harness_final_state_cant_replace_missing_workload_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful final read can't establish an observed peak when workload reads all fail."""
    import asyncio
    import inspect
    import runpy
    import sqlite3

    from policynim.storage.operation_store import OperationStore

    original_to_thread = asyncio.to_thread
    original_report = OperationStore.report

    def locked_report(path: Path) -> object:
        """Exercise a real blocked read without exposing private database diagnostics."""
        with sqlite3.connect(path, timeout=0.1) as writer:
            writer.execute("PRAGMA journal_mode=DELETE")
            writer.execute("BEGIN EXCLUSIVE")
            return original_report(path)

    async def fail_workload_reads(function, /, *args, **kwargs):
        """Inject contention only in workload observations, leaving the final read available."""
        frame = inspect.currentframe()
        assert frame is not None and frame.f_back is not None
        caller = frame.f_back
        if function is original_report and caller.f_locals.get("phase") == "probe":
            return await original_to_thread(locked_report, *args, **kwargs)
        return await original_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fail_workload_reads)
    output = tmp_path / "missing-peak.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "measure_hosted_capacity.py"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--output",
            str(output),
            "--users",
            "1",
            "--chunks",
            "20",
            "--requests-per-user",
            "1",
            "--scenarios",
            "baseline",
        ],
    )
    namespace = runpy.run_path(str(script))
    exit_code = namespace["main"]()
    report = json.loads(output.read_text())
    scenario = report["scenarios"][0]
    assert scenario["successful_state_observations"].get("probe", 0) == 0
    assert scenario["successful_state_observations"]["final"] == 1
    assert scenario["operator_report"] is not None
    assert scenario["observed_peak_active_operations"] is None
    assert scenario["measurement_complete"] is False
    assert report["measurement_complete"] is False and exit_code == 1


@pytest.mark.parametrize("probe_delay", [0.25, 1.0])
def test_shutdown_waits_for_observed_health_probe_before_intervention(
    tmp_path: Path, probe_delay: float
) -> None:
    """Delay local RSS sampling while real HTTP work starts, retaining the race receipt."""
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "delayed-health-shutdown.json"
    script = """
import runpy
import sys
import time
namespace = runpy.run_path('scripts/measure_hosted_capacity.py')
main = namespace['main']
original = namespace['_sample_rss']
calls = 0
probe_delay = float(sys.argv[2])

def delayed_sample(pid):
    '''Model a slow local process sampler before the first measured health request.'''
    global calls
    calls += 1
    if calls == 2:
        time.sleep(probe_delay)
    return original(pid)

main.__globals__['_sample_rss'] = delayed_sample
sys.argv = ['measure', '--output', sys.argv[1], '--users', '1', '--chunks', '10',
            '--requests-per-user', '1', '--provider-delay', '0.2', '--scenarios', 'shutdown']
raise SystemExit(main())
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(output), str(probe_delay)],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    report = json.loads(output.read_text())
    scenario = report["scenarios"][0]
    assert scenario["active_work_observed_before_intervention"] is True
    if probe_delay < 0.5:
        assert process.returncode == 0, process.stderr
        assert scenario["intervention_started_with_active_work"] is True
        assert scenario["cheap_probe_outcomes"].get("health_200", 0) > 0, scenario
        assert scenario["intervention_probe_observed"] is True
    else:
        assert process.returncode == 1, process.stderr
        assert scenario["intervention_started_with_active_work"] is False
        assert scenario["intervention"] == "SIGTERM_after_client_task_completed"
        assert report["measurement_complete"] is False
    assert report["live_nvidia_calls"] == 0 and report["production_capacity_verified"] is False


def test_probe_before_work_cant_satisfy_active_intervention_evidence(tmp_path: Path) -> None:
    """Coordinate actual HTTP so the only healthy probe finishes before work starts."""
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "healthy-probe-before-work.json"
    script = """
import asyncio
import runpy
import sys
import time
namespace = runpy.run_path('scripts/measure_hosted_capacity.py')
main = namespace['main']
original_rpc = namespace['_rpc']
original_sample = namespace['_sample_rss']
original_measure = namespace['_measure']
ready = None
loop = None
sample_calls = 0
tool_calls = 0

async def coordinated_measure(*args):
    '''Set the event loop used by the real process sampler's thread.'''
    global ready, loop
    ready = asyncio.Event()
    loop = asyncio.get_running_loop()
    return await original_measure(*args)

async def coordinated_rpc(*args, **kwargs):
    '''Start expensive work only after the first complete measured probe.'''
    global tool_calls
    if not kwargs.get('discovery', False):
        tool_calls += 1
        if tool_calls > 1:
            await ready.wait()
    return await original_rpc(*args, **kwargs)

def coordinated_sample(pid):
    '''Delay the second probe until actual work has already completed.'''
    global sample_calls
    sample_calls += 1
    if sample_calls == 3:
        loop.call_soon_threadsafe(ready.set)
        time.sleep(1.0)
    return original_sample(pid)

main.__globals__.update(_rpc=coordinated_rpc, _sample_rss=coordinated_sample,
                        _measure=coordinated_measure)
sys.argv = ['measure', '--output', sys.argv[1], '--users', '1', '--chunks', '10',
            '--requests-per-user', '1', '--provider-delay', '0.2', '--scenarios', 'shutdown']
raise SystemExit(main())
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(output)],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    report = json.loads(output.read_text())
    scenario = report["scenarios"][0]
    assert scenario["cheap_probe_outcomes"].get("health_200", 0) > 0
    assert scenario["active_work_observed_before_intervention"] is True
    assert process.returncode == 1, scenario
    assert scenario["intervention_probe_observed"] is False
    assert report["measurement_complete"] is False


def test_completed_request_cant_reuse_an_earlier_active_snapshot(tmp_path: Path) -> None:
    """Delay delivery of a real active SQLite snapshot until the actual HTTP request ends."""
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "completed-before-signal.json"
    script = """
import asyncio
import inspect
import runpy
import sys
from policynim.storage.operation_store import OperationStore
namespace = runpy.run_path('scripts/measure_hosted_capacity.py')
main = namespace['main']
original_measure = namespace['_measure']
original_rpc = namespace['_rpc']
original_to_thread = asyncio.to_thread
original_report = OperationStore.report
finished = None
tool_calls = 0
held = False

async def coordinated_measure(*args):
    '''Create the completion boundary without changing the actual request path.'''
    global finished
    finished = asyncio.Event()
    return await original_measure(*args)

async def coordinated_rpc(*args, **kwargs):
    '''Retain real HTTP completion before releasing the earlier state snapshot.'''
    global tool_calls
    expensive = not kwargs.get('discovery', False)
    if expensive:
        tool_calls += 1
    result = await original_rpc(*args, **kwargs)
    if expensive and tool_calls > 1:
        finished.set()
    return result

async def delayed_snapshot(function, /, *args, **kwargs):
    '''Exercise stale delivery across an awaited report without inventing its contents.'''
    global held
    caller = inspect.currentframe().f_back
    phase = caller.f_locals.get('phase') if caller else None
    result = await original_to_thread(function, *args, **kwargs)
    if (function is original_report and phase == 'intervention'
            and result.active_operations and not held):
        held = True
        await finished.wait()
    return result

main.__globals__.update(_rpc=coordinated_rpc, _measure=coordinated_measure)
asyncio.to_thread = delayed_snapshot
sys.argv = ['measure', '--output', sys.argv[1], '--users', '1', '--chunks', '10',
            '--requests-per-user', '1', '--provider-delay', '0.2', '--scenarios', 'shutdown']
raise SystemExit(main())
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(output)],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    report = json.loads(output.read_text())
    scenario = report["scenarios"][0]
    assert scenario["active_work_observed_before_intervention"] is True
    assert scenario["intervention"] == "SIGTERM_after_client_task_completed", scenario
    assert scenario["intervention_started_with_active_work"] is False
    assert scenario["client_task_completed_before_intervention"] is True
    assert process.returncode == 1 and report["measurement_complete"] is False


@pytest.mark.parametrize("transport_failure", [False, True])
def test_probe_transport_failure_isolated_while_work_remains_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport_failure: bool
) -> None:
    """Hold real SQLite work active and isolate successful health evidence from that gate."""
    import asyncio
    import inspect
    import runpy
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import httpx

    from policynim.storage.operation_store import OperationStore

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/measure_hosted_capacity.py")
    )
    measure = namespace["_measure"]
    client = AsyncMock()
    client.__aenter__.return_value = client
    health_calls = 0

    def health(*args, **kwargs) -> httpx.Response:
        """Keep readiness identical and change only measured health transport."""
        nonlocal health_calls
        health_calls += 1
        if health_calls > 1 and transport_failure:
            raise httpx.ConnectError("private-health-payload")
        return httpx.Response(200)

    client.get.side_effect = health
    monkeypatch.setattr(httpx, "AsyncClient", Mock(return_value=client))
    store = OperationStore(tmp_path / "auth.sqlite3")
    admission = store.reserve(
        principal_id="fixture", operation="policy_search", decide=lambda _: None
    )
    operation_id = admission.operation_id
    assert operation_id is not None
    process = Mock(pid=123)
    process.poll.return_value = None
    ticks = iter([0.0, 0.0, 6.0, 6.0])
    monkeypatch.setitem(
        measure.__globals__,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks), perf_counter=time.perf_counter),
    )
    original_to_thread = asyncio.to_thread
    original_report = OperationStore.report

    async def exercise() -> dict:
        """Wait for one complete probe, then expose an active intervention at the deadline."""
        probe_done = asyncio.Event()
        release_work = asyncio.Event()
        loop = asyncio.get_running_loop()
        sample_calls = 0
        work_calls = 0

        def sample(pid: int) -> int:
            """Release intervention observation only after the first probe has finished."""
            nonlocal sample_calls
            sample_calls += 1
            if sample_calls == 3:
                loop.call_soon_threadsafe(probe_done.set)
            return 1

        async def rpc(*args, discovery=False, **kwargs) -> tuple[float, str, int]:
            """Keep the request pending until the intervention without invoking a provider."""
            nonlocal work_calls
            if not discovery:
                work_calls += 1
                if work_calls > 1:
                    await release_work.wait()
            await asyncio.sleep(0)
            return 1.0, "ok", 5

        async def observed_thread(function, /, *args, **kwargs):
            """Coordinate only the report boundary; return actual SQLite aggregate facts."""
            frame = inspect.currentframe()
            caller = frame.f_back if frame else None
            if (
                function is original_report
                and caller
                and caller.f_locals.get("phase") == "intervention"
            ):
                await probe_done.wait()
            return await original_to_thread(function, *args, **kwargs)

        def signal_shutdown(signum: int) -> None:
            """Finish the actual reservation only after the measured intervention."""
            store.finish(operation_id, "completed")
            release_work.set()

        process.send_signal.side_effect = signal_shutdown
        monkeypatch.setitem(measure.__globals__, "_sample_rss", sample)
        monkeypatch.setitem(measure.__globals__, "_rpc", rpc)
        monkeypatch.setattr(asyncio, "to_thread", observed_thread)
        return await measure(
            {"port": 1234, "tokens": ["fixture"], "users": 1, "requests_per_user": 1, "delay": 0},
            tmp_path,
            process,
            "shutdown",
        )

    try:
        result = asyncio.run(exercise())
    finally:
        store.close()
    assert result["intervention_started_with_active_work"] is True
    assert result["client_task_completed_before_intervention"] is False
    assert result["intervention_probe_observed"] is (not transport_failure)
    assert result["measurement_complete"] is (not transport_failure)
    assert "private-health-payload" not in json.dumps(result)


def test_capacity_failure_diagnostics_keep_categories_without_private_payloads() -> None:
    """Report available safe failure evidence without copying caller or exception payloads."""
    import runpy

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "measure_hosted_capacity.py")
    )
    summarize = namespace["_server_failure_diagnostics"]
    sentinel = "Bearer private-capacity-token /Users/private/input prompt"
    events = [
        {
            "event": "operation.storage_failure",
            "phase": "reservation",
            "exception_class": "OperationalError",
            "sqlite_code": 5,
            "os_errno": None,
            "message": sentinel,
            "correlation_id": sentinel,
        },
        {"event": "mcp.tool", "upstream_failure_class": "admission_busy", "latency_ms": 402.5},
        {"event": "mcp.tool", "upstream_failure_class": None, "latency_ms": 10.0},
        {
            "event": "operation.storage_failure",
            "phase": sentinel,
            "exception_class": sentinel,
            "sqlite_code": sentinel,
            "os_errno": sentinel,
        },
        {"event": "mcp.tool", "upstream_failure_class": sentinel, "latency_ms": float("nan")},
    ]
    result = summarize(events)
    assert result["tool_failure_counts"] == {"admission_busy": 1, "other": 1}
    assert result["tool_failure_latency_ms"] == {"p50": 402.5, "p95": 402.5, "p99": 402.5}
    assert result["storage_failures"] == [
        {
            "phase": "reservation",
            "exception_class": "OperationalError",
            "sqlite_code": 5,
            "os_errno": None,
            "count": 1,
        },
        {
            "phase": "other",
            "exception_class": "OtherError",
            "sqlite_code": None,
            "os_errno": None,
            "count": 1,
        },
    ]
    assert result["database_lock_timing"] == "not_sampled"
    assert sentinel not in json.dumps(result)

    bounded = summarize(
        [
            {
                "event": "operation.storage_failure",
                "phase": "reservation",
                "exception_class": "OperationalError",
                "sqlite_code": code,
            }
            for code in range(50)
        ]
    )
    assert len(bounded["storage_failures"]) == 16
    assert bounded["additional_storage_failure_events"] == 34


@pytest.mark.parametrize(
    "failure",
    [
        "retry_after_limit",
        "budget_exhausted",
        "invalid_response",
        "auth",
        "endpoint_not_found",
        "endpoint_unavailable",
        "shutdown",
        "bad_request",
        "http_status",
        "unexpected",
        "interrupted",
    ],
)
def test_capacity_diagnostics_preserve_owned_provider_failure_reason(failure: str) -> None:
    """Owned provider failures remain visible without accepting arbitrary log labels."""
    import runpy

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "measure_hosted_capacity.py")
    )
    result = namespace["_server_failure_diagnostics"](
        [{"event": "mcp.tool", "upstream_failure_class": failure, "latency_ms": 5.0}]
    )
    assert result["tool_failure_counts"] == {failure: 1}
    assert result["tool_failure_latency_ms"] == {"p50": 5.0, "p95": 5.0, "p99": 5.0}


@pytest.mark.parametrize("scenario", ["baseline", "saturation", "slow-provider"])
@pytest.mark.parametrize("reservation_pattern", ["zero_active", "before_only", "paired"])
def test_load_completeness_requires_paired_probes_during_active_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    reservation_pattern: str,
) -> None:
    """Successful probes before/without actual work can't certify an active-load measurement."""
    import asyncio
    import runpy
    from unittest.mock import AsyncMock, Mock

    import httpx

    from policynim.storage.operation_store import OperationStore

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "measure_hosted_capacity.py")
    )
    measure = namespace["_measure"]
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(200)
    monkeypatch.setattr(httpx, "AsyncClient", Mock(return_value=client))
    store = OperationStore(tmp_path / "auth.sqlite3")
    operation_id = None
    if reservation_pattern != "zero_active":
        operation_id = store.reserve(
            principal_id="fixture", operation="policy_search", decide=lambda _: None
        ).operation_id
        assert operation_id is not None
    process = Mock(pid=123)
    process.poll.return_value = None

    async def exercise() -> dict:
        """Complete one probe cycle before allowing the measured request to finish."""
        probe_done = asyncio.Event()
        loop = asyncio.get_running_loop()
        sample_calls = 0
        request_calls = 0

        def sample(pid: int) -> int:
            """Signal the next cycle only after both first-cycle state observations completed."""
            nonlocal sample_calls
            sample_calls += 1
            if sample_calls == 3:
                loop.call_soon_threadsafe(probe_done.set)
            return 1

        async def rpc(*args, discovery=False, **kwargs) -> tuple[float, str, int]:
            """End real work between probe samples for the before-only negative control."""
            nonlocal request_calls
            if discovery:
                if reservation_pattern == "before_only" and operation_id is not None:
                    store.finish(operation_id, "completed")
                return 1.0, "ok", 0
            request_calls += 1
            if request_calls > 1:
                await probe_done.wait()
                if reservation_pattern == "paired" and operation_id is not None:
                    store.finish(operation_id, "completed")
            return 1.0, "ok", 5

        monkeypatch.setitem(measure.__globals__, "_sample_rss", sample)
        monkeypatch.setitem(measure.__globals__, "_rpc", rpc)
        return await measure(
            {"port": 1234, "tokens": ["fixture"], "users": 1, "requests_per_user": 1, "delay": 0},
            tmp_path,
            process,
            scenario,
        )

    try:
        result = asyncio.run(exercise())
    finally:
        store.close()
    assert result["cheap_probe_outcomes"]["ok"] > 0
    assert result["cheap_probe_outcomes"]["health_200"] > 0
    assert result["operator_report"]["active_operations"] == 0
    assert result["measurement_complete"] is (reservation_pattern == "paired")
    assert result["paired_active_work_probe_observed"] is (reservation_pattern == "paired")
