"""Real-process synthetic HTTP capacity and failure-path smoke coverage."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


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
    assert scenarios["baseline"]["outcomes"] == {"ok": 12}
    assert scenarios["baseline"]["successful_search_hit_counts"] == {"5": 12}
    assert scenarios["saturation"]["outcomes"].get("server_busy", 0) >= 1
    assert scenarios["locked-database"]["outcomes"] == {"admission_unavailable": 12}
    assert scenarios["locked-database"]["operator_report"]["retained_rows"] == 1  # warmup only
    for scenario in scenarios.values():
        assert scenario["observed_peak_active_operations"] <= 10
        assert scenario["cheap_probe_outcomes"].get("health_200", 0) > 0
        assert scenario["operator_report"]["active_operations"] == 0
        assert scenario["live_provider_usage"] is None
        assert scenario["sampled_peak_server_rss_bytes"] > 0
        assert scenario["server_exit_code"] in {0, -15}
