"""Bounded aggregate reporting, retention and private-input telemetry regressions."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from test_grounded_mcp import generation as generation_fixture
from typer.testing import CliRunner

from policynim.errors import PolicyNIMError
from policynim.interfaces import cli, mcp
from policynim.operations import emit_operation_event
from policynim.services.operation_admission import admit_operation
from policynim.services.operational_report import inspect_operational_report
from policynim.settings import Settings
from policynim.storage.operation_store import OperationStore

generation = generation_fixture


def test_retention_preserves_active_and_current_day_usage(tmp_path: Path) -> None:
    """Pruning old terminal history can't refund today's quota or free running work."""
    store = OperationStore(tmp_path / "auth.sqlite3")
    now = time.time()
    old = now - 40 * 86400
    accepted = []
    for index in range(3):
        decision = admit_operation(
            store,
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
    decision = admit_operation(
        store,
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

    loggers = [
        logging.getLogger(mcp._HOSTED_LOGGER_NAME),
        logging.getLogger("policynim.operations"),
    ]
    for logger in loggers:
        logger.addHandler(caplog.handler)
    try:
        asyncio.run(scenario())
    finally:
        for logger in loggers:
            logger.removeHandler(caplog.handler)
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


def test_hosted_server_disables_raw_url_access_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """OAuth callback query codes can't enter Uvicorn's default raw request URL logs."""
    from unittest.mock import Mock

    import uvicorn
    from starlette.applications import Starlette

    observed = Mock()
    monkeypatch.setattr(
        uvicorn, "Server", lambda config: SimpleNamespace(run=lambda: observed(config))
    )
    mcp._run_streamable_http_app(Starlette(), host="127.0.0.1", port=8000, log_level="warning")
    assert observed.call_count == 1
    assert observed.call_args.args[0].access_log is False


def test_rejection_and_untyped_failures_have_content_free_failure_classes() -> None:
    """Failed tool events can't be grouped as successful merely because their error lacks a code."""
    from policynim.services.operation_admission import OperationAdmissionError

    assert (
        mcp._failure_class_from_error(RuntimeError("Authorization: Bearer private"))
        == "RuntimeError"
    )
    assert (
        mcp._failure_class_from_error(
            OperationAdmissionError("quota_exceeded: Retry after 00:00 UTC.")
        )
        == "quota_exceeded"
    )


def test_quota_schema_migration_preserves_accepted_allowance(tmp_path: Path) -> None:
    """Adding telemetry correlation to #98's schema can't reset a caller's daily allowance."""
    path = tmp_path / "auth.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE operation_usage (operation_id TEXT PRIMARY KEY, "
            "principal_id TEXT, operation TEXT, usage_date TEXT, outcome TEXT, "
            "process_id INTEGER, started_at REAL, finished_at REAL, owner_id TEXT)"
        )
        connection.execute(
            "INSERT INTO operation_usage VALUES "
            "('old', 'alice', 'policy_search', ?, 'completed', 1, ?, ?, 'old-owner')",
            (datetime.now(UTC).date().isoformat(), time.time(), time.time()),
        )
    store = OperationStore(path)
    assert store.usage("alice").accepted == 1
    assert (
        admit_operation(
            store,
            principal_id="alice",
            operation="policy_search",
            daily_limit=1,
            principal_limit=2,
            global_limit=10,
        ).rejection
        == "quota_exceeded"
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT correlation_id FROM operation_usage").fetchone() == (
            None,
        )
    store.close()


def test_locked_retention_rolls_back_without_removing_history(tmp_path: Path) -> None:
    """A writer lock leaves terminal history unchanged and permits a later cleanup retry."""
    store = OperationStore(tmp_path / "auth.sqlite3")
    old = time.time() - 40 * 86400
    accepted = admit_operation(
        store,
        principal_id="alice",
        operation="policy_search",
        daily_limit=5,
        principal_limit=2,
        global_limit=10,
        now=old,
    )
    assert accepted.operation_id
    store.finish(accepted.operation_id, "completed")
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE operation_usage SET finished_at = ?", (old,))
    with sqlite3.connect(store.path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError):
            store.prune(retention_days=30)
    assert OperationStore.report(store.path).retained_rows == 1
    assert store.prune(retention_days=30) == 1
    assert OperationStore.report(store.path).retained_rows == 0
    store.close()


def test_reversed_wall_timestamps_arent_reported_as_zero_latency(tmp_path: Path) -> None:
    """A clock adjustment produces an unavailable sample instead of a fabricated zero."""
    store = OperationStore(tmp_path / "auth.sqlite3")
    accepted = admit_operation(
        store,
        principal_id="alice",
        operation="policy_search",
        daily_limit=5,
        principal_limit=2,
        global_limit=10,
    )
    assert accepted.operation_id
    store.finish(accepted.operation_id, "completed")
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE operation_usage SET finished_at = started_at - 1")
    report = store.report(store.path)
    assert report.latency_clock == "persisted_utc_wall_timestamps"
    assert report.latency_sample_count == 0
    assert report.latency_p50_ms is None
    assert report.outcomes == {"completed": 1}
    store.close()


def test_unexpected_http_tool_exception_never_reaches_sdk_trace_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Redact unexpected service exceptions before the SDK's raw traceback logger sees them."""
    from starlette.testclient import TestClient
    from test_mcp_authorization import rpc

    sentinel = "Authorization: Bearer private-token C:\\private\\task /Users/private/policy"
    settings = Settings.model_validate(
        {
            "mcp_require_auth": True,
            "mcp_public_base_url": "https://policy.example",
            "mcp_bearer_tokens": ["fixture-key"],
            "beta_auth_db_path": tmp_path / "auth.sqlite3",
            "index_db_path": tmp_path / "index.sqlite3",
        }
    )
    failures: list[str | None] = []
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(mcp, "emit_operation_event", lambda **fields: diagnostics.append(fields))

    def fail(**arguments: object) -> dict[str, object]:
        """Inject a credential-bearing failure through actual HTTP dispatch."""
        raise RuntimeError(sentinel)

    def capture(event: str, **fields: object) -> None:
        """Retain only the stable failure class for the assertion."""
        value = fields.get("upstream_failure_class")
        failures.append(str(value) if value is not None else None)

    monkeypatch.setattr(mcp, "_run_policy_search", fail)
    monkeypatch.setattr(mcp, "_emit_hosted_event", capture)
    with (
        caplog.at_level(logging.INFO),
        TestClient(
            mcp._build_streamable_http_app(settings), base_url="https://policy.example"
        ) as client,
    ):
        response = rpc(
            client,
            "tools/call",
            {"name": "policy_search", "arguments": {"query": "q"}},
            token="fixture-key",
        )
    assert response.status_code == 200 and response.json()["result"]["isError"]
    assert "internal_error" in response.text and "correlation" in response.text
    assert "private-token" not in response.text and "private-token" not in caplog.text
    assert "RuntimeError" in failures

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic["event"] == "operation.unexpected_failure"
    assert diagnostic["exception_chain"] == ["RuntimeError"]
    frames = cast(list[dict[str, object]], diagnostic["internal_frames"])
    assert 1 <= len(frames) <= 8
    assert all(str(frame["module"]).startswith("policynim.") for frame in frames)
    assert all(set(frame) == {"module", "function", "line"} for frame in frames)
    assert "private-token" not in json.dumps(diagnostic)


def test_report_observes_active_generation_and_real_job_backlog(generation, tmp_path: Path) -> None:
    """Provider-free telemetry reflects the served generation and persisted application jobs."""
    from test_evaluation_jobs import job_setup, principal

    service, suite, generation_id = job_setup(generation, tmp_path)
    operations = OperationStore(service.settings.beta_auth_db_path)
    try:
        service.submit(suite, idempotency_key="report", principal=principal())
        report = inspect_operational_report(service.settings)
        assert report["index_state"] == "ready"
        assert report["active_generation"] == generation_id
        assert report["job_backlog"] == {"queued": 1}
        assert report["job_telemetry_status"] == "available"
        assert "report" not in json.dumps(report["job_backlog"])
    finally:
        operations.close()


def test_report_doesnt_claim_damaged_generation_is_ready(generation, tmp_path: Path) -> None:
    """A malformed activation pointer yields invalid readiness without guessing an identity."""
    settings, _, _ = generation
    operations = OperationStore(tmp_path / "operator.sqlite3")
    settings = settings.model_copy(
        update={"beta_auth_db_path": operations.path, "eval_workspace_dir": tmp_path / "absent"}
    )
    (settings.generations_dir / "active.json").write_text("{}")
    try:
        report = inspect_operational_report(settings)
        assert report["index_state"] == "invalid" and report["active_generation"] is None
        assert report["job_backlog"] is None
        assert report["job_telemetry_status"] == "unavailable_no_job_store"
        assert not settings.eval_workspace_dir.exists()
    finally:
        operations.close()


@pytest.mark.parametrize("fail_inspect", [False, True])
def test_report_closes_generation_adapter_after_success_or_failed_inspection(
    generation, tmp_path: Path, monkeypatch, fail_inspect: bool
) -> None:
    """A factory-owned generation lease drains even when its inspector raises unexpectedly."""
    from policynim.services import operational_report as module
    from policynim.storage.index_readiness import IndexReadinessReport

    settings, _, generation_id = generation
    operations = OperationStore(tmp_path / "operator.sqlite3")
    settings = settings.model_copy(update={"beta_auth_db_path": operations.path})
    closed: list[bool] = []
    adapter = SimpleNamespace(generation_id=generation_id, close=lambda: closed.append(True))
    monkeypatch.setattr(module, "create_index_store", lambda _: adapter)

    def inspect(_):
        """Inject at the public adapter inspector, retaining real activation validation."""
        if fail_inspect:
            raise RuntimeError("fixture inspector failed")
        return IndexReadinessReport(state="ready", chunk_count=2, compatible=True)

    monkeypatch.setattr(module, "inspect_index_readiness", inspect)
    try:
        if fail_inspect:
            with pytest.raises(RuntimeError, match="fixture inspector failed"):
                inspect_operational_report(settings)
        else:
            assert inspect_operational_report(settings)["active_generation"] == generation_id
    finally:
        operations.close()
    assert closed == [True]


@pytest.mark.parametrize("inspection_fails", [False, True])
def test_report_closes_owned_index_after_inspection(
    generation, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inspection_fails: bool
) -> None:
    """Release a generation-capable index handle on success and inspection failure."""
    from unittest.mock import Mock

    from policynim.services import operational_report
    from policynim.storage.index_readiness import IndexReadinessReport

    store = OperationStore(tmp_path / "auth.sqlite3")
    settings = generation[0].model_copy(update={"beta_auth_db_path": store.path})
    index = Mock()
    monkeypatch.setattr(operational_report, "create_index_store", lambda settings: index)
    inspect = Mock(
        side_effect=RuntimeError("inspection failed") if inspection_fails else None,
        return_value=IndexReadinessReport(state="missing"),
    )
    monkeypatch.setattr(operational_report, "inspect_index_readiness", inspect)
    if inspection_fails:
        with pytest.raises(RuntimeError, match="inspection failed"):
            inspect_operational_report(settings)
    else:
        assert inspect_operational_report(settings)["index_state"] == "missing"
    index.close.assert_called_once_with()


def test_idle_auth_database_has_zero_readonly_operations(tmp_path: Path) -> None:
    """Healthy beta state doesn't need a hosted operation before reporting zero usage."""
    from policynim.storage.auth_store import AuthStore

    path = tmp_path / "auth.sqlite3"
    AuthStore(path=path)
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    report = OperationStore.report(path)
    assert report.retained_rows == report.active_operations == report.latency_sample_count == 0
    assert report.outcomes == {} and report.latency_p95_ms is None
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
            == before
        )


def test_recovery_downtime_is_excluded_from_operation_latency(tmp_path: Path) -> None:
    """Keep crash-recovery outcomes while leaving unknown execution duration out of tails."""
    store = OperationStore(tmp_path / "auth.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.executemany(
            "INSERT INTO operation_usage (operation_id, principal_id, operation, usage_date, "
            "outcome, process_id, started_at, finished_at, owner_id) "
            "VALUES (?, 'identity', 'policy_search', '2026-01-01', ?, 1, 0, ?, 'fixture-owner')",
            [("timed", "completed", 0.1), ("crashed", "interrupted", 86400)],
        )
    report = store.report(store.path)
    assert report.outcomes == {"completed": 1, "interrupted": 1}
    assert report.latency_sample_count == 1 and report.latency_p99_ms == 100


def test_retention_backlog_is_batched_during_concurrent_admission(tmp_path: Path) -> None:
    """A large terminal backlog only removes 1000 rows per short admission transaction."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store = OperationStore(tmp_path / "auth.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.executemany(
            "INSERT INTO operation_usage (operation_id, principal_id, operation, usage_date, "
            "outcome, process_id, started_at, finished_at, owner_id) "
            "VALUES (?, 'old', 'policy_search', '2000-01-01', "
            "'completed', 1, 0, 1, 'fixture-owner')",
            [(str(index),) for index in range(6000)],
        )
    assert store.prune(retention_days=30) == 1000
    gate = Barrier(2)

    def reserve(index: int) -> None:
        """Interleave two real writer transactions against remaining retention debt."""
        gate.wait(timeout=5)
        decision = admit_operation(
            store,
            principal_id=str(index),
            operation="policy_search",
            daily_limit=10,
            principal_limit=10,
            global_limit=10,
        )
        assert decision.rejection is None

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(reserve, range(2)))
    report = store.report(store.path)
    assert report.retained_rows == 3002 and report.active_operations == 2


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("failure_errno", [errno.EROFS, errno.ENOSPC, errno.EIO, errno.EDQUOT])
def test_report_marks_inaccessible_lease_metadata_unreadable(
    generation, tmp_path: Path, monkeypatch, wrapped: bool, failure_errno: int
) -> None:
    """Storage access failure at reader admission can't masquerade as index corruption."""
    from policynim.errors import GenerationError
    from policynim.services import operational_report as module

    settings, _, _ = generation
    store = OperationStore(tmp_path / "operator.sqlite3")
    settings = settings.model_copy(update={"beta_auth_db_path": store.path})

    def reject_lease(*args, **kwargs):
        """Represent disk capacity, I/O and permission failures at reader admission."""
        error = OSError(failure_errno, "private-generation-path")
        if wrapped:
            raise GenerationError("pin", "Reader metadata isn't writable.") from error
        raise error

    monkeypatch.setattr(module.GenerationStore, "read_activation", reject_lease)
    try:
        report = inspect_operational_report(settings)
        assert report["index_state"] == "unreadable" and report["active_generation"] is None
        assert "private-generation-path" not in json.dumps(report)
    finally:
        store.close()


def test_callback_storage_error_keeps_cause_class_without_payload(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The real browser callback doesn't label unknown local failures as upstream failures."""
    from urllib.parse import parse_qs, urlsplit

    from starlette.testclient import TestClient
    from test_mcp import ControlledKeyRotationService, _self_serve_hosted_settings

    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(mcp, "emit_operation_event", lambda **fields: diagnostics.append(fields))
    sentinel = "Authorization: Bearer private-token C:\\private\\state /Users/private/state"

    class FailingAccounts(ControlledKeyRotationService):
        """Fail local account persistence after the browser completed its state handshake."""

        def complete_github_oauth(self, *, code: str):
            """Inject a private local storage exception through the actual callback route."""
            raise OSError(sentinel)

    monkeypatch.setattr(mcp, "create_beta_auth_service", lambda settings: FailingAccounts())
    with (
        caplog.at_level(logging.ERROR),
        TestClient(
            mcp._build_streamable_http_app(_self_serve_hosted_settings()),
            base_url="https://beta.example.com",
        ) as client,
    ):
        start = client.get("/auth/github/start", follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        response = client.get(
            "/auth/github/callback", params={"state": state, "code": "fixture-code"}
        )
    assert response.status_code == 502
    assert "stage=unknown, class=OSError" in caplog.text
    assert "auth storage health" in caplog.text
    assert "upstream" not in response.text and "unexpected_upstream" not in caplog.text
    assert sentinel not in caplog.text and sentinel not in response.text
    assert "private-token" not in caplog.text and "Traceback" not in caplog.text

    assert diagnostics[0]["event"] == "auth.callback_failure"
    assert any(
        frame["function"] == "github_callback"
        for frame in cast(list[dict[str, object]], diagnostics[0]["internal_frames"])
    )
    assert "private-token" not in json.dumps(diagnostics)


def test_readonly_report_separates_live_crashed_and_unknown_ownership(tmp_path: Path) -> None:
    """Observation doesn't call abandoned reservations running or mutate recovery state."""
    import subprocess
    import sys

    path = tmp_path / "auth.sqlite3"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; "
            "from policynim.storage.operation_store import OperationStore; "
            "from policynim.services.operation_admission import admit_operation; import sys; "
            "s=OperationStore(Path(sys.argv[1])); "
            "admit_operation(s,principal_id='crashed',operation='policy_search',daily_limit=5,principal_limit=5,global_limit=5)",
            str(path),
        ],
        check=True,
    )
    owners = list(path.with_name(path.name + ".owners").iterdir())
    report = OperationStore.report(path)
    assert report.active_operations == 0 and report.reserved_operations == 1
    assert report.unreconciled_operations == 1 and report.unknown_owner_operations == 0
    assert report.outcomes == {"accepted": 1}
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT outcome FROM operation_usage").fetchone()[0] == "accepted"
    assert list(path.with_name(path.name + ".owners").iterdir()) == owners
    if sys.platform != "win32":
        import fcntl

        with owners[0].open("rb") as observer:
            fcntl.flock(observer.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            assert OperationStore.report(path).unreconciled_operations == 1
    owners[0].unlink()
    report = OperationStore.report(path)
    assert report.active_operations == report.unreconciled_operations == 0
    assert report.unknown_owner_operations == report.reserved_operations == 1


@pytest.mark.parametrize("damage", ["legacy", "symlink", "broken_symlink", "corrupt"])
def test_report_preserves_generation_when_job_store_is_unavailable(generation, tmp_path, damage):
    """One damaged telemetry source can't hide independently verified generation readiness."""
    settings, _, generation_id = generation
    operations = OperationStore(tmp_path / "operator.sqlite3")
    workspace = tmp_path / "evaluations"
    path = workspace / "jobs" / "jobs.sqlite3"
    path.parent.mkdir(parents=True)
    if damage == "legacy":
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA user_version=1")
    elif damage in {"symlink", "broken_symlink"}:
        target = tmp_path / "private-target.sqlite3"
        if damage == "symlink":
            target.write_bytes(b"private-content")
        path.symlink_to(target)
    else:
        path.write_bytes(b"private-corrupt-database")
    before = path.read_bytes() if damage != "broken_symlink" else None
    settings = settings.model_copy(
        update={"beta_auth_db_path": operations.path, "eval_workspace_dir": workspace}
    )
    try:
        report = inspect_operational_report(settings)
        assert report["index_state"] == "ready"
        assert report["active_generation"] == generation_id
        assert report["job_backlog"] is None
        assert report["job_telemetry_status"] == "unavailable"
        assert (
            report["job_telemetry_reason"]
            == {
                "legacy": "job_store_version",
                "symlink": "job_store_invalid",
                "broken_symlink": "job_store_invalid",
                "corrupt": "job_store_unreadable",
            }[damage]
        )
        assert (path.read_bytes() if damage != "broken_symlink" else None) == before
        assert path.is_symlink() == (damage in {"symlink", "broken_symlink"})
        assert "private" not in json.dumps(report)
    finally:
        operations.close()


@pytest.mark.parametrize("corrupt_pointer", [False, True])
def test_operational_report_inspects_managed_generation_without_legacy_fallback(
    tmp_path: Path, corrupt_pointer: bool
) -> None:
    """Report the selected generation and refuse to call a corrupt selection ready."""
    from test_policy_generations import seed_legacy

    from policynim.services.generations import GenerationStore

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(
        update={
            "generations_dir": tmp_path / "generations",
            "beta_auth_db_path": tmp_path / "auth.sqlite3",
        }
    )
    generation_store = GenerationStore(root=settings.generations_dir, embedding=identity)
    published = generation_store.migrate(settings, index_path=index, rules_path=rules)
    operation_store = OperationStore(settings.beta_auth_db_path)
    operation_store.close()
    pointer = settings.generations_dir / "active.json"
    if corrupt_pointer:
        pointer.write_bytes(b"invalid-pointer")
    before = pointer.read_bytes()
    report = inspect_operational_report(settings)
    assert pointer.read_bytes() == before
    if corrupt_pointer:
        assert report["generation_state"] == report["index_state"] == "invalid"
        assert report["active_generation"] is None
    else:
        assert report["generation_state"] == report["index_state"] == "ready"
        assert report["active_generation"] == published.generation_id
        assert isinstance(report["indexed_chunks"], int) and report["indexed_chunks"] > 0
    assert not list((settings.generations_dir / "leases").rglob("*.lock"))


@pytest.mark.parametrize(
    "code", [sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_IOERR, sqlite3.SQLITE_READONLY]
)
def test_report_classifies_wrapped_sqlite_access_errors_as_unreadable(
    generation, tmp_path: Path, monkeypatch, code: int
) -> None:
    """Index inspection access errors stay separate from damaged generation content."""
    from policynim.errors import GenerationError
    from policynim.services import operational_report as module

    settings, _, _ = generation
    settings = settings.model_copy(update={"beta_auth_db_path": tmp_path / "operator.sqlite3"})
    OperationStore(settings.beta_auth_db_path).close()

    def inaccessible_index(*args, **kwargs):
        """Inject the typed SQLite error surfaced by the public index factory."""
        error = sqlite3.OperationalError("private-state-path")
        error.sqlite_errorcode = code
        try:
            raise error
        except sqlite3.Error as cause:
            raise GenerationError("pin", "Index couldn't be read.") from cause

    monkeypatch.setattr(module, "create_index_store", inaccessible_index)
    report = inspect_operational_report(settings)
    assert report["index_state"] == "unreadable"
    assert "private-state-path" not in json.dumps(report)
