"""Real-process synthetic HTTP capacity and failure-path smoke coverage."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


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
    scenarios = {item["scenario"]: item for item in report["scenarios"]}
    assert scenarios["disconnect"]["drain_observation"] == "terminal_outcome_observed"
    assert scenarios["disconnect"]["intervention_started_with_active_work"] is True
    assert scenarios["shutdown"]["intervention_started_with_active_work"] is True
    assert scenarios["disconnect"]["client_concurrency"] == 1
    assert scenarios["shutdown"]["client_concurrency"] == 1
    assert scenarios["baseline"]["outcomes"] == {"ok": 12}
    assert scenarios["baseline"]["successful_search_hit_counts"] == {"5": 12}
    assert scenarios["saturation"]["outcomes"].get("server_busy", 0) >= 1
    assert scenarios["locked-database"]["outcomes"] == {"admission_busy": 12}
    assert scenarios["locked-database"]["operator_report"]["retained_rows"] == 1  # warmup only
    assert scenarios["locked-database"]["provider_stage_counts"]["embed_query"] == 1
    for scenario in scenarios.values():
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
        ],
    )
    namespace = runpy.run_path(str(script))
    assert namespace["main"]() == 1
    report = json.loads(output.read_text())
    assert report["measurement_complete"] is False
    assert len(report["scenarios"]) == 2
    assert report["scenarios"][0]["measurement_complete"] is True
    failed = report["scenarios"][1]
    assert failed["measurement_complete"] is False and failed["operator_report"] is None
    assert failed["state_read_failure_counts"]["final:database_busy"] == 3
    assert failed["attempted_requests"] == 1
    assert str(tmp_path) not in output.read_text()
