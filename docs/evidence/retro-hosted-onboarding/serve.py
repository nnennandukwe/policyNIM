"""Serve the hosted onboarding pages on loopback with a disposable GitHub identity."""

from __future__ import annotations

import os
import secrets
from contextlib import chdir, suppress
from pathlib import Path
from socket import socket
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlencode

import uvicorn
from pydantic import AnyHttpUrl
from pydantic_settings import SettingsConfigDict

from policynim.errors import ProviderError
from policynim.interfaces.mcp import _build_streamable_http_app
from policynim.services.beta_auth import BetaAuthService, _GitHubIdentity
from policynim.settings import Settings
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import EmbeddedChunk, EmbeddingIdentity, PolicyMetadata


class FixtureSettings(Settings):
    """Keep local dotenv files outside the fixture's settings sources."""

    model_config = SettingsConfigDict(env_file=None)


def authorize(*, state: str) -> str:
    """Return the browser to the real callback without visiting GitHub."""
    return "/auth/github/callback?" + urlencode({"state": state, "code": "fixture"})


def exchange(code: str) -> str:
    """Accept the fixture code and let callers exercise the provider error page."""
    if code != "fixture":
        raise ProviderError("Fixture GitHub exchange failed. Retry sign-in.")
    return "fixture-access"


def serve() -> None:
    """Isolate storage and settings, then serve until interrupted."""
    identity = _GitHubIdentity(
        1,
        "onboarding-browser-fixture",
        "onboarding-browser-fixture-with-a-long-address@example.test",
    )
    with (
        TemporaryDirectory(prefix="policynim-onboarding-") as directory,
        chdir(directory),
        patch.dict(os.environ, clear=True),
        patch.object(BetaAuthService, "build_github_authorize_url", side_effect=authorize),
        patch.object(BetaAuthService, "_exchange_code_for_access_token", side_effect=exchange),
        patch.object(BetaAuthService, "_fetch_github_identity", return_value=identity),
        socket() as listener,
    ):
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        root = Path(directory)
        settings = FixtureSettings(
            mcp_require_auth=True,
            beta_signup_enabled=True,
            beta_session_secret=secrets.token_urlsafe(32),
            beta_github_client_id="fixture",
            beta_github_client_secret="fixture",
            beta_auth_db_path=root / "private" / "auth.sqlite3",
            index_db_path=root / "index.sqlite3",
            generations_dir=root / "generations",
            runtime_rules_artifact_path=root / "runtime-rules.json",
            runtime_evidence_db_path=root / "evidence.sqlite3",
            eval_workspace_dir=root / "evaluations",
            mcp_public_base_url=AnyHttpUrl(origin),
        )
        index = SQLiteVecIndexStore(
            path=settings.index_db_path,
            embedding_identity=EmbeddingIdentity(model="fixture", endpoint="https://example.test"),
        )
        try:
            index.replace(
                [
                    EmbeddedChunk(
                        chunk_id="fixture",
                        path="fixture.md",
                        section="Example",
                        lines="1-1",
                        text="Synthetic browser fixture.",
                        vector=[1.0, 0.0],
                        policy=PolicyMetadata(
                            policy_id="fixture", title="Example", doc_type="guidance", domain="demo"
                        ),
                    )
                ]
            )
        finally:
            index.close()
        app = _build_streamable_http_app(settings)
        print(f"{origin}/beta", flush=True)
        config = uvicorn.Config(app, access_log=False, log_level="error")
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        serve()
