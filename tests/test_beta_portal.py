"""Tests for the hosted beta portal routes."""

from __future__ import annotations

from datetime import UTC, date, datetime

from starlette.datastructures import Headers
from starlette.testclient import TestClient

from policynim.interfaces import mcp as mcp_module
from policynim.settings import Settings
from policynim.types import BetaAccount, BetaIssuedApiKey, BetaUsageSnapshot


class StubBetaAuthService:
    """Minimal hosted beta auth service stub for portal route tests."""

    def __init__(self) -> None:
        self._account = BetaAccount(
            account_id=1,
            github_user_id=123,
            github_login="octocat",
            email="octocat@example.com",
            status="active",
            created_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            last_login_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            api_key_prefix="pnm_existing",
            api_key_created_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
        )
        self._usage = BetaUsageSnapshot(
            usage_date=date(2026, 4, 5),
            request_count=2,
            quota=500,
            remaining=498,
        )
        self.oauth_states: list[str] = []

    def build_github_authorize_url(self, *, state: str) -> str:
        self.oauth_states.append(state)
        return f"https://github.example.test/authorize?state={state}"

    def complete_github_oauth(self, *, code: str) -> BetaAccount:
        assert code == "oauth-code"
        return self._account

    def get_account(self, account_id: int) -> BetaAccount | None:
        if account_id != self._account.account_id:
            return None
        return self._account

    def get_portal_usage(self, account_id: int) -> BetaUsageSnapshot:
        assert account_id == self._account.account_id
        return self._usage

    def issue_api_key(self, *, account_id: int) -> BetaIssuedApiKey:
        assert account_id == self._account.account_id
        self._account = self._account.model_copy(update={"api_key_prefix": "pnm_rotated"})
        return BetaIssuedApiKey(
            account=self._account,
            api_key="pnm_new_secret",
            usage=self._usage,
        )


class ExplodingBetaAuthService(StubBetaAuthService):
    """Portal auth stub that raises an unexpected exception during callback."""

    def complete_github_oauth(self, *, code: str) -> BetaAccount:
        raise RuntimeError("unexpected oauth failure")


def _signup_settings(
    *,
    base_url: str = "https://beta.example.com",
    rate_limit_max_attempts: int = 20,
) -> Settings:
    return Settings.model_validate(
        {
            "mcp_require_auth": True,
            "beta_signup_enabled": True,
            "beta_session_secret": "session-secret",
            "beta_github_client_id": "github-client-id",
            "beta_github_client_secret": "github-client-secret",
            "beta_auth_rate_limit_max_attempts": rate_limit_max_attempts,
            "mcp_public_base_url": base_url,
        }
    )


def test_beta_portal_is_not_registered_when_signup_is_disabled() -> None:
    app = mcp_module._build_streamable_http_app(Settings())

    with TestClient(app) as client:
        response = client.get("/beta")

    assert response.status_code == 404


def test_beta_portal_renders_signed_out_landing(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_beta_auth_service",
        lambda settings: StubBetaAuthService(),
    )

    app = mcp_module._build_streamable_http_app(_signup_settings())

    with TestClient(app) as client:
        response = client.get("/beta")

    assert response.status_code == 200
    assert 'class="beta-page beta-page--landing"' in response.text
    assert "data-theme-toggle" in response.text
    assert f'href="{mcp_module._BETA_CSS_ROUTE}"' in response.text
    assert f'src="{mcp_module._BETA_THEME_INIT_JS_ROUTE}"' in response.text
    assert f'src="{mcp_module._BETA_PAGE_JS_ROUTE}"' in response.text
    assert "Continue with GitHub" in response.text
    assert "Connect in three moves" in response.text
    assert "policy-aware engineering preflight layer" in response.text
    assert "codex mcp add policynim" not in response.text


def test_beta_portal_login_flow_sets_session_and_renders_dashboard(monkeypatch) -> None:
    stub = StubBetaAuthService()
    monkeypatch.setattr(mcp_module, "create_beta_auth_service", lambda settings: stub)

    app = mcp_module._build_streamable_http_app(_signup_settings())

    with TestClient(app, base_url="https://testserver") as client:
        start = client.get("/auth/github/start", follow_redirects=False)
        assert start.status_code == 302
        assert start.headers["location"].startswith("https://github.example.test/authorize")

        callback = client.get(
            f"/auth/github/callback?state={stub.oauth_states[0]}&code=oauth-code",
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "/beta"

        dashboard = client.get("/beta")

    assert dashboard.status_code == 200
    assert 'class="beta-page beta-page--dashboard"' in dashboard.text
    assert "data-theme-toggle" in dashboard.text
    assert f'href="{mcp_module._BETA_CSS_ROUTE}"' in dashboard.text
    assert f'src="{mcp_module._BETA_THEME_INIT_JS_ROUTE}"' in dashboard.text
    assert f'src="{mcp_module._BETA_PAGE_JS_ROUTE}"' in dashboard.text
    assert "octocat" in dashboard.text
    assert "Copy command" in dashboard.text
    assert "codex mcp add policynim" in dashboard.text
    assert "claude mcp add --transport http policynim" in dashboard.text


def test_beta_portal_rejects_invalid_oauth_state(monkeypatch) -> None:
    stub = StubBetaAuthService()
    monkeypatch.setattr(mcp_module, "create_beta_auth_service", lambda settings: stub)

    app = mcp_module._build_streamable_http_app(_signup_settings())

    with TestClient(app, base_url="https://testserver") as client:
        client.get("/auth/github/start", follow_redirects=False)
        response = client.get(
            "/auth/github/callback?state=wrong-state&code=oauth-code",
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert 'class="beta-page beta-page--landing"' in response.text
    assert "OAuth state was missing or invalid" in response.text


def test_beta_portal_masks_unexpected_oauth_exceptions(monkeypatch) -> None:
    stub = ExplodingBetaAuthService()
    monkeypatch.setattr(mcp_module, "create_beta_auth_service", lambda settings: stub)

    app = mcp_module._build_streamable_http_app(_signup_settings())

    with TestClient(app, base_url="https://testserver") as client:
        client.get("/auth/github/start", follow_redirects=False)
        response = client.get(
            f"/auth/github/callback?state={stub.oauth_states[0]}&code=oauth-code",
            follow_redirects=False,
        )

    assert response.status_code == 502
    assert 'class="beta-page beta-page--landing"' in response.text
    assert "unexpected upstream error" in response.text


def test_beta_portal_regenerate_route_shows_new_api_key_once(monkeypatch) -> None:
    stub = StubBetaAuthService()
    monkeypatch.setattr(mcp_module, "create_beta_auth_service", lambda settings: stub)

    app = mcp_module._build_streamable_http_app(_signup_settings())

    with TestClient(app, base_url="https://testserver") as client:
        client.get("/auth/github/start", follow_redirects=False)
        client.get(
            f"/auth/github/callback?state={stub.oauth_states[0]}&code=oauth-code",
            follow_redirects=False,
        )
        response = client.post("/beta/api-key/regenerate")

    assert response.status_code == 200
    assert "pnm_new_secret" in response.text
    assert "Copy export" in response.text
    assert "export POLICYNIM_TOKEN=pnm_new_secret" in response.text


def test_beta_portal_serves_packaged_logo_assets(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_beta_auth_service",
        lambda settings: StubBetaAuthService(),
    )

    app = mcp_module._build_streamable_http_app(_signup_settings())

    with TestClient(app) as client:
        light = client.get(mcp_module._BETA_LIGHT_LOGO_ROUTE)
        dark = client.get(mcp_module._BETA_DARK_LOGO_ROUTE)
        css = client.get(mcp_module._BETA_CSS_ROUTE)
        theme_init_js = client.get(mcp_module._BETA_THEME_INIT_JS_ROUTE)
        page_js = client.get(mcp_module._BETA_PAGE_JS_ROUTE)
        favicon = client.get("/favicon.ico")

    assert light.status_code == 200
    assert light.headers["content-type"].startswith("image/png")
    assert dark.status_code == 200
    assert dark.headers["content-type"].startswith("image/jpeg")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert '[data-copy-state="copied"]::before' in css.text
    assert theme_init_js.status_code == 200
    assert theme_init_js.headers["content-type"].startswith("text/javascript")
    assert page_js.status_code == 200
    assert page_js.headers["content-type"].startswith("text/javascript")
    assert 'button.dataset.copyState = "copied"' in page_js.text
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/png")


def test_beta_portal_uses_secure_session_cookie_for_https_deployments(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_beta_auth_service",
        lambda settings: StubBetaAuthService(),
    )

    app = mcp_module._build_streamable_http_app(_signup_settings())

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/auth/github/start", follow_redirects=False)

    assert response.status_code == 302
    assert "secure" in response.headers["set-cookie"].lower()


def test_beta_portal_keeps_http_session_cookie_for_local_http_development(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_beta_auth_service",
        lambda settings: StubBetaAuthService(),
    )

    app = mcp_module._build_streamable_http_app(_signup_settings(base_url="http://localhost:8000"))

    with TestClient(app) as client:
        response = client.get("/auth/github/start", follow_redirects=False)

    assert response.status_code == 302
    assert "secure" not in response.headers["set-cookie"].lower()


def test_beta_portal_rate_limit_uses_forwarded_client_ip(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_module,
        "create_beta_auth_service",
        lambda settings: StubBetaAuthService(),
    )

    app = mcp_module._build_streamable_http_app(_signup_settings(rate_limit_max_attempts=1))

    with TestClient(app, base_url="https://testserver") as client:
        first = client.get(
            "/auth/github/start",
            follow_redirects=False,
            headers={"x-forwarded-for": "203.0.113.10"},
        )
        second = client.get(
            "/auth/github/start",
            follow_redirects=False,
            headers={"x-forwarded-for": "203.0.113.11"},
        )

    assert first.status_code == 302
    assert second.status_code == 302


def test_forwarded_client_address_parses_forwarded_header() -> None:
    headers = Headers({"forwarded": 'for="[2001:db8:cafe::17]:4711";proto=https'})

    assert mcp_module._forwarded_client_address(headers) == "2001:db8:cafe::17"
