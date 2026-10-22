"""HTTP operation challenges inside the SDK's authenticated route."""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from policynim.services.authorization import OPERATION_SCOPES, AuthorizationError

MAX_AUTH_BODY_BYTES = 1024 * 1024


async def oauth_tool_declarations(ctx: ServerRequestContext, call_next: CallNext) -> HandlerResult:
    """Add ChatGPT's fields after SDK 2.2 sieves tools to the core MCP wire schema."""
    result = await call_next(ctx)
    if ctx.method == "tools/list" and isinstance(result, dict):
        for tool in result["tools"]:
            schemes = [{"type": "oauth2", "scopes": sorted(OPERATION_SCOPES[tool["name"]])}]
            tool["securitySchemes"] = schemes
            tool["_meta"] = {**(tool.get("_meta") or {}), "securitySchemes": schemes}
    return result


@dataclass
class _RequestAuthorizationState:
    """Share one request's denial outcome across SDK tasks and its service worker."""

    failure: AuthorizationError | None = None


_REQUEST_AUTHORIZATION: ContextVar[_RequestAuthorizationState | None] = ContextVar(
    "policynim_request_authorization", default=None
)


def record_authorization_failure(error: AuthorizationError) -> None:
    """Retain a denial for the HTTP boundary without passing HTTP state into services."""
    state = _REQUEST_AUTHORIZATION.get()
    if state is not None:
        state.failure = error


def authorization_challenge(error: AuthorizationError, metadata_url: str) -> str:
    """Build a sanitized challenge without interpolating exception or credential text."""
    parts = [f'error="{error.code}"', f'resource_metadata="{metadata_url}"']
    description = (
        "Authenticate again before retrying."
        if error.code == "invalid_token"
        else "Authorize the required operation scopes before retrying."
    )
    parts.append(f'error_description="{description}"')
    if error.required_scopes:
        parts.append(f'scope="{" ".join(sorted(error.required_scopes))}"')
    return "Bearer " + ", ".join(parts)


def authorization_tool_result(error: AuthorizationError, metadata_url: str) -> CallToolResult:
    """Expose a tool-level linking signal while retaining the HTTP challenge contract."""
    return CallToolResult(
        content=[TextContent(type="text", text="Authentication required. Authorize and retry.")],
        is_error=True,
        _meta={"mcp/www_authenticate": [authorization_challenge(error, metadata_url)]},
    )


def _challenge_response(
    error: AuthorizationError, metadata_url: str, tool_request: dict[str, Any] | None = None
) -> Response:
    """Return transport denials with a matching MCP tool result for tool invocations."""
    status = 401 if error.code == "invalid_token" else 403
    headers = {"WWW-Authenticate": authorization_challenge(error, metadata_url)}
    if tool_request is not None and "id" not in tool_request:
        return Response(status_code=status, headers=headers)
    body = {"error": error.code}
    if (
        tool_request is not None
        and isinstance(tool_request.get("id"), (str, int))
        and not isinstance(tool_request["id"], bool)
    ):
        body = {
            "jsonrpc": "2.0",
            "id": tool_request.get("id"),
            "result": authorization_tool_result(error, metadata_url).model_dump(
                by_alias=True, exclude_none=True
            ),
        }
    return JSONResponse(
        body,
        status_code=status,
        headers=headers,
    )


class OperationAuthorizationApp:
    """Challenge scope escalation before MCP dispatch or provider allocation."""

    def __init__(
        self, app: ASGIApp, *, metadata_url: str, body_timeout_seconds: float = 30.0
    ) -> None:
        """Wrap the protected route after the SDK's authentication middleware."""
        self._app = app
        self._metadata_url = metadata_url
        self._body_timeout_seconds = body_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Inspect a bounded request body and replay it unchanged after permission checks."""
        token = get_access_token()
        if scope["type"] != "http" or scope["method"] != "POST" or token is None:
            await self._app(scope, receive, send)
            return
        body = bytearray()
        request = Request(scope, receive)
        try:
            async with asyncio.timeout(self._body_timeout_seconds):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_AUTH_BODY_BYTES:
                        await JSONResponse({"error": "Request body too large."}, status_code=413)(
                            scope, receive, send
                        )
                        return
        except ClientDisconnect:
            return
        except TimeoutError:
            await JSONResponse(
                {
                    "error": "request_timeout",
                    "message": "Request body timed out. Retry the request.",
                },
                status_code=408,
            )(scope, receive, send)
            return
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("method") == "tools/call":
            params = payload.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            required = OPERATION_SCOPES.get(name) if isinstance(name, str) else None
            if required is not None and not required.issubset(token.scopes):
                await _challenge_response(
                    AuthorizationError(
                        "Insufficient operation scopes.",
                        code="insufficient_scope",
                        required_scopes=required,
                    ),
                    self._metadata_url,
                    payload,
                )(scope, receive, send)
                return
        delivered = False

        async def replay_body() -> Message:
            """Replay the consumed body once and preserve subsequent disconnect signals."""
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        state = _RequestAuthorizationState()
        context = _REQUEST_AUTHORIZATION.set(state)
        response_start: Message | None = None
        replaced = False

        async def send_authorized_response(message: Message) -> None:
            """Hold JSON response headers until a service denial can become a challenge."""
            nonlocal response_start, replaced
            if replaced:
                return
            if message["type"] == "http.response.start":
                response_start = message
                return
            if response_start is not None:
                if state.failure is not None:
                    replaced = True
                    await _challenge_response(
                        state.failure,
                        self._metadata_url,
                        payload
                        if isinstance(payload, dict) and payload.get("method") == "tools/call"
                        else None,
                    )(scope, receive, send)
                    return
                await send(response_start)
                response_start = None
            await send(message)

        try:
            await self._app(scope, replay_body, send_authorized_response)
        finally:
            _REQUEST_AUTHORIZATION.reset(context)
