"""Offline integration tests exercising actual stdio and localhost HTTP MCP."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from fixtures.mcp_fixture_server import (
    CITATION,
    UNEXPECTED_DETAIL,
    OfflineSettings,
    offline_services,
)
from jsonschema import Draft202012Validator, ValidationError
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import AnyHttpUrl
from starlette.types import ASGIApp

from policynim.interfaces import mcp as mcp_module
from policynim.settings import Settings
from policynim.types import PreflightResult, SearchResult

_ROOT = Path(__file__).resolve().parents[1]
_STDIO_SERVER = Path(__file__).parent / "fixtures" / "mcp_fixture_server.py"
_TOKEN = "offline-protocol-token"


@asynccontextmanager
async def _serve_http(app: ASGIApp) -> AsyncIterator[str]:
    """Keep the bound ephemeral socket reserved through startup and shutdown."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.setblocking(False)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_level="warning",
                lifespan="on",
                ws="none",
                timeout_graceful_shutdown=2,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise AssertionError("Offline HTTP server exited before startup.")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=5)
            finally:
                if not task.done():
                    task.cancel()


def _settings() -> Settings:
    """Configure an isolated HTTP server with a known token and public origin."""
    return OfflineSettings(
        nvidia_api_key=None,
        default_top_k=3,
        mcp_require_auth=True,
        mcp_bearer_tokens=[_TOKEN],
        mcp_public_base_url=AnyHttpUrl("https://policynim.example"),
        beta_signup_enabled=False,
    )


def _validate_result(result: CallToolResult, tool: Tool) -> dict[str, Any]:
    """Validate structured data against the advertised schema and compatible text JSON."""
    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert tool.output_schema is not None
    Draft202012Validator.check_schema(tool.output_schema)
    Draft202012Validator(tool.output_schema).validate(result.structured_content)
    text_blocks = [item.text for item in result.content if isinstance(item, TextContent)]
    assert len(text_blocks) == 1
    assert json.loads(text_blocks[0]) == result.structured_content
    return result.structured_content


async def _exercise_client(client: Client, mode: str) -> None:
    """Exercise discovery, tool contracts, validation, and failures through the SDK client."""
    expected_version = "2025-11-25" if mode == "legacy" else "2026-07-28"
    assert client.protocol_version == expected_version
    assert client.server_info is not None
    assert client.server_info.name == "PolicyNIM"
    assert client.server_info.version == version("policynim")
    if mode == "legacy":
        assert client.session.initialize_result is not None
        assert client.session.discover_result is None
    else:
        assert client.session.discover_result is not None
        assert client.session.initialize_result is None

    listing = await client.list_tools()
    tools = {tool.name: tool for tool in listing.tools}
    assert set(tools) == {"policy_search", "policy_preflight"}
    for tool in tools.values():
        assert tool.description and tool.description.strip()
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.open_world_hint is True
        input_validator = Draft202012Validator(tool.input_schema)
        input_key = "query" if tool.name == "policy_search" else "task"
        for top_k in (None, 1, 20):
            input_validator.validate({input_key: "boundary input", "top_k": top_k})
        for top_k in (0, 21):
            arguments = {input_key: "boundary input", "top_k": top_k}
            with pytest.raises(ValidationError):
                input_validator.validate(arguments)
            rejected = await client.call_tool(tool.name, arguments)
            assert rejected.is_error is True

    search = await client.call_tool(
        "policy_search", {"query": "background cleanup", "domain": "backend", "top_k": 2}
    )
    search_payload = SearchResult.model_validate(_validate_result(search, tools["policy_search"]))
    assert search_payload.query == "background cleanup"
    assert search_payload.domain == "backend"
    assert search_payload.top_k == 2
    assert search_payload.hits[0].chunk_id == CITATION.chunk_id
    assert search_payload.hits[0].lines == CITATION.lines

    default_search = await client.call_tool("policy_search", {"query": "default top-k"})
    assert _validate_result(default_search, tools["policy_search"])["top_k"] == 3

    preflight = await client.call_tool(
        "policy_preflight", {"task": "background cleanup", "domain": "backend", "top_k": 2}
    )
    preflight_payload = PreflightResult.model_validate(
        _validate_result(preflight, tools["policy_preflight"])
    )
    assert preflight_payload.task == "background cleanup"
    assert preflight_payload.domain == "backend"
    assert preflight_payload.citations == [CITATION]
    assert preflight_payload.insufficient_context is False

    uncovered = await client.call_tool("policy_preflight", {"task": "uncovered-task"})
    uncovered_payload = PreflightResult.model_validate(
        _validate_result(uncovered, tools["policy_preflight"])
    )
    assert uncovered_payload.insufficient_context is True
    assert uncovered_payload.citations == []
    assert uncovered_payload.implementation_guidance == []

    expected_error = await client.call_tool("policy_search", {"query": "missing-index"})
    assert expected_error.is_error is True
    assert "policynim ingest" in str(expected_error.content)

    unexpected_error = await client.call_tool("policy_search", {"query": "unexpected-failure"})
    assert unexpected_error.is_error is True
    assert UNEXPECTED_DETAIL not in unexpected_error.model_dump_json()
    assert "RuntimeError" not in unexpected_error.model_dump_json()
    assert any(isinstance(item, TextContent) and item.text for item in unexpected_error.content)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_stdio_subprocess_protocol_round_trip(mode: str, tmp_path: Path) -> None:
    """Discover, call, fail safely, and stop a real child without provider credentials."""
    config_file = tmp_path / "offline.env"
    config_file.write_text("", encoding="utf-8")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(_STDIO_SERVER)],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(_ROOT / "src"),
            "POLICYNIM_CONFIG_FILE": str(config_file),
        },
    )

    async def run() -> None:
        """Start and close the SDK-managed child under a bounded timeout."""
        async with asyncio.timeout(20):
            async with Client(parameters, mode=mode, read_timeout_seconds=5) as client:
                await _exercise_client(client, mode)

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_http_protocol_round_trip_preserves_wire_contract(mode: str) -> None:
    """Verify actual HTTP serialization and sessionless requests for both protocol eras."""
    requests: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    headers: list[httpx2.Headers] = []

    async def record_request(request: httpx2.Request) -> None:
        """Capture serialized JSON-RPC before the real socket transport sends it."""
        if request.method == "POST":
            requests.append(json.loads(request.content))

    async def record_response(response: httpx2.Response) -> None:
        """Capture JSON responses while preserving empty notification acknowledgments."""
        headers.append(response.headers)
        if response.headers.get("content-type", "").startswith("application/json"):
            await response.aread()
            if response.content:
                responses.append(response.json())

    async def run() -> None:
        """Run an authenticated SDK client against a real ephemeral localhost listener."""
        with offline_services(_settings()) as events:
            app = mcp_module._build_streamable_http_app(_settings())
            async with _serve_http(app) as origin:
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {_TOKEN}"},
                    timeout=5,
                    trust_env=False,
                    event_hooks={"request": [record_request], "response": [record_response]},
                ) as http_client:
                    transport = streamable_http_client(f"{origin}/mcp", http_client=http_client)
                    async with Client(transport, mode=mode, read_timeout_seconds=5) as client:
                        await _exercise_client(client, mode)
            assert events.count("search.created") == events.count("search.closed") == 4
            assert events.count("preflight.created") == events.count("preflight.closed") == 2

    asyncio.run(run())
    methods = [request["method"] for request in requests]
    if mode == "legacy":
        assert methods[0] == "initialize"
        assert "notifications/initialized" in methods
        assert "server/discover" not in methods
    else:
        assert methods[0] == "server/discover"
        assert "initialize" not in methods
        for request in requests:
            meta = request["params"]["_meta"]
            assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
            assert "io.modelcontextprotocol/clientCapabilities" in meta
        for response in responses:
            if "result" in response:
                assert response["result"]["resultType"] == "complete"

    assert all("mcp-session-id" not in item for item in headers)
    listing = next(
        response["result"] for response in responses if "tools" in response.get("result", {})
    )
    assert all("outputSchema" in tool and "inputSchema" in tool for tool in listing["tools"])
    results = [
        response["result"] for response in responses if "content" in response.get("result", {})
    ]
    assert any("structuredContent" in result for result in results)
    assert all(
        "structured_content" not in result and "is_error" not in result for result in results
    )


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_http_auth_and_origin_guards_apply_to_real_mcp_paths(path: str) -> None:
    """Neither a slash variant nor a valid token bypasses the HTTP trust boundary."""

    async def run() -> None:
        """Probe authentication and host trust without allowing hostile redirects."""
        with offline_services(_settings()) as events:
            app = mcp_module._build_streamable_http_app(_settings())
            async with _serve_http(app) as origin:
                async with httpx2.AsyncClient(timeout=5, trust_env=False) as client:
                    health = await client.get(f"{origin}/healthz")
                    assert health.status_code == 200
                    assert "index_db_path" not in health.json()
                    assert "index_uri" not in health.json()
                    headers = {
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                        "Mcp-Method": "server/discover",
                    }
                    request = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "server/discover",
                        "params": {
                            "_meta": {
                                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                "io.modelcontextprotocol/clientCapabilities": {},
                            }
                        },
                    }
                    for authorization in (None, "Bearer incorrect", "Token incorrect"):
                        supplied = dict(headers)
                        if authorization is not None:
                            supplied["Authorization"] = authorization
                        response = await client.post(
                            f"{origin}{path}", headers=supplied, json=request
                        )
                        assert response.status_code == 401
                        assert response.json() == {"error": "Unauthorized."}

                    authorized = {**headers, "Authorization": f"Bearer {_TOKEN}"}
                    for origin_header in (None, origin, "https://policynim.example"):
                        supplied = dict(authorized)
                        if origin_header is not None:
                            supplied["Origin"] = origin_header
                        response = await client.post(
                            f"{origin}{path}", headers=supplied, json=request, follow_redirects=True
                        )
                        assert response.status_code == 200
                        assert "2026-07-28" in response.json()["result"]["supportedVersions"]

                    for overrides, status in (
                        ({"Host": "untrusted.example"}, 421),
                        ({"Origin": "https://untrusted.example"}, 403),
                    ):
                        response = await client.post(
                            f"{origin}{path}",
                            headers={**authorized, **overrides},
                            json=request,
                            follow_redirects=False,
                        )
                        assert response.status_code == status
            assert events == []

    asyncio.run(run())
