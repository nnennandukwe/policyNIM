"""Hosted URL compatibility through actual service properties and HTTP responses."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from policynim.errors import ConfigurationError
from policynim.interfaces import mcp as mcp_module
from policynim.services.beta_auth import BetaAuthService
from policynim.settings import Settings
from policynim.storage import AuthStore


@pytest.mark.parametrize(
    ("configured", "origin"),
    [
        ("https://EXAMPLE.test", "https://example.test"),
        ("https://example.test:443/", "https://example.test"),
        ("http://localhost:80/", "http://localhost"),
        ("https://example.test:8443", "https://example.test:8443"),
        ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
        ("https://[2001:db8::1]:8443/", "https://[2001:db8::1]:8443"),
    ],
)
def test_public_service_and_http_urls_preserve_validated_origins(
    tmp_path: Path, configured: str, origin: str
) -> None:
    """Beta URLs, OAuth redirect parameters and readiness keep the same canonical origin."""
    settings = Settings.model_validate(
        {
            "mcp_public_base_url": configured,
            "beta_github_client_id": "fixture-client",
            "generations_dir": tmp_path / "missing-generations",
        }
    )
    service = BetaAuthService(store=AuthStore(path=tmp_path / "auth.sqlite3"), settings=settings)
    try:
        assert service.mcp_url == origin + "/mcp"
        assert service.portal_url == origin + "/beta"
        assert service.github_callback_url == origin + "/auth/github/callback"
        authorize = parse_qs(
            urlsplit(service.build_github_authorize_url(state="state & value")).query
        )
        assert authorize["redirect_uri"] == [origin + "/auth/github/callback"]
        assert authorize["state"] == ["state & value"]
    finally:
        service.close()
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="http://localhost"
    ) as client:
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["mcp_url"] == origin + "/mcp"


def test_unset_public_url_keeps_optional_health_and_required_auth_behavior(tmp_path: Path) -> None:
    """Unset URLs remain optional for health and fail at the required beta-auth properties."""
    settings = Settings.model_validate({"generations_dir": tmp_path / "missing-generations"})
    service = BetaAuthService(store=AuthStore(path=tmp_path / "auth.sqlite3"), settings=settings)
    try:
        for property_name in ("mcp_url", "portal_url", "github_callback_url"):
            with pytest.raises(
                ConfigurationError, match="POLICYNIM_MCP_PUBLIC_BASE_URL must be configured"
            ):
                getattr(service, property_name)
    finally:
        service.close()
    with TestClient(mcp_module._build_streamable_http_app(settings)) as client:
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["mcp_url"] is None


@pytest.mark.parametrize(
    "configured",
    [
        "https://example.test/mcp",
        "https://example.test/nested/",
        "https://example.test/?mode=mcp",
        "https://example.test/#mcp",
        "https://user:password@example.test",
    ],
)
def test_invalid_public_origins_still_fail_settings_validation(configured: str) -> None:
    """URL construction doesn't broaden the existing service-origin trust boundary."""
    with pytest.raises(ValidationError):
        Settings.model_validate({"mcp_public_base_url": configured})
