"""MCP surface for the public PolicyNIM workflow."""

from __future__ import annotations

import asyncio
import errno
import logging
import socket

from mcp.server.fastmcp import FastMCP
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from policynim.errors import ConfigurationError
from policynim.services import (
    create_preflight_service,
    create_runtime_health_service,
    create_search_service,
    ensure_hosted_runtime_ready,
)
from policynim.settings import Settings, get_settings
from policynim.types import (
    MAX_TOP_K,
    MIN_TOP_K,
    HealthCheckResult,
    PreflightRequest,
    SearchRequest,
)

SUPPORTED_TRANSPORTS = ("stdio", "streamable-http")
_STREAMABLE_HTTP_PATH = "/mcp"
_HEALTH_PATH = "/healthz"
LOGGER = logging.getLogger(__name__)


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


def _register_tools(server: FastMCP) -> FastMCP:
    """Register the public MCP tools on the supplied server instance."""
    server.tool(name="policy_preflight")(policy_preflight)
    server.tool(name="policy_search")(policy_search)
    return server


def _create_mcp_server(settings: Settings) -> FastMCP:
    """Create a fresh MCP server configured from runtime settings."""
    server = FastMCP(
        "PolicyNIM",
        json_response=True,
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path=_STREAMABLE_HTTP_PATH,
    )
    _register_tools(server)
    _register_health_route(server, settings)
    return server


def _register_health_route(server: FastMCP, settings: Settings) -> None:
    """Register a public readiness endpoint for hosted HTTP runtimes."""
    try:
        health_service = create_runtime_health_service(settings)
    except Exception:
        LOGGER.exception("Could not construct runtime health service.")
        health_service = None
    fallback_reason = "Local index readiness could not be inspected."

    def _fallback_result() -> JSONResponse:
        result = HealthCheckResult(
            status="error",
            ready=False,
            table_name=settings.lancedb_table,
            row_count=0,
            mcp_url=_derive_mcp_url(settings),
            reason=fallback_reason,
        )
        return JSONResponse(result.model_dump(mode="json"), status_code=503)

    @server.custom_route(_HEALTH_PATH, methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> Response:
        if health_service is None:
            return _fallback_result()

        try:
            result = await asyncio.to_thread(health_service.check)
        except Exception:
            LOGGER.exception("Runtime health probe failed.")
            return _fallback_result()

        status_code = 200 if result.ready else 503
        return JSONResponse(result.model_dump(mode="json"), status_code=status_code)


def _derive_mcp_url(settings: Settings) -> str | None:
    if settings.mcp_public_base_url is None:
        return None
    return str(settings.mcp_public_base_url).rstrip("/") + "/mcp"


class _BearerProtectedASGIApp:
    """Protect the MCP HTTP route with exact-match bearer token auth."""

    def __init__(self, app: ASGIApp, *, protected_path: str, valid_tokens: list[str]) -> None:
        self._app = app
        self._protected_path = protected_path
        self._valid_tokens = set(valid_tokens)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != self._protected_path:
            await self._app(scope, receive, send)
            return

        token = _extract_bearer_token(scope)
        if token is None or token not in self._valid_tokens:
            response = JSONResponse({"error": "Unauthorized."}, status_code=401)
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


def _extract_bearer_token(scope: Scope) -> str | None:
    """Return the bearer token from the HTTP Authorization header, if valid."""
    headers = Headers(scope=scope)
    authorization = headers.get("authorization")
    if authorization is None:
        return None

    parts = authorization.strip().split()
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _build_streamable_http_app(settings: Settings) -> ASGIApp:
    """Create the streamable-http ASGI app, wrapping auth only when required."""
    server = _create_mcp_server(settings)
    app = server.streamable_http_app()
    if not settings.mcp_require_auth:
        return app
    return _BearerProtectedASGIApp(
        app,
        protected_path=server.settings.streamable_http_path,
        valid_tokens=settings.mcp_bearer_tokens,
    )


def _run_streamable_http_app(
    app: ASGIApp,
    *,
    host: str,
    port: int,
    log_level: str = "info",
) -> None:
    """Serve the hosted HTTP app through uvicorn."""
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    server.run()


def _should_fail_fast_for_hosted_http(settings: Settings) -> bool:
    """Return whether hosted HTTP startup should require a ready local index."""
    return settings.mcp_public_base_url is not None


def run_server(transport: str = "stdio") -> None:
    """Run the PolicyNIM MCP server."""
    if transport not in SUPPORTED_TRANSPORTS:
        allowed = ", ".join(SUPPORTED_TRANSPORTS)
        raise ValueError(f"Transport must be one of: {allowed}.")

    settings = get_settings()
    if transport == "streamable-http":
        _ensure_streamable_http_port_available(settings.mcp_host, settings.mcp_port)
        if _should_fail_fast_for_hosted_http(settings):
            ensure_hosted_runtime_ready(settings)
        app = _build_streamable_http_app(settings)
        try:
            _run_streamable_http_app(app, host=settings.mcp_host, port=settings.mcp_port)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise ConfigurationError(
                    _streamable_http_port_in_use_message(settings.mcp_host, settings.mcp_port)
                ) from exc
            raise
        return

    server = _create_mcp_server(settings)
    server.run(transport=transport)


mcp = _register_tools(FastMCP("PolicyNIM", json_response=True))


if __name__ == "__main__":
    run_server()
