"""Bounded aggregate reporting, retention and private-input telemetry regressions."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from typer.testing import CliRunner

from policynim.errors import PolicyNIMError
from policynim.interfaces import cli, mcp
from policynim.operations import emit_operation_event
from policynim.services.operational_report import inspect_operational_report
from policynim.settings import Settings
from policynim.storage.operation_store import OperationStore


def test_retention_preserves_active_and_current_day_usage(tmp_path: Path) -> None:
    """Pruning old terminal history can't refund today's quota or free running work."""
    store = OperationStore(tmp_path / "auth.sqlite3")
    now = time.time()
    old = now - 40 * 86400
    accepted = []
    for index in range(3):
        decision = store.admit(
            principal_id=str(index),
            operation="policy_search",
            daily_limit=5,
            principal_limit=5,
            global_limit=5,
            now=old,
        )
        assert decision.operation_id
        accepted.append(decision.operation_id)
    store.finish(accepted[0], "completed")
    store.finish(accepted[1], "failed")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE operation_usage SET finished_at = ? WHERE outcome != 'accepted'", (old,)
        )
        connection.execute(
            "UPDATE operation_usage SET usage_date = ? WHERE operation_id = ?",
            (datetime.now(UTC).date().isoformat(), accepted[1]),
        )
    assert store.prune(retention_days=30, now=now) == 1
    assert store.prune(retention_days=30, now=now) == 0
    report = OperationStore.report(store.path)
    assert report.retained_rows == 2 and report.active_operations == 1
    assert report.outcomes == {"accepted": 1, "failed": 1}


def test_report_is_read_only_and_aggregate(tmp_path: Path) -> None:
    """Reports never create missing state and omit principal/request/credential fields."""
    missing = tmp_path / "missing" / "state.sqlite3"
    settings = Settings().model_copy(
        update={"beta_auth_db_path": missing, "index_db_path": tmp_path / "absent-index.sqlite3"}
    )
    with pytest.raises(PolicyNIMError, match="No database was created"):
        inspect_operational_report(settings)
    assert not missing.parent.exists()
    store = OperationStore(tmp_path / "auth.sqlite3")
    decision = store.admit(
        principal_id="private-user-secret",
        operation="policy_search",
        daily_limit=5,
        principal_limit=5,
        global_limit=5,
        correlation_id="opaque-correlation",
    )
    assert decision.operation_id
    store.finish(decision.operation_id, "completed")
    settings = settings.model_copy(update={"beta_auth_db_path": store.path})
    report = inspect_operational_report(settings)
    assert report["provider_usage"] is None
    assert report["latency_sample_count"] == 1
    assert report["index_state"] == "missing"
    assert "private-user-secret" not in json.dumps(report)
    assert "opaque-correlation" not in json.dumps(report)
    assert decision.operation_id not in json.dumps(report)


def test_report_latency_sample_is_bounded(tmp_path: Path) -> None:
    """A large retained history still contributes at most 1000 latency samples."""
    store = OperationStore(tmp_path / "auth.sqlite3")
    today = datetime.now(UTC).date().isoformat()
    with sqlite3.connect(store.path) as connection:
        connection.executemany(
            "INSERT INTO operation_usage (operation_id, principal_id, operation, usage_date, "
            "outcome, process_id, started_at, finished_at, owner_id) "
            "VALUES (?, 'identity', 'policy_search', ?, "
            "'completed', 1, 0, ?, 'fixture-owner')",
            [(str(index), today, float(index)) for index in range(1200)],
        )
    report = store.report(store.path)
    assert report.retained_rows == 1200
    assert report.latency_sample_count == report.latency_sample_limit == 1000
    assert report.latency_p50_ms == 699000.0


def test_telemetry_hashes_request_ids_and_correlates_provider_events(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Raw tokens, paths and task text can't be smuggled into correlation labels."""
    sentinel = "Authorization: Bearer private-token C:\\private\\task /Users/private/policy"
    settings = Settings().model_copy(update={"beta_auth_db_path": tmp_path / "auth.sqlite3"})
    caplog.set_level(logging.INFO)

    def operation() -> dict[str, object]:
        """Emit a provider-double event without arguments or exception text."""
        emit_operation_event(kind="attempt", stage="embed", usage_status="unknown")
        return {"ok": True}

    async def scenario() -> None:
        """Run through the actual MCP logging wrapper and synchronous worker context."""
        executor = mcp._ToolExecutor(settings=settings)
        try:
            assert await mcp._run_logged_tool(
                "policy_search",
                operation,
                ctx=cast(mcp.Context, SimpleNamespace(request_id=sentinel)),
                executor=executor,
            ) == {"ok": True}
        finally:
            await executor.close()

    asyncio.run(scenario())
    events = [
        json.loads(record.message) for record in caplog.records if record.message.startswith('{"')
    ]
    tool = next(item for item in events if item["event"] == "mcp.tool")
    provider = next(item for item in events if item["event"] == "provider.operation")
    assert tool["correlation_id"] == provider["correlation_id"]
    assert len(tool["correlation_id"]) == 32 and len(tool["request_id"]) == 24
    assert sentinel not in caplog.text and "private-token" not in caplog.text


def test_beta_storage_exception_logs_exclude_payload(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed beta database initialization doesn't log raw exception text or traceback."""
    sentinel = "Authorization: Bearer private-token C:\\private\\state /Users/private/state"

    def fail(settings: Settings) -> None:
        """Inject a private error through the real fallback path."""
        raise RuntimeError(sentinel)

    monkeypatch.setattr(mcp, "create_beta_auth_service", fail)
    settings = Settings().model_copy(
        update={"mcp_require_auth": True, "mcp_bearer_tokens": ["fixture-key"]}
    )
    with caplog.at_level(logging.ERROR):
        assert mcp._build_beta_auth_service(settings) is None
    assert "storage_unavailable" in caplog.text
    assert sentinel not in caplog.text and "Traceback" not in caplog.text


@pytest.mark.parametrize("value", [0, -1, 366])
def test_retention_configuration_rejects_invalid_values(value: int) -> None:
    """Bound configurable retention to a positive year-or-shorter interval."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(mcp_operation_retention_days=value)


def test_retention_environment_and_operator_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented retention variable and report command use the runtime settings."""
    monkeypatch.setenv("POLICYNIM_MCP_OPERATION_RETENTION_DAYS", "7")
    assert Settings().mcp_operation_retention_days == 7
    store = OperationStore(tmp_path / "auth.sqlite3")
    settings = Settings().model_copy(
        update={"beta_auth_db_path": store.path, "index_db_path": tmp_path / "index.sqlite3"}
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    result = CliRunner().invoke(cli.app, ["beta-admin", "operations"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["retention_days"] == 7
