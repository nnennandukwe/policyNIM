"""Loopback-only HTTP transport for the local setup service."""

from __future__ import annotations

import secrets
from typing import Literal

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from policynim.errors import PolicyNIMError
from policynim.runtime_paths import resolve_asset_path, resolve_template_root
from policynim.services.onboarding import OnboardingRequest, OnboardingService


def create_onboarding_app(service: OnboardingService, *, token: str, port: int) -> Starlette:
    """Require exact loopback origins and per-launch authorization before setup operations."""
    templates = Jinja2Templates(directory=str(resolve_template_root()))
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {"http://" + host for host in hosts}

    async def home(request: Request) -> Response:
        """Serve the packaged setup workspace without putting authorization into its markup."""
        return templates.TemplateResponse(request=request, name="setup.html.j2", context={})

    async def status(request: Request) -> Response:
        """Allow an authorized browser to recover process-local progress after disconnects."""
        return JSONResponse(service.status())

    async def operation(request: Request) -> Response:
        """Bound and validate transport input before delegating business behavior to a service."""
        if request.headers.get("origin") not in origins:
            return JSONResponse(
                {"status": "error", "message": "Setup origin wasn't accepted."}, 403
            )
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"status": "error", "message": "Send JSON setup choices."}, 415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                return JSONResponse(
                    {"status": "error", "message": "Setup request is too large."}, 413
                )
        try:
            choices = OnboardingRequest.model_validate_json(body)
            result = await run_in_threadpool(service.run, request.path_params["action"], choices)
            return JSONResponse(result)
        except ValidationError:
            return JSONResponse(
                {"status": "error", "message": "Check the setup fields and retry."}, 422
            )
        except PolicyNIMError as error:
            return JSONResponse({"status": "error", "message": str(error)}, 409)
        except (OSError, ValueError, RuntimeError):
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Setup couldn't finish. Existing configuration is "
                    "preserved. Check sources, storage access, and the selected generation "
                    "before retrying.",
                },
                409,
            )

    app = Starlette(
        routes=[
            Route("/", home),
            Route("/api/status", status),
            Route("/api/{action:str}", operation, methods=["POST"]),
            Mount("/assets", StaticFiles(directory=resolve_asset_path("setup", "setup.js").parent)),
        ]
    )

    async def protect(request: Request, call_next) -> Response:
        """Reject DNS rebinding and tokenless API access, and keep secrets outside caches."""
        if request.headers.get("host") not in hosts:
            return Response("Loopback setup host required.", 403)
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("x-setup-token", "").encode("utf-8"), token.encode("utf-8")
        ):
            return JSONResponse(
                {"status": "error", "message": "Open the setup launch URL again."}, 403
            )
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'"
                ),
            }
        )
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=protect)
    return app


def serve_onboarding(
    service: OnboardingService,
    *,
    port: int,
    browser: bool,
    log_level: Literal["info", "warning"] = "info",
) -> None:
    """Reserve a loopback socket before publishing its URL; never expose the server publicly."""
    import socket
    import webbrowser

    import typer
    import uvicorn

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
        actual_port = listener.getsockname()[1]
        token = secrets.token_urlsafe(32)
        url = f"http://127.0.0.1:{actual_port}/#token={token}"
        app = create_onboarding_app(service, token=token, port=actual_port)
        typer.echo("Open setup: " + url)
        typer.echo("Keep this local URL private. Stop setup with Ctrl+C when you're finished.")
        if browser:
            webbrowser.open(url)
        server = uvicorn.Server(uvicorn.Config(app, log_level=log_level, access_log=False))
        server.run(sockets=[listener])
