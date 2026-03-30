"""Tests for the hosted runtime health service."""

from __future__ import annotations

import pytest

from policynim.errors import ConfigurationError
from policynim.services import health as health_module
from policynim.services.health import RuntimeHealthService
from policynim.settings import Settings
from policynim.types import HealthCheckResult


class StubIndexStore:
    """Minimal index-store stub for health service tests."""

    def __init__(
        self,
        *,
        exists: bool = True,
        row_count: int = 1,
        exists_error: Exception | None = None,
        count_error: Exception | None = None,
    ) -> None:
        self._exists = exists
        self._row_count = row_count
        self._exists_error = exists_error
        self._count_error = count_error

    def replace(self, chunks) -> None:  # pragma: no cover - protocol filler for tests
        raise NotImplementedError

    def exists(self) -> bool:
        if self._exists_error is not None:
            raise self._exists_error
        return self._exists

    def count(self) -> int:
        if self._count_error is not None:
            raise self._count_error
        return self._row_count

    def list_chunks(self):  # pragma: no cover - protocol filler for tests
        raise NotImplementedError

    def search(self, query_embedding, *, top_k: int, domain: str | None = None):  # pragma: no cover
        raise NotImplementedError


class StaticHealthService:
    """Fixed readiness result for hosted startup validation tests."""

    def __init__(self, result: HealthCheckResult) -> None:
        self._result = result

    def check(self) -> HealthCheckResult:
        return self._result


def test_runtime_health_service_reports_ready_index() -> None:
    service = RuntimeHealthService(
        index_store=StubIndexStore(exists=True, row_count=4),
        table_name="policy_chunks",
        mcp_url="https://beta.example.com/mcp",
    )

    result = service.check()

    assert result.status == "ok"
    assert result.ready is True
    assert result.row_count == 4
    assert result.mcp_url == "https://beta.example.com/mcp"
    assert result.reason is None


def test_runtime_health_service_reports_missing_index() -> None:
    service = RuntimeHealthService(
        index_store=StubIndexStore(exists=False),
        table_name="policy_chunks",
        mcp_url=None,
    )

    result = service.check()

    assert result.status == "error"
    assert result.ready is False
    assert result.row_count == 0
    assert result.reason is not None
    assert "does not exist" in result.reason


def test_runtime_health_service_reports_empty_index() -> None:
    service = RuntimeHealthService(
        index_store=StubIndexStore(exists=True, row_count=0),
        table_name="policy_chunks",
        mcp_url=None,
    )

    result = service.check()

    assert result.status == "error"
    assert result.ready is False
    assert result.row_count == 0
    assert result.reason is not None
    assert "contains no rows" in result.reason


def test_runtime_health_service_reports_unreadable_index() -> None:
    service = RuntimeHealthService(
        index_store=StubIndexStore(count_error=OSError("permission denied")),
        table_name="policy_chunks",
        mcp_url=None,
    )

    result = service.check()

    assert result.status == "error"
    assert result.ready is False
    assert result.row_count == 0
    assert result.reason is not None
    assert result.reason == "Local index readiness could not be inspected."


def test_ensure_hosted_runtime_ready_accepts_ready_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        health_module,
        "create_runtime_health_service",
        lambda settings: StaticHealthService(
            HealthCheckResult(
                status="ok",
                ready=True,
                table_name="policy_chunks",
                row_count=4,
                mcp_url="https://beta.example.com/mcp",
                reason=None,
            )
        ),
    )

    health_module.ensure_hosted_runtime_ready(Settings())


def test_ensure_hosted_runtime_ready_raises_for_missing_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health_module,
        "create_runtime_health_service",
        lambda settings: StaticHealthService(
            HealthCheckResult(
                status="error",
                ready=False,
                table_name="policy_chunks",
                row_count=0,
                mcp_url="https://beta.example.com/mcp",
                reason="Local index table 'policy_chunks' does not exist.",
            )
        ),
    )

    with pytest.raises(ConfigurationError, match="policynim ingest"):
        health_module.ensure_hosted_runtime_ready(Settings())


def test_ensure_hosted_runtime_ready_raises_for_empty_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health_module,
        "create_runtime_health_service",
        lambda settings: StaticHealthService(
            HealthCheckResult(
                status="error",
                ready=False,
                table_name="policy_chunks",
                row_count=0,
                mcp_url="https://beta.example.com/mcp",
                reason="Local index table 'policy_chunks' exists but contains no rows.",
            )
        ),
    )

    with pytest.raises(ConfigurationError, match="contains no rows"):
        health_module.ensure_hosted_runtime_ready(Settings())


def test_ensure_hosted_runtime_ready_wraps_constructor_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = PermissionError("permission denied")

    def raise_constructor_error(settings: Settings) -> RuntimeHealthService:
        raise failure

    monkeypatch.setattr(
        health_module,
        "create_runtime_health_service",
        raise_constructor_error,
    )

    with pytest.raises(ConfigurationError, match="PermissionError: permission denied") as exc_info:
        health_module.ensure_hosted_runtime_ready(Settings())

    assert exc_info.value.__cause__ is failure
