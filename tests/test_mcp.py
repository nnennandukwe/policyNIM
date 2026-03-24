"""Tests for the Day 5 MCP surface."""

from __future__ import annotations

import asyncio
import socket

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from policynim.errors import ConfigurationError, MissingIndexError
from policynim.interfaces import mcp as mcp_module
from policynim.settings import Settings
from policynim.types import (
    Citation,
    PolicyGuidance,
    PolicyMetadata,
    PreflightRequest,
    PreflightResult,
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

    def mock_run(*, transport: str) -> None:
        captured["transport"] = transport

    monkeypatch.setattr(
        mcp_module,
        "get_settings",
        lambda: Settings(mcp_host="0.0.0.0", mcp_port=9001),
    )
    monkeypatch.setattr(mcp_module.mcp, "run", mock_run)

    mcp_module.run_server("stdio")

    assert captured == {"transport": "stdio"}
    assert mcp_module.mcp.settings.host == "0.0.0.0"
    assert mcp_module.mcp.settings.port == 9001


def test_run_server_uses_streamable_http_transport(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def mock_run(*, transport: str) -> None:
        captured["transport"] = transport

    monkeypatch.setattr(
        mcp_module,
        "get_settings",
        lambda: Settings(mcp_host="127.0.0.1", mcp_port=8010),
    )
    monkeypatch.setattr(mcp_module.mcp, "run", mock_run)

    mcp_module.run_server("streamable-http")

    assert captured == {"transport": "streamable-http"}
    assert mcp_module.mcp.settings.host == "127.0.0.1"
    assert mcp_module.mcp.settings.port == 8010


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
        mcp_module.mcp,
        "run",
        lambda **kwargs: pytest.fail("mcp.run should not be called when the port probe fails"),
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
