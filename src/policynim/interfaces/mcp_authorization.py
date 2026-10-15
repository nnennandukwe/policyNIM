"""HTTP operation challenges inside the SDK's authenticated route."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from policynim.services.authorization import OPERATION_SCOPES, AuthorizationError

MAX_AUTH_BODY_BYTES = 1024 * 1024


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


def _challenge_response(error: AuthorizationError, metadata_url: str) -> JSONResponse:
    """Build the same RFC 6750 challenge for HTTP and service-level rejections."""
    parts = [f'error="{error.code}"', f'resource_metadata="{metadata_url}"']
    if error.required_scopes:
        parts.append(f'scope="{" ".join(sorted(error.required_scopes))}"')
    return JSONResponse(
        {"error": error.code},
        status_code=401 if error.code == "invalid_token" else 403,
        headers={"WWW-Authenticate": "Bearer " + ", ".join(parts)},
    )


class OperationAuthorizationApp:
    """Challenge scope escalation before MCP dispatch or provider allocation."""

    def __init__(self, app: ASGIApp, *, metadata_url: str) -> None:
        """Wrap the protected route after the SDK's authentication middleware."""
        self._app = app
        self._metadata_url = metadata_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Inspect a bounded request body and replay it unchanged after permission checks."""
        token = get_access_token()
        if scope["type"] != "http" or scope["method"] != "POST" or token is None:
            await self._app(scope, receive, send)
            return
        body = bytearray()
        request = Request(scope, receive)
        try:
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_AUTH_BODY_BYTES:
                    await JSONResponse({"error": "Request body too large."}, status_code=413)(
                        scope, receive, send
                    )
                    return
        except ClientDisconnect:
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
                    await _challenge_response(state.failure, self._metadata_url)(
                        scope, receive, send
                    )
                    return
                await send(response_start)
                response_start = None
            await send(message)

        try:
            await self._app(scope, replay_body, send_authorized_response)
        finally:
            _REQUEST_AUTHORIZATION.reset(context)
