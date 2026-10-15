"""HTTP operation challenges inside the SDK's authenticated route."""

from __future__ import annotations

import json

from mcp.server.auth.middleware.auth_context import get_access_token
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from policynim.services.authorization import OPERATION_SCOPES

MAX_AUTH_BODY_BYTES = 1024 * 1024


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
                challenge = (
                    'Bearer error="insufficient_scope", '
                    f'scope="{" ".join(sorted(required))}", '
                    f'resource_metadata="{self._metadata_url}"'
                )
                await JSONResponse(
                    {"error": "insufficient_scope"},
                    status_code=403,
                    headers={"WWW-Authenticate": challenge},
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

        await self._app(scope, replay_body, send)
