"""MCP surface for the public PolicyNIM workflow."""

from __future__ import annotations

import errno
import socket

from mcp.server.fastmcp import FastMCP

from policynim.errors import ConfigurationError
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


def _streamable_http_port_in_use_message(host: str, port: int) -> str:
    """Return a concrete recovery message for MCP port collisions."""
    return (
        f"Could not start streamable-http MCP server on {host}:{port} "
        "because the port is already in use. "
        "Stop the conflicting process or set `POLICYNIM_MCP_PORT` to another open port. "
        "If the eval UI is also running, check `POLICYNIM_EVAL_UI_PORT` as well."
    )


def _ensure_streamable_http_port_available(host: str, port: int) -> None:
    """Fail early with a clear error when the HTTP MCP port is already occupied."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise ConfigurationError(_streamable_http_port_in_use_message(host, port)) from exc
        raise ConfigurationError(
            f"Could not reserve streamable-http MCP server port {host}:{port}: {exc}."
        ) from exc


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
    if transport == "streamable-http":
        _ensure_streamable_http_port_available(settings.mcp_host, settings.mcp_port)
    try:
        mcp.run(transport=transport)
    except OSError as exc:
        if transport == "streamable-http" and exc.errno == errno.EADDRINUSE:
            raise ConfigurationError(
                _streamable_http_port_in_use_message(settings.mcp_host, settings.mcp_port)
            ) from exc
        raise


if __name__ == "__main__":
    run_server()
