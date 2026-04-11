"""Tests for the MCP surface and hosted HTTP runtime."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Sequence

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import ASGIApp

from policynim.errors import ConfigurationError, MissingIndexError, ProviderError
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

    def streamable_http_app(self) -> ASGIApp:
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
    result = asyncio.run(mcp_module.mcp.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    assert isinstance(result, dict)
    return result


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

    with pytest.raises(ToolError, match="Run `policynim ingest` first"):
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

    with pytest.raises(ToolError, match="missing NVIDIA key"):
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
            raise ConfigurationError("upstream timeout", failure_class="timeout")

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

    with pytest.raises(ToolError, match="upstream timeout"):
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
        ) -> GeneratedPreflightDraft:
            del compiled_packet
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

    with pytest.raises(ToolError, match="upstream timeout"):
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

    with TestClient(app) as client:
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

    with TestClient(app) as client:
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

    with TestClient(app) as client:
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

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["ready"] is False
    assert payload["reason"] == "Local index readiness could not be inspected."
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

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(mcp_module, "create_runtime_health_service", build_service)
    monkeypatch.setattr(mcp_module.asyncio, "to_thread", fake_to_thread)

    app = mcp_module._build_streamable_http_app(Settings())

    with TestClient(app) as client:
        first = client.get("/healthz")
        second = client.get("/healthz")

    assert first.status_code == 200
    assert second.status_code == 200
    assert factory_calls == 1
    assert check_calls == 2


def test_streamable_http_app_keeps_mcp_open_when_auth_disabled(monkeypatch) -> None:
    _stub_streamable_http_server(monkeypatch)

    app = mcp_module._build_streamable_http_app(Settings())

    with TestClient(app) as client:
        response = client.get("/mcp")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_streamable_http_app_rejects_missing_bearer_token(monkeypatch) -> None:
    _stub_streamable_http_server(monkeypatch)

    app = mcp_module._build_streamable_http_app(_hosted_settings())

    with TestClient(app) as client:
        response = client.get("/mcp")

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

    with TestClient(app) as client:
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

    with TestClient(app) as client:
        response = client.get("/mcp", headers={"Authorization": "Token secret-token"})

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized."}


def test_streamable_http_app_rejects_invalid_bearer_token(monkeypatch) -> None:
    auth_service = StaticBetaAuthService(BetaAuthDecision(status="unauthorized"))
    _stub_streamable_http_server(monkeypatch, auth_service=auth_service)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app) as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer wrong-token"})

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized."}
    assert auth_service.seen_tokens == ["wrong-token"]


def test_streamable_http_app_accepts_valid_bearer_token(monkeypatch) -> None:
    _stub_streamable_http_server(monkeypatch)

    app = mcp_module._build_streamable_http_app(_hosted_settings())

    with TestClient(app) as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_streamable_http_app_accepts_valid_db_backed_api_key(monkeypatch) -> None:
    auth_service = StaticBetaAuthService(BetaAuthDecision(status="authorized", source="api_key"))
    _stub_streamable_http_server(monkeypatch, auth_service=auth_service)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app) as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer db-secret"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert auth_service.seen_tokens == ["db-secret"]


def test_streamable_http_app_returns_403_for_suspended_beta_account(monkeypatch) -> None:
    auth_service = StaticBetaAuthService(BetaAuthDecision(status="suspended", source="api_key"))
    _stub_streamable_http_server(monkeypatch, auth_service=auth_service)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app) as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer suspended-secret"})

    assert response.status_code == 403
    assert response.json() == {"error": "Account suspended."}


def test_streamable_http_app_returns_429_for_quota_exhausted_beta_account(monkeypatch) -> None:
    auth_service = StaticBetaAuthService(
        BetaAuthDecision(status="quota_exceeded", source="api_key")
    )
    _stub_streamable_http_server(monkeypatch, auth_service=auth_service)

    app = mcp_module._build_streamable_http_app(_self_serve_hosted_settings())

    with TestClient(app) as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer quota-secret"})

    assert response.status_code == 429
    assert response.json() == {"error": "Quota exceeded."}
