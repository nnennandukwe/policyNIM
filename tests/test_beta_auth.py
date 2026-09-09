"""Tests for the hosted beta auth service."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Event

import pytest

from policynim.errors import PolicyNIMError, ProviderError
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


def test_beta_auth_service_lists_filtered_audit_events(tmp_path: Path) -> None:
    """Return filtered hosted beta audit events through the service layer."""
    service = _service(tmp_path)
    account = service._store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat",
        email="octocat@example.com",
        now=service._utc_now(),
    )
    service._store.rotate_api_key(
        account_id=account.account_id,
        key_prefix="pnm_first",
        key_hash="stored-hash",
        now=service._utc_now(),
    )

    events = service.list_audit_events(github_login="octocat", event_type="api_key_rotated")

    assert len(events) == 1
    assert events[0].event_type == "api_key_rotated"
    assert events[0].github_login == "octocat"
    assert events[0].details == {"key_prefix": "pnm_first"}


def test_beta_auth_service_rejects_audit_filter_for_unknown_account(tmp_path: Path) -> None:
    """Reject audit-log account filters that do not match a hosted beta account."""
    service = _service(tmp_path)

    with pytest.raises(PolicyNIMError, match="does not exist"):
        service.list_audit_events(github_login="missing-user")


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [("rotate", "unauthorized"), ("revoke", "unauthorized"), ("suspend", "suspended")],
)
def test_authentication_observes_authority_changes_before_quota_admission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
    expected_status: str,
) -> None:
    """Reject authority withdrawn while authentication is preparing quota admission."""
    service = _service(tmp_path)
    operator_store = AuthStore(path=service._store.path)
    now = service._utc_now()
    account = operator_store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat",
        email=None,
        now=now,
    )
    issued = service.issue_api_key(account_id=account.account_id)
    admission_preparing = Event()
    continue_admission = Event()

    def paused_clock() -> datetime:
        """Pause at the former gap between active-key lookup and quota consumption."""
        admission_preparing.set()
        assert continue_admission.wait(timeout=5)
        return now

    monkeypatch.setattr(service, "_utc_now", paused_clock)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(service.authenticate_api_key, token=issued.api_key)
        try:
            assert admission_preparing.wait(timeout=5)
            if mutation == "rotate":
                operator_store.rotate_api_key(
                    account_id=account.account_id,
                    key_prefix="pnm_replacement",
                    key_hash="replacement-hash",
                    now=now,
                )
            elif mutation == "revoke":
                operator_store.revoke_active_key(account_id=account.account_id, now=now)
            else:
                operator_store.set_account_status(
                    account_id=account.account_id, status="suspended", now=now
                )
        finally:
            continue_admission.set()
        decision = pending.result(timeout=5)

    assert decision.status == expected_status
    assert decision.usage is None
    if mutation == "suspend":
        assert decision.account is not None
        assert decision.account.status == "suspended"
        assert decision.source == "api_key"
    else:
        assert decision.account is None
        assert decision.source is None
    usage = operator_store.get_usage_snapshot(
        account_id=account.account_id,
        usage_date=now.date(),
        quota=service._settings.beta_daily_request_quota,
    )
    assert usage.request_count == 0
    assert operator_store.list_audit_events(event_type="quota_exceeded") == []


def test_authentication_maps_committed_quota_and_account_state(tmp_path: Path) -> None:
    """Keep authorization status and precedence in the service's public contract."""
    settings = _settings(tmp_path).model_copy(update={"beta_daily_request_quota": 1})
    store = AuthStore(path=settings.beta_auth_db_path)
    service = BetaAuthService(store=store, settings=settings)
    account = store.upsert_account_from_github(
        github_user_id=123, github_login="octocat", email=None, now=service._utc_now()
    )
    issued = service.issue_api_key(account_id=account.account_id)

    allowed = service.authenticate_api_key(token=issued.api_key)
    assert allowed.status == "authorized"
    assert allowed.source == "api_key"
    assert allowed.account == issued.account
    assert allowed.usage is not None and allowed.usage.request_count == 1

    exhausted = service.authenticate_api_key(token=issued.api_key)
    assert exhausted.status == "quota_exceeded"
    assert exhausted.source == "api_key"
    assert exhausted.account == issued.account
    assert exhausted.usage is not None and exhausted.usage.remaining == 0

    service.suspend_account(github_login="octocat")
    suspended = service.authenticate_api_key(token=issued.api_key)
    assert suspended.status == "suspended"
    assert suspended.source == "api_key"
    assert suspended.account is not None and suspended.account.status == "suspended"
    assert suspended.usage is None

    service.revoke_api_key(github_login="octocat")
    revoked = service.authenticate_api_key(token=issued.api_key)
    assert revoked.status == "unauthorized"
    assert revoked.source is None
    assert revoked.account is None and revoked.usage is None
    assert service.get_portal_usage(account.account_id).request_count == 1
    assert len(service.list_audit_events(event_type="quota_exceeded")) == 1
