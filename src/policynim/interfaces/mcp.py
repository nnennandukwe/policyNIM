"""MCP surface for the public PolicyNIM workflow."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from policynim.services import create_preflight_service, create_search_service
from policynim.settings import get_settings
from policynim.types import MAX_TOP_K, MIN_TOP_K, PreflightRequest, SearchRequest

SUPPORTED_TRANSPORTS = ("stdio", "streamable-http")

mcp = FastMCP("PolicyNIM", json_response=True)


def _resolve_top_k(top_k: int | None) -> int:
    """Resolve and validate top_k across MCP tools."""
    resolved = top_k if top_k is not None else get_settings().default_top_k
    _validate_top_k(resolved)
    return resolved


def _validate_top_k(top_k: int) -> None:
    """Validate top_k across MCP tools."""
    if not MIN_TOP_K <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}.")


def _close_service(service: object | None) -> None:
    close = getattr(service, "close", None)
    if callable(close):
        close()


@mcp.tool(name="policy_preflight")
def policy_preflight(
    task: str,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, object]:
    """Return policy guidance for a coding task."""
    resolved_top_k = _resolve_top_k(top_k)
    service = create_preflight_service(get_settings())
    try:
        result = service.preflight(PreflightRequest(task=task, domain=domain, top_k=resolved_top_k))
        return result.model_dump(mode="json")
    finally:
        _close_service(service)


@mcp.tool(name="policy_search")
def policy_search(
    query: str,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, object]:
    """Search the policy corpus."""
    resolved_top_k = _resolve_top_k(top_k)
    service = create_search_service(get_settings())
    try:
        result = service.search(SearchRequest(query=query, domain=domain, top_k=resolved_top_k))
        return result.model_dump(mode="json")
    finally:
        _close_service(service)


def run_server(transport: str = "stdio") -> None:
    """Run the PolicyNIM MCP server."""
    if transport not in SUPPORTED_TRANSPORTS:
        allowed = ", ".join(SUPPORTED_TRANSPORTS)
        raise ValueError(f"Transport must be one of: {allowed}.")
    settings = get_settings()
    mcp.settings.host = settings.mcp_host
    mcp.settings.port = settings.mcp_port
    mcp.run(transport=transport)


if __name__ == "__main__":
    run_server()
