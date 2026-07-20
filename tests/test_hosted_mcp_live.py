"""Opt-in live smoke coverage for the deployed hosted MCP beta."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from policynim.types import HealthCheckResult, PreflightResult, SearchResult

_BETA_URL = os.getenv("POLICYNIM_BETA_MCP_URL", "").strip()
_BETA_TOKEN = os.getenv("POLICYNIM_BETA_MCP_TOKEN", "").strip()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _BETA_URL, reason="POLICYNIM_BETA_MCP_URL is not configured."),
    pytest.mark.skipif(not _BETA_TOKEN, reason="POLICYNIM_BETA_MCP_TOKEN is not configured."),
]


@asynccontextmanager
async def _authenticated_session() -> AsyncIterator[ClientSession]:
    headers = {"Authorization": f"Bearer {_BETA_TOKEN}"}
    timeout = httpx.Timeout(30.0, read=300.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
        async with streamable_http_client(_BETA_URL, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def _structured_payload(result) -> dict[str, object]:
    payload = result.structuredContent
    assert isinstance(payload, dict)
    return payload


def _hosted_url(path: str) -> str:
    """Build a hosted URL on the same origin as the configured MCP endpoint."""
    parts = urlsplit(_BETA_URL)
    if not parts.scheme or not parts.netloc:
        raise AssertionError("POLICYNIM_BETA_MCP_URL must be an absolute URL.")
    if parts.path.rstrip("/") != "/mcp":
        raise AssertionError("POLICYNIM_BETA_MCP_URL must point to the hosted /mcp route.")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _expected_mcp_url() -> str:
    """Return the normalized hosted MCP URL expected in health payloads."""
    return _hosted_url("/mcp")


def _health_url() -> str:
    """Return the public hosted readiness URL for the beta service."""
    return _hosted_url("/healthz")


def test_hosted_healthz_reports_ready_index_live() -> None:
    """Verify the deployed beta exposes a ready health payload with an indexed corpus."""
    response = httpx.get(_health_url(), timeout=30.0)

    assert response.status_code == 200
    payload = HealthCheckResult.model_validate(response.json())
    assert payload.ready is True
    assert payload.status == "ok"
    assert payload.row_count > 0
    assert payload.mcp_url == _expected_mcp_url()
    assert payload.reason is None


def test_hosted_mcp_lists_tools_live() -> None:
    async def run() -> set[str]:
        async with _authenticated_session() as session:
            result = await session.list_tools()
            return {tool.name for tool in result.tools}

    assert asyncio.run(run()) == {"policy_preflight", "policy_search"}


def test_hosted_policy_search_live() -> None:
    async def run() -> SearchResult:
        async with _authenticated_session() as session:
            result = await session.call_tool(
                "policy_search",
                {"query": "background cleanup", "top_k": 1},
            )
            return SearchResult.model_validate(_structured_payload(result))

    payload = asyncio.run(run())

    assert payload.query == "background cleanup"
    assert payload.hits
    assert payload.insufficient_context is False


def test_hosted_policy_preflight_live() -> None:
    async def run() -> PreflightResult:
        async with _authenticated_session() as session:
            result = await session.call_tool(
                "policy_preflight",
                {"task": "refresh token cleanup", "top_k": 3},
            )
            return PreflightResult.model_validate(_structured_payload(result))

    payload = asyncio.run(run())

    assert payload.summary
    assert payload.citations
    assert payload.insufficient_context is False


def test_hosted_mcp_rejects_invalid_token_live() -> None:
    response = httpx.get(
        _expected_mcp_url(),
        headers={
            "Accept": "text/event-stream",
            "Authorization": "Bearer invalid-token",
        },
        timeout=30.0,
    )

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized."}
