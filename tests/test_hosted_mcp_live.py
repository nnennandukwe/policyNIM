"""Opt-in live smoke coverage for the deployed hosted MCP beta."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from policynim.types import PreflightResult, SearchResult

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


def _structured_payload(result) -> dict[str, object]:  # noqa: ANN001
    payload = result.structuredContent
    assert isinstance(payload, dict)
    return payload


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
        _BETA_URL,
        headers={
            "Accept": "text/event-stream",
            "Authorization": "Bearer invalid-token",
        },
        timeout=30.0,
    )

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized."}
