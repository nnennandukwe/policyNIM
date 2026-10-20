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
    assert scenarios["locked-database"]["outcomes"] == {"admission_unavailable": 12}
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
