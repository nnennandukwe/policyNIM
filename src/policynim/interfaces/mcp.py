"""MCP surface for the public PolicyNIM workflow."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import secrets
import socket
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from mcp.server.fastmcp import Context, FastMCP
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from policynim.errors import ConfigurationError, PolicyNIMError, ProviderError
from policynim.runtime_paths import resolve_asset_path, resolve_template_root
from policynim.services import (
    BetaAuthService,
    create_beta_auth_service,
    create_preflight_service,
    create_runtime_health_service,
    create_search_service,
    ensure_hosted_runtime_ready,
)
from policynim.settings import Settings, get_settings
from policynim.types import (
    MAX_TOP_K,
    MIN_TOP_K,
    BetaAccount,
    BetaUsageSnapshot,
    HealthCheckResult,
    PreflightRequest,
    SearchRequest,
)

SUPPORTED_TRANSPORTS = ("stdio", "streamable-http")
_STREAMABLE_HTTP_PATH = "/mcp"
_HEALTH_PATH = "/healthz"
_BETA_PATH = "/beta"
_FAVICON_PATH = "/favicon.ico"
_AUTH_GITHUB_START_PATH = "/auth/github/start"
_AUTH_GITHUB_CALLBACK_PATH = "/auth/github/callback"
_BETA_API_KEY_REGENERATE_PATH = "/beta/api-key/regenerate"
_BETA_LOGOUT_PATH = "/beta/logout"
_BETA_ASSET_PATH = "/beta/assets"
_BETA_LIGHT_LOGO_FILENAME = "policynim_lightmode.png"
_BETA_DARK_LOGO_FILENAME = "policynim_darkmode.jpg"
_BETA_CSS_FILENAME = "beta.css"
_BETA_THEME_INIT_JS_FILENAME = "beta-theme-init.js"
_BETA_PAGE_JS_FILENAME = "beta-page.js"
_BETA_LIGHT_LOGO_ROUTE = f"{_BETA_ASSET_PATH}/{_BETA_LIGHT_LOGO_FILENAME}"
_BETA_DARK_LOGO_ROUTE = f"{_BETA_ASSET_PATH}/{_BETA_DARK_LOGO_FILENAME}"
_BETA_CSS_ROUTE = f"{_BETA_ASSET_PATH}/{_BETA_CSS_FILENAME}"
_BETA_THEME_INIT_JS_ROUTE = f"{_BETA_ASSET_PATH}/{_BETA_THEME_INIT_JS_FILENAME}"
_BETA_PAGE_JS_ROUTE = f"{_BETA_ASSET_PATH}/{_BETA_PAGE_JS_FILENAME}"
_BETA_ACCOUNT_SESSION_KEY = "beta_account_id"
_BETA_GITHUB_STATE_SESSION_KEY = "beta_github_oauth_state"
LOGGER = logging.getLogger(__name__)
_HOSTED_LOGGER_NAME = "policynim.hosted"
_HOSTED_AUTH_RESULT: ContextVar[str] = ContextVar(
    "policynim_hosted_auth_result",
    default="not_required",
)


class _InMemoryRateLimiter:
    """Process-local sliding-window throttling for the GitHub auth routes."""

    def __init__(self, *, max_attempts: int, window_seconds: int) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        attempts = [
            attempt
            for attempt in self._attempts.get(key, [])
            if timestamp - attempt < self._window_seconds
        ]
        if len(attempts) >= self._max_attempts:
            self._attempts[key] = attempts
            return False
        attempts.append(timestamp)
        self._attempts[key] = attempts
        return True

    def reset(self) -> None:
        """Clear all in-memory rate-limit state."""
        self._attempts.clear()


def _resolve_top_k(top_k: int | None) -> int:
    """Resolve and validate top_k across MCP tools."""
    resolved = top_k if top_k is not None else get_settings().default_top_k
    _validate_top_k(resolved)
    return resolved


def _validate_top_k(top_k: int) -> None:
    """Validate top_k across MCP tools."""
    if not MIN_TOP_K <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}.")


def _format_validation_error(label: str, exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "request"
    return f"{label} is invalid at {location}: {error['msg']}."


def _close_service(service: object | None) -> None:
    close = getattr(service, "close", None)
    if callable(close):
        close()


def _configure_hosted_logger() -> None:
    """Emit hosted MCP telemetry as one JSON object per line."""
    logger = logging.getLogger(_HOSTED_LOGGER_NAME)
    if getattr(logger, "_policynim_configured", False):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    setattr(logger, "_policynim_configured", True)


def _emit_hosted_event(
    event: str,
    *,
    auth_result: str,
    tool_name: str | None,
    latency_ms: float | None,
    upstream_failure_class: str | None,
    request_id: str | None,
) -> None:
    payload = {
        "event": event,
        "auth_result": auth_result,
        "tool_name": tool_name,
        "latency_ms": latency_ms,
        "upstream_failure_class": upstream_failure_class,
        "request_id": request_id,
    }
    logging.getLogger(_HOSTED_LOGGER_NAME).info(json.dumps(payload, sort_keys=True))


def _elapsed_ms(start_time: float) -> float:
    return round((time.perf_counter() - start_time) * 1000, 2)


def _failure_class_from_error(exc: BaseException) -> str | None:
    current: BaseException | None = exc
    while current is not None:
        failure_class = getattr(current, "failure_class", None)
        if isinstance(failure_class, str) and failure_class:
            return failure_class
        current = current.__cause__ or current.__context__
    return None


def _request_id_from_context(ctx: Context) -> str | None:
    try:
        return ctx.request_id
    except ValueError:
        return None


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


def _run_policy_preflight(
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
    except ValidationError as exc:
        raise ValueError(_format_validation_error("Preflight request", exc)) from exc
    finally:
        _close_service(service)


def policy_preflight(
    task: str,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, object]:
    """Return policy guidance for a coding task."""
    return _run_policy_preflight(task=task, domain=domain, top_k=top_k)


def _run_policy_search(
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


def policy_search(
    query: str,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, object]:
    """Search the policy corpus."""
    return _run_policy_search(query=query, domain=domain, top_k=top_k)


def _run_logged_tool(
    tool_name: str,
    operation: Callable[[], dict[str, object]],
    *,
    ctx: Context,
) -> dict[str, object]:
    start_time = time.perf_counter()
    auth_result = _HOSTED_AUTH_RESULT.get()
    request_id = _request_id_from_context(ctx)
    try:
        result = operation()
    except Exception as exc:
        _emit_hosted_event(
            "mcp.tool",
            auth_result=auth_result,
            tool_name=tool_name,
            latency_ms=_elapsed_ms(start_time),
            upstream_failure_class=_failure_class_from_error(exc),
            request_id=request_id,
        )
        raise

    _emit_hosted_event(
        "mcp.tool",
        auth_result=auth_result,
        tool_name=tool_name,
        latency_ms=_elapsed_ms(start_time),
        upstream_failure_class=None,
        request_id=request_id,
    )
    return result


def _policy_preflight_tool(
    task: str,
    domain: str | None = None,
    top_k: int | None = None,
    *,
    ctx: Context,
) -> dict[str, object]:
    return _run_logged_tool(
        "policy_preflight",
        lambda: _run_policy_preflight(task=task, domain=domain, top_k=top_k),
        ctx=ctx,
    )


def _policy_search_tool(
    query: str,
    domain: str | None = None,
    top_k: int | None = None,
    *,
    ctx: Context,
) -> dict[str, object]:
    return _run_logged_tool(
        "policy_search",
        lambda: _run_policy_search(query=query, domain=domain, top_k=top_k),
        ctx=ctx,
    )


def _register_tools(server: FastMCP) -> FastMCP:
    """Register the public MCP tools on the supplied server instance."""
    server.tool(name="policy_preflight")(_policy_preflight_tool)
    server.tool(name="policy_search")(_policy_search_tool)
    return server


def _create_mcp_server(
    settings: Settings,
    *,
    beta_auth_service: BetaAuthService | None = None,
) -> FastMCP:
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
    if settings.beta_signup_enabled and beta_auth_service is not None:
        _register_beta_routes(server, settings, beta_auth_service)
    return server


def _register_beta_routes(
    server: FastMCP,
    settings: Settings,
    beta_auth_service: BetaAuthService,
) -> None:
    """Register the hosted beta portal routes."""
    limiter = _InMemoryRateLimiter(
        max_attempts=settings.beta_auth_rate_limit_max_attempts,
        window_seconds=settings.beta_auth_rate_limit_window_seconds,
    )
    trust_forwarded_headers = _beta_session_https_only(settings)

    def _rate_limited(request: Request) -> HTMLResponse | None:
        client_ip = _client_address(
            request,
            trust_forwarded_headers=trust_forwarded_headers,
        )
        if limiter.allow(f"{request.url.path}:{client_ip}"):
            return None
        return _render_beta_landing(
            settings,
            message=(
                "Too many beta authentication attempts from this IP. "
                "Retry after the rate-limit window."
            ),
            status_code=429,
        )

    @server.custom_route(_BETA_LIGHT_LOGO_ROUTE, methods=["GET"], include_in_schema=False)
    async def beta_light_logo(_: Request) -> Response:
        return _render_beta_asset(_BETA_LIGHT_LOGO_FILENAME, media_type="image/png")

    @server.custom_route(_BETA_DARK_LOGO_ROUTE, methods=["GET"], include_in_schema=False)
    async def beta_dark_logo(_: Request) -> Response:
        return _render_beta_asset(_BETA_DARK_LOGO_FILENAME, media_type="image/jpeg")

    @server.custom_route(_BETA_CSS_ROUTE, methods=["GET"], include_in_schema=False)
    async def beta_css(_: Request) -> Response:
        return _render_beta_asset(_BETA_CSS_FILENAME, media_type="text/css")

    @server.custom_route(_BETA_THEME_INIT_JS_ROUTE, methods=["GET"], include_in_schema=False)
    async def beta_theme_init_js(_: Request) -> Response:
        return _render_beta_asset(_BETA_THEME_INIT_JS_FILENAME, media_type="text/javascript")

    @server.custom_route(_BETA_PAGE_JS_ROUTE, methods=["GET"], include_in_schema=False)
    async def beta_page_js(_: Request) -> Response:
        return _render_beta_asset(_BETA_PAGE_JS_FILENAME, media_type="text/javascript")

    @server.custom_route(_FAVICON_PATH, methods=["GET"], include_in_schema=False)
    async def favicon(_: Request) -> Response:
        return _render_beta_asset(_BETA_LIGHT_LOGO_FILENAME, media_type="image/png")

    @server.custom_route(_BETA_PATH, methods=["GET"], include_in_schema=False)
    async def beta_dashboard(request: Request) -> Response:
        account_id = _require_beta_session_account_id(request)
        if account_id is None:
            return _render_beta_landing(settings)

        account = beta_auth_service.get_account(account_id)
        if account is None:
            request.session.clear()
            return _render_beta_landing(
                settings,
                message="Your hosted beta session expired. Sign in again to continue.",
            )
        usage = beta_auth_service.get_portal_usage(account_id)
        return _render_beta_dashboard(settings, account=account, usage=usage)

    @server.custom_route(_AUTH_GITHUB_START_PATH, methods=["GET"], include_in_schema=False)
    async def github_start(request: Request) -> Response:
        blocked = _rate_limited(request)
        if blocked is not None:
            return blocked
        state = secrets.token_urlsafe(24)
        request.session[_BETA_GITHUB_STATE_SESSION_KEY] = state
        return RedirectResponse(
            beta_auth_service.build_github_authorize_url(state=state),
            status_code=302,
        )

    @server.custom_route(_AUTH_GITHUB_CALLBACK_PATH, methods=["GET"], include_in_schema=False)
    async def github_callback(request: Request) -> Response:
        blocked = _rate_limited(request)
        if blocked is not None:
            return blocked
        error = str(request.query_params.get("error") or "").strip()
        if error:
            return _render_beta_landing(
                settings,
                message=f"GitHub sign-in failed: {error}. Retry the sign-in flow.",
                status_code=400,
            )

        expected_state = request.session.pop(_BETA_GITHUB_STATE_SESSION_KEY, None)
        returned_state = str(request.query_params.get("state") or "").strip()
        if not expected_state or not returned_state or returned_state != expected_state:
            return _render_beta_landing(
                settings,
                message=(
                    "GitHub sign-in failed because the OAuth state was missing or invalid. "
                    "Start the sign-in flow again from /beta."
                ),
                status_code=400,
            )

        code = str(request.query_params.get("code") or "").strip()
        try:
            account = beta_auth_service.complete_github_oauth(code=code)
        except (PolicyNIMError, ProviderError) as exc:
            return _render_beta_landing(settings, message=str(exc), status_code=502)
        except Exception:
            LOGGER.exception("Unexpected hosted beta OAuth callback failure.")
            return _render_beta_landing(
                settings,
                message=(
                    "GitHub sign-in failed due to an unexpected upstream error. "
                    "Retry the sign-in flow."
                ),
                status_code=502,
            )

        request.session[_BETA_ACCOUNT_SESSION_KEY] = account.account_id
        return RedirectResponse(_BETA_PATH, status_code=302)

    @server.custom_route(_BETA_API_KEY_REGENERATE_PATH, methods=["POST"], include_in_schema=False)
    async def beta_regenerate_api_key(request: Request) -> Response:
        account_id = _require_beta_session_account_id(request)
        if account_id is None:
            return RedirectResponse(_BETA_PATH, status_code=302)
        account = beta_auth_service.get_account(account_id)
        if account is None:
            request.session.clear()
            return RedirectResponse(_BETA_PATH, status_code=302)
        try:
            issued_key = beta_auth_service.issue_api_key(account_id=account_id)
        except PolicyNIMError as exc:
            usage = beta_auth_service.get_portal_usage(account_id)
            return _render_beta_dashboard(
                settings,
                account=account,
                usage=usage,
                message=str(exc),
                message_tone="error",
            )
        return _render_beta_dashboard(
            settings,
            account=issued_key.account,
            usage=issued_key.usage,
            new_api_key=issued_key.api_key,
            message="API key generated. Export `POLICYNIM_TOKEN` before connecting your client.",
            message_tone="success",
        )

    @server.custom_route(_BETA_LOGOUT_PATH, methods=["POST"], include_in_schema=False)
    async def beta_logout(request: Request) -> Response:
        request.session.clear()
        return RedirectResponse(_BETA_PATH, status_code=302)


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


def _derive_beta_url(settings: Settings) -> str | None:
    if settings.mcp_public_base_url is None:
        return None
    return str(settings.mcp_public_base_url).rstrip("/") + _BETA_PATH


def _beta_asset_path(filename: str) -> Path:
    return resolve_asset_path("beta", filename)


def _beta_template_root() -> Path:
    return resolve_template_root()


@lru_cache(maxsize=1)
def _beta_template_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_beta_template_root())),
        autoescape=select_autoescape(
            enabled_extensions=("html", "j2"),
            default=True,
            default_for_string=True,
        ),
    )


def _render_beta_asset(filename: str, *, media_type: str) -> Response:
    asset_path = _beta_asset_path(filename)
    if not asset_path.is_file():
        return Response("Missing beta asset.", status_code=404)
    return FileResponse(
        asset_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _beta_notice_context(*, title: str, message: str, tone: str) -> dict[str, str]:
    return {
        "title": title,
        "message": message,
        "tone": tone,
    }


def _beta_command_card_context(
    *,
    title: str,
    description: str,
    command: str,
    button_label: str = "Copy command",
) -> dict[str, str]:
    return {
        "title": title,
        "description": description,
        "command": command,
        "button_label": button_label,
    }


def _beta_page_context(*, page_class: str) -> dict[str, object]:
    return {
        "document_title": "PolicyNIM Hosted Beta",
        "page_class": page_class,
        "favicon_url": _BETA_LIGHT_LOGO_ROUTE,
        "beta_css_url": _BETA_CSS_ROUTE,
        "beta_theme_init_js_url": _BETA_THEME_INIT_JS_ROUTE,
        "beta_page_js_url": _BETA_PAGE_JS_ROUTE,
        "light_logo_url": _BETA_LIGHT_LOGO_ROUTE,
        "dark_logo_url": _BETA_DARK_LOGO_ROUTE,
    }


def _render_beta_template(
    *,
    template_name: str,
    context: dict[str, object],
    status_code: int = 200,
) -> HTMLResponse:
    template = _beta_template_environment().get_template(template_name)
    return HTMLResponse(template.render(**context), status_code=status_code)


def _beta_usage_percent(usage: BetaUsageSnapshot) -> int:
    return max(0, min(100, round((usage.request_count / usage.quota) * 100)))


def _render_beta_landing(
    settings: Settings,
    *,
    message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    portal_url = _derive_beta_url(settings) or _BETA_PATH
    mcp_url = _derive_mcp_url(settings) or _STREAMABLE_HTTP_PATH
    notices: list[dict[str, str]] = []
    if message:
        notices.append(
            _beta_notice_context(
                title="Attention required",
                message=message,
                tone="error",
            )
        )
    context: dict[str, object] = _beta_page_context(page_class="beta-page--landing")
    context.update(
        {
            "portal_url": portal_url,
            "mcp_url": mcp_url,
            "github_start_path": _AUTH_GITHUB_START_PATH,
            "notices": notices,
            "steps": [
                {
                    "index": 1,
                    "card_class": "beta-card beta-card--emphasis",
                    "title": "Authenticate with GitHub",
                    "description": (
                        "Start the hosted beta session from the GitHub OAuth flow. PolicyNIM "
                        "stores the portal session and keeps the MCP endpoint locked behind "
                        "bearer auth."
                    ),
                },
                {
                    "index": 2,
                    "card_class": "beta-card",
                    "title": "Issue or rotate your API key",
                    "description": (
                        "The dashboard reveals the full secret once, along with the current key "
                        "prefix and the latest issuance timestamp for quick recovery."
                    ),
                },
                {
                    "index": 3,
                    "card_class": "beta-card",
                    "title": "Paste the generated client commands",
                    "description": (
                        "Once signed in, the portal shows ready-to-run commands for both Codex "
                        "and Claude Code so the hosted MCP route is connected without any manual "
                        "command assembly."
                    ),
                },
            ],
        }
    )
    return _render_beta_template(
        template_name="beta/landing.html.j2",
        context=context,
        status_code=status_code,
    )


def _render_beta_dashboard(
    settings: Settings,
    *,
    account: BetaAccount,
    usage: BetaUsageSnapshot,
    new_api_key: str | None = None,
    message: str | None = None,
    message_tone: str = "warning",
) -> HTMLResponse:
    portal_url = _derive_beta_url(settings) or _BETA_PATH
    mcp_url = _derive_mcp_url(settings) or _STREAMABLE_HTTP_PATH
    current_key = account.api_key_prefix or "No active key"
    current_key_created = (
        account.api_key_created_at.isoformat() if account.api_key_created_at is not None else "N/A"
    )
    usage_text = f"{usage.request_count} / {usage.quota} requests, {usage.remaining} remaining."
    usage_percent = _beta_usage_percent(usage)
    status_class = f"beta-status-pill beta-status-pill--{account.status}"
    codex_command = (
        f"codex mcp add policynim --url {mcp_url} --bearer-token-env-var POLICYNIM_TOKEN"
    )
    claude_command = (
        "claude mcp add --transport http policynim "
        f'{mcp_url} --header "Authorization: Bearer $POLICYNIM_TOKEN"'
    )

    notices: list[dict[str, str]] = []
    if account.status != "active":
        notices.append(
            _beta_notice_context(
                title="Account suspended",
                message=("Existing API keys will be rejected until the account is resumed."),
                tone="warning",
            )
        )
    if message:
        notice_title = "Portal update" if message_tone == "success" else "Action required"
        notices.append(
            _beta_notice_context(
                title=notice_title,
                message=message,
                tone=message_tone,
            )
        )
    new_key_context: dict[str, str] | None = None
    if new_api_key is not None:
        export_command = f"export POLICYNIM_TOKEN={new_api_key}"
        new_key_context = {
            "button_label": "Copy export",
            "export_command": export_command,
        }

    context: dict[str, object] = _beta_page_context(page_class="beta-page--dashboard")
    context.update(
        {
            "portal_url": portal_url,
            "mcp_url": mcp_url,
            "status_class": status_class,
            "status_title": account.status.title(),
            "account": {
                "github_login": account.github_login,
                "email": account.email or "Not available",
                "status": account.status,
                "current_key": current_key,
                "current_key_created": current_key_created,
            },
            "usage": {
                "text": usage_text,
                "percent": usage_percent,
                "usage_date": usage.usage_date.isoformat(),
            },
            "api_key_regenerate_path": _BETA_API_KEY_REGENERATE_PATH,
            "logout_path": _BETA_LOGOUT_PATH,
            "notices": notices,
            "new_key": new_key_context,
            "commands": [
                _beta_command_card_context(
                    title="Connect Codex",
                    description=(
                        "Add the hosted PolicyNIM MCP endpoint to Codex using the generated "
                        "bearer token environment variable."
                    ),
                    command=codex_command,
                ),
                _beta_command_card_context(
                    title="Connect Claude Code",
                    description=(
                        "Register the same hosted MCP endpoint in Claude Code and pass the "
                        "bearer token through the Authorization header."
                    ),
                    command=claude_command,
                ),
            ],
        }
    )
    return _render_beta_template(template_name="beta/dashboard.html.j2", context=context)


def _client_address(request: Request, *, trust_forwarded_headers: bool = False) -> str:
    if trust_forwarded_headers:
        forwarded_ip = _forwarded_client_address(request.headers)
        if forwarded_ip is not None:
            return forwarded_ip

    client = request.client
    if client is None or not client.host:
        return "unknown"
    return client.host


def _forwarded_client_address(headers: Headers) -> str | None:
    x_forwarded_for = headers.get("x-forwarded-for")
    if x_forwarded_for is not None:
        for candidate in x_forwarded_for.split(","):
            forwarded_ip = candidate.strip()
            if forwarded_ip:
                return forwarded_ip

    forwarded = headers.get("forwarded")
    if forwarded is None:
        return None

    for forwarded_value in forwarded.split(","):
        for attribute in forwarded_value.split(";"):
            key, separator, value = attribute.partition("=")
            if separator and key.strip().lower() == "for":
                forwarded_ip = value.strip().strip('"')
                if forwarded_ip.startswith("[") and "]" in forwarded_ip:
                    return forwarded_ip[1 : forwarded_ip.index("]")]
                if forwarded_ip:
                    return forwarded_ip.removeprefix("for=").split(":")[0]
    return None


def _require_beta_session_account_id(request: Request) -> int | None:
    raw_value = request.session.get(_BETA_ACCOUNT_SESSION_KEY)
    try:
        if raw_value is None:
            return None
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _beta_session_https_only(settings: Settings) -> bool:
    """Use secure beta session cookies for HTTPS-hosted deployments."""
    if settings.mcp_public_base_url is None:
        return False
    return settings.mcp_public_base_url.scheme == "https"


def _build_beta_auth_service(settings: Settings) -> BetaAuthService | None:
    if not settings.beta_signup_enabled and not settings.mcp_require_auth:
        return None
    try:
        return create_beta_auth_service(settings)
    except Exception as exc:
        if settings.beta_signup_enabled or not settings.mcp_bearer_tokens:
            raise ConfigurationError(
                "Hosted beta auth initialization failed. Check "
                "`POLICYNIM_BETA_AUTH_DB_PATH`, GitHub OAuth settings, "
                "and the writable auth volume."
            ) from exc
        LOGGER.exception(
            "Hosted beta auth initialization failed. "
            "Continuing with env-issued break-glass tokens only."
        )
        return None


class _BearerProtectedASGIApp:
    """Protect the MCP HTTP route with exact-match bearer token auth."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        protected_path: str,
        valid_tokens: list[str],
        beta_auth_service: BetaAuthService | None,
    ) -> None:
        self._app = app
        self._protected_path = protected_path
        self._valid_tokens = set(valid_tokens)
        self._beta_auth_service = beta_auth_service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != self._protected_path:
            await self._app(scope, receive, send)
            return

        token = _extract_bearer_token(scope)
        auth_result: str | None = None
        response: JSONResponse | None = None
        if token is not None and token in self._valid_tokens:
            auth_result = "authorized"
        elif self._beta_auth_service is not None:
            decision = self._beta_auth_service.authenticate_api_key(token=token)
            if decision.status == "authorized":
                auth_result = "authorized"
            elif decision.status == "suspended":
                auth_result = "suspended"
                response = JSONResponse({"error": "Account suspended."}, status_code=403)
            elif decision.status == "quota_exceeded":
                auth_result = "quota_exceeded"
                response = JSONResponse({"error": "Quota exceeded."}, status_code=429)
            else:
                auth_result = "unauthorized"
                response = JSONResponse({"error": "Unauthorized."}, status_code=401)
        else:
            auth_result = "unauthorized"
            response = JSONResponse({"error": "Unauthorized."}, status_code=401)

        if auth_result != "authorized":
            _emit_hosted_event(
                "mcp.auth",
                auth_result=auth_result or "unauthorized",
                tool_name=None,
                latency_ms=None,
                upstream_failure_class=None,
                request_id=None,
            )
            assert response is not None
            await response(scope, receive, send)
            return

        token_state = _HOSTED_AUTH_RESULT.set(auth_result)
        try:
            await self._app(scope, receive, send)
        finally:
            _HOSTED_AUTH_RESULT.reset(token_state)


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
    beta_auth_service = _build_beta_auth_service(settings)
    server = _create_mcp_server(settings, beta_auth_service=beta_auth_service)
    app = server.streamable_http_app()
    if settings.beta_signup_enabled:
        assert settings.beta_session_secret is not None
        app = SessionMiddleware(
            app,
            secret_key=settings.beta_session_secret,
            same_site="lax",
            https_only=_beta_session_https_only(settings),
        )
    if not settings.mcp_require_auth:
        return app
    return _BearerProtectedASGIApp(
        app,
        protected_path=server.settings.streamable_http_path,
        valid_tokens=settings.mcp_bearer_tokens,
        beta_auth_service=beta_auth_service,
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


def run_server(transport: str = "stdio") -> None:
    """Run the PolicyNIM MCP server."""
    if transport not in SUPPORTED_TRANSPORTS:
        allowed = ", ".join(SUPPORTED_TRANSPORTS)
        raise ValueError(f"Transport must be one of: {allowed}.")

    settings = get_settings()
    if transport == "streamable-http":
        _configure_hosted_logger()
        _ensure_streamable_http_port_available(settings.mcp_host, settings.mcp_port)
        ensure_hosted_runtime_ready(settings, rebuild_if_missing=True)
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
