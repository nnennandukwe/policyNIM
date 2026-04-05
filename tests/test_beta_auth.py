"""Tests for the hosted beta auth service."""

from __future__ import annotations

from pathlib import Path

import pytest

from policynim.errors import ProviderError
from policynim.services.beta_auth import BetaAuthService
from policynim.settings import Settings
from policynim.storage import AuthStore


class _StubResponse:
    def __init__(
        self,
        payload: object | None = None,
        *,
        json_error: ValueError | None = None,
    ) -> None:
        self._payload = payload
        self._json_error = json_error

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _StubGitHubClient:
    def __init__(
        self,
        *,
        post_response: _StubResponse | None = None,
        get_responses: dict[str, _StubResponse] | None = None,
        **kwargs: object,
    ) -> None:
        self._post_response = post_response or _StubResponse({})
        self._get_responses = get_responses or {}
        self.kwargs = kwargs

    def __enter__(self) -> _StubGitHubClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def post(self, url: str, data: dict[str, str]) -> _StubResponse:
        return self._post_response

    def get(self, url: str) -> _StubResponse:
        response = self._get_responses.get(url)
        if response is None:
            raise AssertionError(f"Unexpected URL requested: {url}")
        return response


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "mcp_require_auth": True,
            "beta_signup_enabled": True,
            "beta_session_secret": "session-secret",
            "beta_github_client_id": "github-client-id",
            "beta_github_client_secret": "github-client-secret",
            "mcp_public_base_url": "https://beta.example.com",
            "beta_auth_db_path": str(tmp_path / "auth.sqlite3"),
        }
    )


def _service(tmp_path: Path) -> BetaAuthService:
    settings = _settings(tmp_path)
    return BetaAuthService(
        store=AuthStore(path=settings.beta_auth_db_path),
        settings=settings,
    )


def test_exchange_code_rejects_invalid_json(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)

    monkeypatch.setattr(
        "policynim.services.beta_auth.httpx.Client",
        lambda **kwargs: _StubGitHubClient(
            post_response=_StubResponse(json_error=ValueError("invalid json")),
            **kwargs,
        ),
    )

    with pytest.raises(ProviderError, match="invalid JSON payload"):
        service._exchange_code_for_access_token("oauth-code")


def test_fetch_github_identity_rejects_missing_required_fields(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    monkeypatch.setattr(
        "policynim.services.beta_auth.httpx.Client",
        lambda **kwargs: _StubGitHubClient(
            get_responses={
                "https://api.github.com/user": _StubResponse({"login": "octocat"}),
                "https://api.github.com/user/emails": _StubResponse([]),
            },
            **kwargs,
        ),
    )

    with pytest.raises(ProviderError, match="valid user id"):
        service._fetch_github_identity("github-access-token")
