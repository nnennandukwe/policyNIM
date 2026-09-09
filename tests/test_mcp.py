"""Tests for the MCP surface and hosted HTTP runtime."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from importlib.metadata import version
from threading import Event, Lock, get_ident

import anyio
import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient
from starlette.types import ASGIApp

from policynim.errors import (
    ConfigurationError,
    InvalidPolicyDocumentError,
    MissingIndexError,
    PolicyNIMError,
    ProviderError,
)
from policynim.interfaces import mcp as mcp_module
from policynim.services.preflight import PreflightService
from policynim.settings import Settings
from policynim.types import (
    BetaAuthDecision,
    Citation,
    EmbeddedChunk,
    GeneratedCompiledPolicyDraft,
    GeneratedPolicyConstraint,
    GeneratedPreflightDraft,
    HealthCheckResult,
    PolicyChunk,
    PolicyGuidance,
    PolicyMetadata,
    PreflightRequest,
    PreflightResult,
    RouteRequest,
    ScoredChunk,
    SearchRequest,
    SearchResult,
)


class MockPreflightService:
    """Static preflight service for MCP tests."""

    def __init__(self) -> None:
        self.closed = False

    def preflight(self, request: PreflightRequest) -> PreflightResult:
        return PreflightResult(
            task=request.task,
            domain=request.domain,
            summary="Grounded guidance for refresh-token cleanup.",
            applicable_policies=[
                PolicyGuidance(
                    policy_id="AUTH-001",
                    title="Auth Reviews",
                    rationale="Cleanup must preserve revocation semantics.",
                    citation_ids=["AUTH-1"],
                )
            ],
            implementation_guidance=["Delete only expired tokens and preserve auditability."],
            review_flags=["Do not log raw token values."],
            tests_required=["Add coverage for active-token preservation."],
            citations=[
                Citation(
                    policy_id="AUTH-001",
                    title="Auth Reviews",
                    path="policies/security/auth-review.md",
                    section="Cleanup",
                    lines="10-16",
                    chunk_id="AUTH-1",
                )
            ],
            insufficient_context=False,
        )

    def close(self) -> None:
        self.closed = True


class MockSearchService:
    """Static search service for MCP tests."""

    def __init__(self) -> None:
        self.closed = False

    def search(self, request: SearchRequest) -> SearchResult:
        return SearchResult(
            query=request.query,
            domain=request.domain,
            top_k=request.top_k,
            hits=[
                ScoredChunk(
                    chunk_id="BACKEND-1",
                    path="policies/backend/background-jobs.md",
                    section="Background Jobs > Cleanup",
                    lines="20-24",
                    text="Cleanup jobs should be idempotent and observable.",
                    policy=PolicyMetadata(
                        policy_id="JOB-001",
                        title="Background Jobs",
                        doc_type="guidance",
                        domain="backend",
                    ),
                    score=0.98,
                )
            ],
            insufficient_context=False,
        )

    def close(self) -> None:
        self.closed = True


class StaticHealthService:
    """Fixed health-check service for HTTP route tests."""

    def __init__(self, result: HealthCheckResult) -> None:
        self._result = result

    def check(self) -> HealthCheckResult:
        return self._result


class StreamableHTTPStubServer:
    """Minimal server stub for auth-wrapper tests."""

    def __init__(self, app: ASGIApp | None = None) -> None:
        self.settings = type("SettingsStub", (), {"streamable_http_path": "/mcp"})()
        self._app = app or _ok_starlette_app()
        self.run_calls: list[str] = []

    def run(self, *, transport: str) -> None:
        self.run_calls.append(transport)

    def streamable_http_app(self, **kwargs: object) -> ASGIApp:
        """Return the stub app while accepting the SDK transport options."""
        return self._app


class StaticBetaAuthService:
    """Static hosted beta auth service for MCP auth-wrapper tests."""

    def __init__(self, decision: BetaAuthDecision) -> None:
        self._decision = decision
        self.seen_tokens: list[str | None] = []

    def authenticate_api_key(self, *, token: str | None) -> BetaAuthDecision:
        self.seen_tokens.append(token)
        return self._decision


def _ok_starlette_app() -> ASGIApp:
    async def ok_endpoint(request) -> JSONResponse:
        return JSONResponse({"ok": True}, status_code=200)

    return Starlette(routes=[Route("/mcp", ok_endpoint, methods=["GET"])])


def _call_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Read the SDK's structured result from a direct tool invocation."""
    result = asyncio.run(mcp_module.mcp.call_tool(name, arguments))
    assert isinstance(result, CallToolResult)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _search_payload(payload: dict[str, object]) -> SearchResult:
    return SearchResult.model_validate(payload)


def _preflight_payload(payload: dict[str, object]) -> PreflightResult:
    return PreflightResult.model_validate(payload)


def _hosted_settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "mcp_require_auth": True,
        "mcp_bearer_tokens": ["secret-token"],
        "mcp_public_base_url": "https://beta.example.com",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def _self_serve_hosted_settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "mcp_require_auth": True,
        "mcp_bearer_tokens": [],
        "beta_signup_enabled": True,
        "beta_session_secret": "session-secret",
        "beta_github_client_id": "github-client-id",
        "beta_github_client_secret": "github-client-secret",
        "mcp_public_base_url": "https://beta.example.com",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def _stub_streamable_http_server(
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth_service: StaticBetaAuthService | None = None,
) -> None:
    monkeypatch.setattr(
        mcp_module,
        "_create_mcp_server",
        lambda settings, beta_auth_service=None: StreamableHTTPStubServer(),
    )
    monkeypatch.setattr(
        mcp_module,
        "_build_beta_auth_service",
        lambda settings: auth_service,
    )


def test_policy_preflight_returns_exact_typed_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_preflight_service",
        lambda settings: MockPreflightService(),
    )

    payload = mcp_module.policy_preflight(
        task="refresh token cleanup",
        domain="security",
        top_k=3,
    )

    assert _preflight_payload(payload) == MockPreflightService().preflight(
        PreflightRequest(task="refresh token cleanup", domain="security", top_k=3)
    )


def test_policy_search_returns_exact_typed_payload(monkeypatch) -> None:
    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: MockSearchService())

    payload = mcp_module.policy_search(
        query="background cleanup",
        domain="backend",
        top_k=2,
    )

    assert _search_payload(payload) == MockSearchService().search(
        SearchRequest(query="background cleanup", domain="backend", top_k=2)
    )


def test_policy_preflight_uses_runtime_default_top_k(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class CapturingPreflightService:
        def preflight(self, request) -> PreflightResult:
            captured["top_k"] = request.top_k
            return MockPreflightService().preflight(request)

    monkeypatch.setattr(
        mcp_module,
        "create_preflight_service",
        lambda settings: CapturingPreflightService(),
    )
    monkeypatch.setattr(mcp_module, "get_settings", lambda: Settings(default_top_k=7))

    payload = mcp_module.policy_preflight(task="refresh token cleanup")
    result = _preflight_payload(payload)

    assert captured["top_k"] == 7
    assert result.task == "refresh token cleanup"


def test_policy_search_rejects_out_of_range_top_k() -> None:
    with pytest.raises(ValueError, match="top_k must be between 1 and 20"):
        mcp_module.policy_search(query="background cleanup", top_k=21)


def test_policy_preflight_surfaces_missing_index_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_preflight_service",
        lambda settings: (_ for _ in ()).throw(MissingIndexError("Run `policynim ingest` first.")),
    )

    with pytest.raises(ToolError, match="run `policynim ingest`.*index configuration"):
        _call_tool("policy_preflight", {"task": "refresh token cleanup"})


def test_policy_preflight_formats_route_validation_errors(monkeypatch) -> None:
    class FailingPreflightService(MockPreflightService):
        def preflight(self, request: PreflightRequest) -> PreflightResult:
            RouteRequest(task=request.task, domain=request.domain, top_k=request.top_k)
            raise AssertionError("expected RouteRequest validation to fail")

    service = FailingPreflightService()
    monkeypatch.setattr(mcp_module, "create_preflight_service", lambda settings: service)

    with pytest.raises(ToolError) as exc_info:
        _call_tool("policy_preflight", {"task": "   ", "top_k": 1})

    message = str(exc_info.value)
    assert "Preflight request is invalid at task" in message
    assert "task must not be empty" in message
    assert "1 validation error" not in message
    assert "RouteRequest" not in message
    assert service.closed is True


def test_policy_search_surfaces_configuration_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_search_service",
        lambda settings: (_ for _ in ()).throw(ConfigurationError("missing NVIDIA key")),
    )

    with pytest.raises(ToolError, match="configuration is invalid.*server settings"):
        _call_tool("policy_search", {"query": "background cleanup"})


def test_run_server_uses_stdio_transport_and_runtime_host_port(monkeypatch) -> None:
    captured: dict[str, object] = {}
    server = StreamableHTTPStubServer()

    def create_server(settings: Settings) -> StreamableHTTPStubServer:
        captured["host"] = settings.mcp_host
        captured["port"] = settings.mcp_port
        return server

    monkeypatch.setattr(
        mcp_module,
        "get_settings",
        lambda: Settings(mcp_host="0.0.0.0", mcp_port=9001),
    )
    monkeypatch.setattr(mcp_module, "_create_mcp_server", create_server)

    mcp_module.run_server("stdio")

    assert captured == {"host": "0.0.0.0", "port": 9001}
    assert server.run_calls == ["stdio"]


def test_run_server_uses_streamable_http_transport(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        mcp_module,
        "get_settings",
        lambda: Settings(mcp_host="127.0.0.1", mcp_port=8010),
    )
    monkeypatch.setattr(
        mcp_module,
        "_ensure_streamable_http_port_available",
        lambda host, port: captured.setdefault("probe", (host, port)),
    )
    monkeypatch.setattr(
        mcp_module,
        "ensure_hosted_runtime_ready",
        lambda settings, *, rebuild_if_missing=False: captured.setdefault(
            "ready", rebuild_if_missing
        ),
    )
    monkeypatch.setattr(mcp_module, "_build_streamable_http_app", lambda settings: object())
    monkeypatch.setattr(
        mcp_module,
        "_run_streamable_http_app",
        lambda app, *, host, port, log_level="info": captured.setdefault(
            "run", {"host": host, "port": port, "log_level": log_level}
        ),
    )

    mcp_module.run_server("streamable-http")

    assert captured["probe"] == ("127.0.0.1", 8010)
    assert captured["ready"] is True
    assert captured["run"] == {"host": "127.0.0.1", "port": 8010, "log_level": "info"}


def test_run_server_requires_ready_index_for_hosted_http(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        mcp_module,
        "get_settings",
        lambda: Settings.model_validate(
            {
                "mcp_host": "127.0.0.1",
                "mcp_port": 8010,
                "mcp_public_base_url": "https://beta.example.com",
            }
        ),
    )
    monkeypatch.setattr(
        mcp_module,
        "_ensure_streamable_http_port_available",
        lambda host, port: captured.setdefault("probe", (host, port)),
    )
    monkeypatch.setattr(
        mcp_module,
        "ensure_hosted_runtime_ready",
        lambda settings, *, rebuild_if_missing=False: captured.setdefault(
            "ready", rebuild_if_missing
        ),
    )
    monkeypatch.setattr(mcp_module, "_build_streamable_http_app", lambda settings: object())
    monkeypatch.setattr(
        mcp_module,
        "_run_streamable_http_app",
        lambda app, *, host, port, log_level="info": captured.setdefault(
            "run", {"host": host, "port": port, "log_level": log_level}
        ),
    )

    mcp_module.run_server("streamable-http")

    assert captured["probe"] == ("127.0.0.1", 8010)
    assert captured["ready"] is True
    assert captured["run"] == {"host": "127.0.0.1", "port": 8010, "log_level": "info"}


def test_run_server_surfaces_hosted_startup_readiness_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "get_settings",
        lambda: Settings.model_validate(
            {
                "mcp_host": "127.0.0.1",
                "mcp_port": 8010,
                "mcp_public_base_url": "https://beta.example.com",
            }
        ),
    )
    monkeypatch.setattr(
        mcp_module,
        "_ensure_streamable_http_port_available",
        lambda host, port: None,
    )
    monkeypatch.setattr(
        mcp_module,
        "ensure_hosted_runtime_ready",
        lambda settings, *, rebuild_if_missing=False: (_ for _ in ()).throw(
            ConfigurationError("Hosted streamable-http startup requires a populated local index.")
        ),
    )
    monkeypatch.setattr(
        mcp_module,
        "_build_streamable_http_app",
        lambda settings: pytest.fail("HTTP app should not be built when hosted startup fails"),
    )

    with pytest.raises(ConfigurationError, match="populated local index"):
        mcp_module.run_server("streamable-http")


def test_streamable_http_port_probe_rejects_in_use_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        host, port = listener.getsockname()

        with pytest.raises(ConfigurationError, match="POLICYNIM_MCP_PORT"):
            mcp_module._ensure_streamable_http_port_available(host, port)


def test_run_server_surfaces_streamable_http_port_conflicts(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "get_settings",
        lambda: Settings(mcp_host="127.0.0.1", mcp_port=8000),
    )
    monkeypatch.setattr(
        mcp_module,
        "_ensure_streamable_http_port_available",
        lambda host, port: (_ for _ in ()).throw(
            ConfigurationError("Could not start streamable-http MCP server on 127.0.0.1:8000.")
        ),
    )
    monkeypatch.setattr(
        mcp_module,
        "_build_streamable_http_app",
        lambda settings: pytest.fail("HTTP app should not be built when the port probe fails"),
    )

    with pytest.raises(ConfigurationError, match="streamable-http MCP server"):
        mcp_module.run_server("streamable-http")


def test_mcp_registers_both_public_tools() -> None:
    tools = asyncio.run(mcp_module.mcp.list_tools())
    assert {tool.name for tool in tools} == {"policy_preflight", "policy_search"}


def test_mcp_describes_typed_results_and_bounded_inputs() -> None:
    """Advertise usable schemas and the application identity to MCP clients."""

    async def inspect() -> None:
        """Inspect discovery through the SDK client rather than private fields."""
        server = mcp_module._register_tools(mcp_module._new_mcp_server())
        async with Client(server) as client:
            discovered = await client.list_tools()
            by_name = {tool.name: tool for tool in discovered.tools}
            for name, result_field in (
                ("policy_search", "hits"),
                ("policy_preflight", "citations"),
            ):
                tool = by_name[name]
                assert tool.description
                assert tool.output_schema is not None
                assert result_field in tool.output_schema["properties"]
                assert tool.annotations is not None
                assert tool.annotations.read_only_hint is True
                assert tool.annotations.destructive_hint is False
                choices = tool.input_schema["properties"]["top_k"]["anyOf"]
                assert any(
                    choice.get("minimum") == 1 and choice.get("maximum") == 20 for choice in choices
                )
        assert server.version == version("policynim")

    asyncio.run(inspect())


async def _wait_until_set(event: Event) -> None:
    """Bound fixture synchronization without consuming a worker thread."""

    async def wait() -> None:
        """Yield while the worker reaches the required fixture boundary."""
        while not event.is_set():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=5)


def test_mcp_rejects_eleventh_operation_before_provider_construction(monkeypatch) -> None:
    """Ten active operations share one immediate gate across both public tools."""
    all_started = Event()
    release = Event()
    lock = Lock()
    factory_threads: list[int] = []
    closed = 0

    class BlockingSearchService(MockSearchService):
        def search(self, request: SearchRequest) -> SearchResult:
            """Block provider work until the admission assertions finish."""
            assert release.wait(5), "test did not release blocked provider work"
            return super().search(request)

        def close(self) -> None:
            """Count cleanup while still executing in the owned worker."""
            nonlocal closed
            with lock:
                closed += 1
            super().close()

    def build_service(settings: Settings) -> BlockingSearchService:
        """Record provider construction before waiting inside its operation."""
        with lock:
            factory_threads.append(get_ident())
            if len(factory_threads) == 10:
                all_started.set()
        return BlockingSearchService()

    monkeypatch.setattr(mcp_module, "create_search_service", build_service)
    monkeypatch.setattr(
        mcp_module,
        "create_runtime_health_service",
        lambda settings: StaticHealthService(
            HealthCheckResult(status="ok", ready=True, table_name="policy_chunks", row_count=1)
        ),
    )
    monkeypatch.setattr(
        mcp_module,
        "create_preflight_service",
        lambda settings: pytest.fail("Rejected preflight must not construct providers"),
    )

    async def exercise() -> None:
        """Exercise admission and responsiveness against registered handlers."""
        server = mcp_module._create_mcp_server(Settings(mcp_max_concurrent_operations=10))
        app = server.streamable_http_app()
        requests = [
            asyncio.create_task(server.call_tool("policy_search", {"query": str(index)}))
            for index in range(10)
        ]
        try:
            await _wait_until_set(all_started)
            assert all(thread != get_ident() for thread in factory_threads)
            assert len(await asyncio.wait_for(server.list_tools(), timeout=0.5)) == 2
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost"
            ) as client:
                health = await asyncio.wait_for(client.get("/healthz"), timeout=0.5)
                assert health.status_code == 200
                assert health.json()["ready"] is True
            with pytest.raises(ToolError, match="server_busy.*Retry later"):
                await asyncio.wait_for(
                    server.call_tool("policy_preflight", {"task": "blocked"}), timeout=0.5
                )
            assert len(factory_threads) == 10
        finally:
            release.set()
            results = await asyncio.gather(*requests)
        assert all(isinstance(result, CallToolResult) for result in results)
        assert closed == 10
        await server.call_tool("policy_search", {"query": "recovered"})
        assert closed == 11

    asyncio.run(exercise())


def test_exported_mcp_resolves_configured_capacity_once_on_first_tool_call(tmp_path) -> None:
    """Import stays configuration-free and the exported server honors its first runtime limit."""
    config_file = tmp_path / "offline.env"
    config_file.write_text("", encoding="utf-8")
    script = textwrap.dedent(
        """
        import asyncio
        import os
        from threading import Event
        from unittest.mock import patch

        from mcp.server.mcpserver.exceptions import ToolError
        from policynim import settings
        from policynim.types import SearchResult

        with patch.object(settings, 'get_settings', side_effect=AssertionError('import settings')):
            from policynim.interfaces import mcp as module
        module.get_settings = settings.get_settings
        os.environ['POLICYNIM_MCP_MAX_CONCURRENT_OPERATIONS'] = '1'
        started = Event()
        release = Event()
        created = []

        class Service:
            def search(self, request):
                '''Block only the first admitted operation.'''
                if request.query == 'first':
                    started.set()
                    assert release.wait(5)
                return SearchResult(query=request.query, top_k=request.top_k, hits=[])

            def close(self):
                '''The fixture owns no external client.'''
                pass

        def create_service(runtime_settings):
            '''Track the effective settings when each service is allocated.'''
            created.append(runtime_settings.mcp_max_concurrent_operations)
            return Service()

        async def exercise():
            '''Keep the first configured admission limit across settings reloads.'''
            running = asyncio.create_task(module.mcp.call_tool('policy_search', {'query': 'first'}))
            try:
                async with asyncio.timeout(5):
                    while not started.is_set():
                        await asyncio.sleep(0.001)
                os.environ['POLICYNIM_MCP_MAX_CONCURRENT_OPERATIONS'] = '2'
                settings.get_settings.cache_clear()
                try:
                    await module.mcp.call_tool('policy_search', {'query': 'excess'})
                except ToolError as error:
                    assert 'server_busy' in str(error)
                else:
                    raise AssertionError('exported server ignored its first configured capacity')
                assert created == [1]
            finally:
                release.set()
                await running
            await module.mcp.call_tool('policy_search', {'query': 'recovered'})
            assert created == [1, 2]

        with patch.object(module, 'create_search_service', create_service):
            asyncio.run(exercise())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "POLICYNIM_CONFIG_FILE": str(config_file)},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("cancellation_kind", ["asyncio", "anyio"])
@pytest.mark.parametrize("worker_outcome", ["success", "operation_failure", "cleanup_failure"])
def test_mcp_cancellation_retains_slot_until_cleanup_finishes(
    monkeypatch, cancellation_kind, worker_outcome
) -> None:
    """Cancellation drains work and cleanup, propagates, and records no success event."""
    started = Event()
    release_work = Event()
    cleanup_started = Event()
    release_cleanup = Event()
    service_threads: list[int] = []
    events: list[dict[str, object]] = []

    class BlockingSearchService(MockSearchService):
        def search(self, request: SearchRequest) -> SearchResult:
            """Expose a cancellable request whose underlying work is synchronous."""
            service_threads.append(get_ident())
            started.set()
            assert release_work.wait(5), "test did not release provider work"
            if worker_outcome == "operation_failure":
                raise ValueError("private-operation-failure")
            return super().search(request)

        def close(self) -> None:
            """Keep the slot occupied through deterministic client cleanup."""
            service_threads.append(get_ident())
            cleanup_started.set()
            assert release_cleanup.wait(5), "test did not release cleanup"
            super().close()
            if worker_outcome == "cleanup_failure":
                raise ValueError("private-cleanup-failure")

    service = BlockingSearchService()
    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: service)
    monkeypatch.setattr(
        mcp_module,
        "_emit_hosted_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )

    async def exercise() -> None:
        """Cancel using both native asyncio and the SDK's structured scopes."""
        server = mcp_module._create_mcp_server(Settings(mcp_max_concurrent_operations=1))
        scopes: list[anyio.CancelScope] = []
        cancellations: list[bool] = []
        results: list[object] = []

        async def call() -> None:
            """Record whether the handler propagated cancellation or returned a result."""
            try:
                results.append(await server.call_tool("policy_search", {"query": "first"}))
            except asyncio.CancelledError:
                cancellations.append(True)
                raise

        async def invoke() -> None:
            """Use the cancellation mechanism selected for this regression."""
            if cancellation_kind == "anyio":
                with anyio.CancelScope() as scope:
                    scopes.append(scope)
                    await call()
            else:
                await call()

        request = asyncio.create_task(invoke())
        try:
            await _wait_until_set(started)
            if cancellation_kind == "anyio":
                scopes[0].cancel()
            else:
                request.cancel()
            await asyncio.sleep(0)
            assert not request.done()
            with pytest.raises(ToolError, match="server_busy"):
                await server.call_tool("policy_search", {"query": "during work"})
            release_work.set()
            await _wait_until_set(cleanup_started)
            with pytest.raises(ToolError, match="server_busy"):
                await server.call_tool("policy_search", {"query": "during cleanup"})
            assert not any(event["upstream_failure_class"] == "cancelled" for event in events)
            events.clear()
        finally:
            release_work.set()
            release_cleanup.set()
            await asyncio.gather(request, return_exceptions=True)
        assert service.closed
        assert len(set(service_threads)) == 1
        assert cancellations == [True]
        assert results == []
        if cancellation_kind == "asyncio":
            assert request.cancelled()
        else:
            assert scopes[0].cancelled_caught
        assert len(events) == 1
        assert events[0]["event"] == "mcp.tool"
        assert events[0]["tool_name"] == "policy_search"
        assert events[0]["upstream_failure_class"] == "cancelled"
        assert "private-" not in str(events)
        monkeypatch.setattr(
            mcp_module, "create_search_service", lambda settings: MockSearchService()
        )
        await server.call_tool("policy_search", {"query": "after cleanup"})

    asyncio.run(exercise())


def test_mcp_sanitizes_unexpected_failure_and_releases_capacity(monkeypatch) -> None:
    """A crash cannot leak its message or keep subsequent operations blocked."""

    class CrashingSearchService(MockSearchService):
        def search(self, request: SearchRequest) -> SearchResult:
            """Fail unexpectedly with text that must never reach a client."""
            raise ValueError("private-provider-secret")

    service = CrashingSearchService()
    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: service)

    async def exercise() -> None:
        """Verify wire-style tool errors and subsequent reuse of the only slot."""
        server = mcp_module._register_tools(mcp_module._new_mcp_server(), capacity=1)
        async with Client(server) as client:
            result = await client.call_tool("policy_search", {"query": "fail"})
            assert isinstance(result, CallToolResult)
            assert result.is_error is True
            assert "private-provider-secret" not in result.model_dump_json()
            assert "Error executing tool policy_search" in result.model_dump_json()
            assert service.closed
            monkeypatch.setattr(
                mcp_module, "create_search_service", lambda settings: MockSearchService()
            )
            result = await client.call_tool("policy_search", {"query": "recover"})
            assert isinstance(result, CallToolResult)
            assert not result.is_error

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("error_type", "expected_guidance"),
    [
        (MissingIndexError, "run `policynim ingest`"),
        (InvalidPolicyDocumentError, "validate the policy corpus"),
        (ConfigurationError, "check the server settings"),
        (ProviderError, "inspect provider health"),
        (PolicyNIMError, "inspect the server logs"),
    ],
)
def test_mcp_sanitizes_domain_errors(monkeypatch, error_type, expected_guidance) -> None:
    """Expected error classes do not make arbitrary messages safe for remote callers."""
    private_message = "/private/operator/policies token=provider-secret"

    def fail_construction(settings: Settings) -> MockSearchService:
        """Raise a domain error carrying representative sensitive context."""
        raise error_type(private_message)

    monkeypatch.setattr(mcp_module, "create_search_service", fail_construction)

    async def exercise() -> None:
        """Inspect only client-visible error content after SDK serialization."""
        server = mcp_module._register_tools(mcp_module._new_mcp_server())
        async with Client(server) as client:
            result = await client.call_tool("policy_search", {"query": "policy question"})
        assert isinstance(result, CallToolResult)
        assert result.is_error is True
        serialized = result.model_dump_json()
        assert expected_guidance in serialized
        assert "/private" not in serialized
        assert "provider-secret" not in serialized

    asyncio.run(exercise())


def test_call_tool_runs_minimal_stdio_path(monkeypatch) -> None:
    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: MockSearchService())

    payload = _call_tool("policy_search", {"query": "background cleanup", "top_k": 1})
    result = _search_payload(payload)

    assert result.query == "background cleanup"
    assert result.hits[0].chunk_id == "BACKEND-1"


def test_call_tool_logs_structured_event_on_success(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: MockSearchService())
    monkeypatch.setattr(
        mcp_module,
        "_emit_hosted_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )

    payload = _call_tool("policy_search", {"query": "background cleanup", "top_k": 1})
    result = _search_payload(payload)

    assert result.query == "background cleanup"
    assert events == [
        {
            "event": "mcp.tool",
            "auth_result": "not_required",
            "tool_name": "policy_search",
            "latency_ms": events[0]["latency_ms"],
            "upstream_failure_class": None,
            "request_id": None,
        }
    ]
    assert isinstance(events[0]["latency_ms"], float)
    assert events[0]["latency_ms"] >= 0


def test_policy_search_closes_service_after_tool_call(monkeypatch) -> None:
    service = MockSearchService()
    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: service)

    payload = mcp_module.policy_search(query="background cleanup", top_k=1)
    result = _search_payload(payload)

    assert result.query == "background cleanup"
    assert service.closed is True


def test_policy_preflight_closes_service_when_tool_raises(monkeypatch) -> None:
    class FailingPreflightService(MockPreflightService):
        def preflight(self, request: PreflightRequest) -> PreflightResult:
            raise MissingIndexError("Run `policynim ingest` first.")

    service = FailingPreflightService()
    monkeypatch.setattr(mcp_module, "create_preflight_service", lambda settings: service)

    with pytest.raises(MissingIndexError, match="Run `policynim ingest` first"):
        mcp_module.policy_preflight(task="refresh token cleanup")

    assert service.closed is True


def test_call_tool_logs_failure_class_when_tool_raises(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    class FailingSearchService:
        def search(self, request: SearchRequest) -> SearchResult:
            raise ProviderError("upstream timeout", failure_class="timeout")

    monkeypatch.setattr(
        mcp_module,
        "create_search_service",
        lambda settings: FailingSearchService(),
    )
    monkeypatch.setattr(
        mcp_module,
        "_emit_hosted_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )

    with pytest.raises(ToolError, match="provider timed out.*Retry later"):
        _call_tool("policy_search", {"query": "background cleanup", "top_k": 1})

    assert events == [
        {
            "event": "mcp.tool",
            "auth_result": "not_required",
            "tool_name": "policy_search",
            "latency_ms": events[0]["latency_ms"],
            "upstream_failure_class": "timeout",
            "request_id": None,
        }
    ]
    assert isinstance(events[0]["latency_ms"], float)
    assert events[0]["latency_ms"] >= 0


def test_call_tool_logs_failure_class_when_policy_preflight_generator_times_out(
    monkeypatch,
) -> None:
    events: list[dict[str, object]] = []

    class StaticEmbedder:
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    class StaticIndexStore:
        def exists(self) -> bool:
            return True

        def count(self) -> int:
            return 1

        def search(
            self,
            query_embedding: Sequence[float],
            *,
            top_k: int,
            domain: str | None = None,
        ) -> list[ScoredChunk]:
            return [
                ScoredChunk(
                    chunk_id="AUTH-1",
                    path="policies/security/auth-review.md",
                    section="Cleanup",
                    lines="10-16",
                    text="Cleanup must preserve revocation semantics.",
                    policy=PolicyMetadata(
                        policy_id="AUTH-001",
                        title="Auth Reviews",
                        doc_type="guidance",
                        domain="security",
                    ),
                    score=0.99,
                )
            ]

        def replace(
            self,
            chunks: Sequence[EmbeddedChunk],
        ) -> None:  # pragma: no cover - protocol filler for tests
            raise NotImplementedError

        def list_chunks(self) -> list[PolicyChunk]:  # pragma: no cover - protocol filler
            return []

    class StaticReranker:
        def rerank(
            self,
            query: str,
            candidates: Sequence[ScoredChunk],
            *,
            top_k: int,
        ) -> list[ScoredChunk]:
            return list(candidates)[:top_k]

    class TimeoutGenerator:
        def generate_preflight(
            self,
            request: PreflightRequest,
            context: Sequence[ScoredChunk],
            *,
            compiled_packet=None,
            regeneration_context=None,
        ) -> GeneratedPreflightDraft:
            del compiled_packet
            del regeneration_context
            raise ProviderError("upstream timeout", failure_class="timeout")

    class StaticCompiler:
        def compile_policy_packet(self, request, selection_packet, context):
            return GeneratedCompiledPolicyDraft(
                required_steps=[
                    GeneratedPolicyConstraint(
                        statement="Use the retained auth policy.",
                        citation_ids=["AUTH-1"],
                    )
                ]
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        mcp_module,
        "create_preflight_service",
        lambda settings: PreflightService(
            embedder=StaticEmbedder(),
            index_store=StaticIndexStore(),
            reranker=StaticReranker(),
            generator=TimeoutGenerator(),
            compiler=StaticCompiler(),
        ),
    )
    monkeypatch.setattr(
        mcp_module,
        "_emit_hosted_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )

    with pytest.raises(ToolError, match="provider timed out.*Retry later"):
        _call_tool("policy_preflight", {"task": "refresh token cleanup", "top_k": 1})

    assert events == [
        {
            "event": "mcp.tool",
            "auth_result": "not_required",
            "tool_name": "policy_preflight",
            "latency_ms": events[0]["latency_ms"],
            "upstream_failure_class": "timeout",
            "request_id": None,
        }
    ]
    assert isinstance(events[0]["latency_ms"], float)
    assert events[0]["latency_ms"] >= 0


def test_healthz_returns_ready_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
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

    app = mcp_module._build_streamable_http_app(
        Settings.model_validate({"mcp_public_base_url": "https://beta.example.com"})
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["mcp_url"] == "https://beta.example.com/mcp"
    assert "index_uri" not in response.json()


def test_healthz_returns_not_ready_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_runtime_health_service",
        lambda settings: StaticHealthService(
            HealthCheckResult(
                status="error",
                ready=False,
                table_name="policy_chunks",
                row_count=0,
                mcp_url=None,
                reason="Local index table 'policy_chunks' exists but contains no rows.",
            )
        ),
    )

    app = mcp_module._build_streamable_http_app(Settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert "contains no rows" in response.json()["reason"]
    assert "index_uri" not in response.json()


def test_healthz_stays_public_when_auth_is_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_runtime_health_service",
        lambda settings: StaticHealthService(
            HealthCheckResult(
                status="ok",
                ready=True,
                table_name="policy_chunks",
                row_count=1,
                mcp_url="https://beta.example.com/mcp",
                reason=None,
            )
        ),
    )

    app = mcp_module._build_streamable_http_app(_hosted_settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert "index_uri" not in response.json()


def test_healthz_returns_fallback_payload_when_service_construction_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_runtime_health_service",
        lambda settings: (_ for _ in ()).throw(OSError("permission denied")),
    )

    app = mcp_module._build_streamable_http_app(
        Settings.model_validate({"mcp_public_base_url": "https://beta.example.com"})
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["ready"] is False
    assert payload["reason"] == "Local index readiness could not be inspected: OSError."
    assert payload["mcp_url"] == "https://beta.example.com/mcp"
    assert "index_uri" not in payload


def test_healthz_returns_fallback_payload_when_probe_fails(monkeypatch) -> None:
    """Return a sanitized public fallback reason when health checks raise later."""

    class FailingHealthService:
        def check(self) -> HealthCheckResult:
            """Raise a representative unexpected health-check failure."""
            raise RuntimeError("unexpected readiness failure")

    monkeypatch.setattr(
        mcp_module,
        "create_runtime_health_service",
        lambda settings: FailingHealthService(),
    )

    app = mcp_module._build_streamable_http_app(
        Settings.model_validate({"mcp_public_base_url": "https://beta.example.com"})
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["ready"] is False
    assert payload["reason"] == ("Local index readiness could not be inspected: RuntimeError.")
    assert payload["mcp_url"] == "https://beta.example.com/mcp"
    assert "index_uri" not in payload


def test_healthz_constructs_service_once_and_runs_check_off_thread(monkeypatch) -> None:
    factory_calls = 0
    check_calls = 0

    class CountingHealthService:
        def check(self) -> HealthCheckResult:
            nonlocal check_calls
            check_calls += 1
            return HealthCheckResult(
                status="ok",
                ready=True,
                table_name="policy_chunks",
                row_count=1,
                mcp_url=None,
                reason=None,
            )

    def build_service(settings) -> CountingHealthService:
        nonlocal factory_calls
        factory_calls += 1
        return CountingHealthService()

    async def mock_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(mcp_module, "create_runtime_health_service", build_service)
    monkeypatch.setattr(mcp_module.asyncio, "to_thread", mock_to_thread)

    app = mcp_module._build_streamable_http_app(Settings())

    with TestClient(app, base_url="http://localhost") as client:
        first = client.get("/healthz")
        second = client.get("/healthz")

    assert first.status_code == 200
    assert second.status_code == 200
    assert factory_calls == 1
    assert check_calls == 2


def test_streamable_http_app_keeps_mcp_open_when_auth_disabled(monkeypatch) -> None:
    _stub_streamable_http_server(monkeypatch)

    app = mcp_module._build_streamable_http_app(Settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/mcp")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("secret", [None, "", "   "])
def test_streamable_http_app_requires_non_empty_session_secret_without_settings_validation(
    monkeypatch: pytest.MonkeyPatch,
    secret: str | None,
) -> None:
    """Guard hosted beta startup even when settings validation is bypassed."""
    _stub_streamable_http_server(monkeypatch)

    settings = Settings.model_construct(
        beta_signup_enabled=True,
        beta_session_secret=secret,
        mcp_require_auth=True,
        mcp_public_base_url="https://beta.example.com",
    )

    with pytest.raises(ConfigurationError, match="POLICYNIM_BETA_SESSION_SECRET"):
        mcp_module._build_streamable_http_app(settings)


def test_streamable_http_app_rejects_missing_bearer_token(monkeypatch) -> None:
    _stub_streamable_http_server(monkeypatch)

    app = mcp_module._build_streamable_http_app(_hosted_settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/mcp")

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized."}


def test_streamable_http_app_redirects_browser_mcp_visits_to_beta_portal(monkeypatch) -> None:
    """Route humans who paste the hosted MCP URL toward token creation."""
    _stub_streamable_http_server(monkeypatch)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app, base_url="https://beta.example.com") as client:
        response = client.get(
            "/mcp",
            headers={"accept": "text/html"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "https://beta.example.com/beta"


def test_streamable_http_app_keeps_bad_bearer_header_json_for_browser_accept(
    monkeypatch,
) -> None:
    """Do not hide broken MCP client auth behind the human onboarding redirect."""
    _stub_streamable_http_server(monkeypatch)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app, base_url="https://beta.example.com") as client:
        response = client.get(
            "/mcp",
            headers={"accept": "text/html", "authorization": "Token wrong"},
            follow_redirects=False,
        )

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized."}


def test_streamable_http_app_logs_auth_rejection(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    _stub_streamable_http_server(monkeypatch)
    monkeypatch.setattr(
        mcp_module,
        "_emit_hosted_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )

    app = mcp_module._build_streamable_http_app(_hosted_settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/mcp")

    assert response.status_code == 401
    assert events == [
        {
            "event": "mcp.auth",
            "auth_result": "unauthorized",
            "tool_name": None,
            "latency_ms": None,
            "upstream_failure_class": None,
            "request_id": None,
        }
    ]


def test_streamable_http_app_rejects_malformed_bearer_header(monkeypatch) -> None:
    _stub_streamable_http_server(monkeypatch)

    app = mcp_module._build_streamable_http_app(_hosted_settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/mcp", headers={"Authorization": "Token secret-token"})

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized."}


def test_streamable_http_app_rejects_invalid_bearer_token(monkeypatch) -> None:
    auth_service = StaticBetaAuthService(BetaAuthDecision(status="unauthorized"))
    _stub_streamable_http_server(monkeypatch, auth_service=auth_service)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer wrong-token"})

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized."}
    assert auth_service.seen_tokens == ["wrong-token"]


def test_streamable_http_app_accepts_valid_bearer_token(monkeypatch) -> None:
    _stub_streamable_http_server(monkeypatch)

    app = mcp_module._build_streamable_http_app(_hosted_settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("path", ["/gateway/mcp", "/gateway/mcp/"])
@pytest.mark.parametrize("deployment", ["root_path", "mount"])
def test_prefixed_hosting_is_rejected_before_auth_or_provider_work(
    monkeypatch: pytest.MonkeyPatch, path: str, deployment: str
) -> None:
    """Proxy prefixes and mounts fail closed because all hosted URLs assume an origin root."""
    services: list[MockSearchService] = []
    auth_service = StaticBetaAuthService(BetaAuthDecision(status="authorized", source="api_key"))

    def create_service(settings: Settings) -> MockSearchService:
        """Record provider construction only after the complete boundary admits a call."""
        service = MockSearchService()
        services.append(service)
        return service

    settings = _hosted_settings()
    monkeypatch.setattr(mcp_module, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp_module, "create_search_service", create_service)
    monkeypatch.setattr(mcp_module, "_build_beta_auth_service", lambda settings: auth_service)
    server = mcp_module._create_mcp_server(settings)
    monkeypatch.setattr(
        mcp_module, "_create_mcp_server", lambda settings, beta_auth_service=None: server
    )
    app = mcp_module._build_streamable_http_app(settings)
    root_path = "/gateway"
    if deployment == "mount":

        @asynccontextmanager
        async def mounted_lifespan(application: Starlette) -> AsyncIterator[None]:
            """Start the SDK session manager on the enclosing app's event loop."""
            async with server.session_manager.run():
                yield

        app = Starlette(routes=[Mount(root_path, app=app)], lifespan=mounted_lifespan)
        root_path = ""

    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "policy_search",
    }
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "policy_search",
            "arguments": {"query": "background cleanup"},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    with TestClient(app, base_url="http://localhost", root_path=root_path) as client:
        for authorization in (
            None,
            "Token secret-token",
            "Bearer wrong-token",
            "Bearer secret-token",
        ):
            supplied = dict(headers)
            if authorization is not None:
                supplied["Authorization"] = authorization
            response = client.post(path, headers=supplied, json=request, follow_redirects=False)
            assert response.status_code == 400
            assert "hosted at the origin root" in response.json()["error"]
            assert services == []
            assert auth_service.seen_tokens == []

        authorized = {**headers, "Authorization": "Bearer secret-token"}
        for overrides in (
            {"Host": "untrusted.example"},
            {"Origin": "https://untrusted.example"},
        ):
            response = client.post(
                path, headers={**authorized, **overrides}, json=request, follow_redirects=False
            )
            assert response.status_code == 400
            assert "hosted at the origin root" in response.json()["error"]
            assert services == []
            assert auth_service.seen_tokens == []

        for public_path in ("/gateway/healthz", "/gateway/beta", "/gateway/auth/github/start"):
            response = client.get(public_path, follow_redirects=False)
            assert response.status_code == 400
            assert "hosted at the origin root" in response.json()["error"]
        assert services == []
        assert auth_service.seen_tokens == []


def test_streamable_http_app_accepts_valid_db_backed_api_key(monkeypatch) -> None:
    auth_service = StaticBetaAuthService(BetaAuthDecision(status="authorized", source="api_key"))
    _stub_streamable_http_server(monkeypatch, auth_service=auth_service)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer db-secret"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert auth_service.seen_tokens == ["db-secret"]


def test_streamable_http_app_returns_403_for_suspended_beta_account(monkeypatch) -> None:
    auth_service = StaticBetaAuthService(BetaAuthDecision(status="suspended", source="api_key"))
    _stub_streamable_http_server(monkeypatch, auth_service=auth_service)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer suspended-secret"})

    assert response.status_code == 403
    assert response.json() == {"error": "Account suspended."}


def test_streamable_http_app_returns_429_for_quota_exhausted_beta_account(monkeypatch) -> None:
    auth_service = StaticBetaAuthService(
        BetaAuthDecision(status="quota_exceeded", source="api_key")
    )
    _stub_streamable_http_server(monkeypatch, auth_service=auth_service)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer quota-secret"})

    assert response.status_code == 429
    assert response.json() == {"error": "Quota exceeded."}


def test_slow_api_key_authentication_does_not_block_public_requests(monkeypatch) -> None:
    """SQLite/auth latency stays off the event loop serving public health traffic."""
    started = Event()
    release = Event()
    worker_threads: list[int] = []

    class SlowAuthService(StaticBetaAuthService):
        def authenticate_api_key(self, *, token: str | None) -> BetaAuthDecision:
            """Simulate blocking account and quota storage without a live database."""
            worker_threads.append(get_ident())
            started.set()
            assert release.wait(5), "test did not release auth work"
            return super().authenticate_api_key(token=token)

    async def ok(request) -> JSONResponse:
        """Serve a lightweight public route while authentication is blocked."""
        return JSONResponse({"ok": True})

    service = SlowAuthService(BetaAuthDecision(status="authorized", source="api_key"))
    stub = StreamableHTTPStubServer(Starlette(routes=[Route("/mcp", ok), Route("/healthz", ok)]))
    monkeypatch.setattr(mcp_module, "_create_mcp_server", lambda *args, **kwargs: stub)
    monkeypatch.setattr(mcp_module, "_build_beta_auth_service", lambda settings: service)
    app = mcp_module._build_streamable_http_app(_hosted_settings())

    async def exercise() -> None:
        """Use concurrent ASGI requests to prove the authentication boundary yields."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            request = asyncio.create_task(
                client.get("/mcp", headers={"Authorization": "Bearer db-token"})
            )
            try:
                await _wait_until_set(started)
                health = await asyncio.wait_for(client.get("/healthz"), timeout=0.5)
                assert health.status_code == 200
                assert len(worker_threads) == 1
                assert worker_threads[0] != get_ident()
            finally:
                release.set()
                result = await request
            assert result.status_code == 200

    asyncio.run(exercise())


def test_slow_github_callback_does_not_block_portal_requests(monkeypatch) -> None:
    """A blocked OAuth exchange leaves the browser session and other routes responsive."""
    started = Event()
    release = Event()
    worker_threads: list[int] = []
    oauth_states: list[str] = []

    class SlowGithubService:
        def build_github_authorize_url(self, *, state: str) -> str:
            """Capture the real session state for a valid callback request."""
            oauth_states.append(state)
            return "https://github.example/authorize"

        def complete_github_oauth(self, *, code: str):
            """Block before returning a deliberate provider failure to the browser."""
            worker_threads.append(get_ident())
            started.set()
            assert release.wait(5), "test did not release GitHub exchange"
            raise ProviderError("GitHub is unavailable")

    monkeypatch.setattr(
        mcp_module, "create_beta_auth_service", lambda settings: SlowGithubService()
    )
    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    async def exercise() -> None:
        """Keep the actual session middleware and callback validation in the test."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://beta.example.com"
        ) as client:
            start = await client.get("/auth/github/start")
            assert start.status_code == 302
            request = asyncio.create_task(
                client.get(
                    "/auth/github/callback", params={"state": oauth_states[0], "code": "fixture"}
                )
            )
            try:
                await _wait_until_set(started)
                landing = await asyncio.wait_for(client.get("/beta"), timeout=0.5)
                assert landing.status_code == 200
                assert worker_threads[0] != get_ident()
            finally:
                release.set()
                result = await request
            assert result.status_code == 502

    asyncio.run(exercise())
