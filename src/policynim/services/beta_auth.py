"""Hosted beta auth and self-serve portal service."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from policynim.errors import ConfigurationError, PolicyNIMError, ProviderError
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import AuthStore
from policynim.types import BetaAccount, BetaAuthDecision, BetaIssuedApiKey, BetaUsageSnapshot

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
_USER_AGENT = "PolicyNIM Hosted Beta"


@dataclass(frozen=True)
class _GitHubIdentity:
    github_user_id: int
    github_login: str
    email: str | None


class BetaAuthService:
    """Own hosted beta account login, API-key issuance, and quota enforcement."""

    def __init__(self, *, store: AuthStore, settings: Settings) -> None:
        self._store = store
        self._settings = settings

    def close(self) -> None:
        """Release owned resources."""
        self._store.close()

    @property
    def mcp_url(self) -> str:
        """Return the public hosted MCP URL."""
        if self._settings.mcp_public_base_url is None:
            raise ConfigurationError("POLICYNIM_MCP_PUBLIC_BASE_URL must be configured.")
        return str(self._settings.mcp_public_base_url).rstrip("/") + "/mcp"

    @property
    def portal_url(self) -> str:
        """Return the public hosted beta portal URL."""
        if self._settings.mcp_public_base_url is None:
            raise ConfigurationError("POLICYNIM_MCP_PUBLIC_BASE_URL must be configured.")
        return str(self._settings.mcp_public_base_url).rstrip("/") + "/beta"

    @property
    def github_callback_url(self) -> str:
        """Return the configured GitHub OAuth callback URL."""
        if self._settings.mcp_public_base_url is None:
            raise ConfigurationError("POLICYNIM_MCP_PUBLIC_BASE_URL must be configured.")
        return str(self._settings.mcp_public_base_url).rstrip("/") + "/auth/github/callback"

    def list_accounts(self) -> list[BetaAccount]:
        """Return all hosted beta accounts."""
        return self._store.list_accounts()

    def get_account(self, account_id: int) -> BetaAccount | None:
        """Return one hosted beta account by id."""
        return self._store.get_account_by_id(account_id)

    def get_portal_usage(self, account_id: int) -> BetaUsageSnapshot:
        """Return the current UTC-day usage for the dashboard."""
        return self._store.get_usage_snapshot(
            account_id=account_id,
            usage_date=self._utc_now().date(),
            quota=self._settings.beta_daily_request_quota,
        )

    def build_github_authorize_url(self, *, state: str) -> str:
        """Return the GitHub OAuth authorize URL for the current deployment."""
        if self._settings.beta_github_client_id is None:
            raise ConfigurationError("POLICYNIM_BETA_GITHUB_CLIENT_ID must be configured.")
        return (
            _GITHUB_AUTHORIZE_URL
            + "?"
            + urlencode(
                {
                    "client_id": self._settings.beta_github_client_id,
                    "redirect_uri": self.github_callback_url,
                    "scope": "read:user user:email",
                    "state": state,
                }
            )
        )

    def complete_github_oauth(self, *, code: str) -> BetaAccount:
        """Exchange the GitHub code and create or update one beta account."""
        if not code.strip():
            raise ProviderError("GitHub OAuth callback did not include an authorization code.")
        access_token = self._exchange_code_for_access_token(code.strip())
        identity = self._fetch_github_identity(access_token)
        return self._store.upsert_account_from_github(
            github_user_id=identity.github_user_id,
            github_login=identity.github_login,
            email=identity.email,
            now=self._utc_now(),
        )

    def issue_api_key(self, *, account_id: int) -> BetaIssuedApiKey:
        """Create a new active API key for one hosted beta account."""
        account = self._require_account(account_id)
        if account.status != "active":
            raise PolicyNIMError(
                f"Hosted beta account {account.github_login!r} is suspended and cannot issue keys."
            )

        api_key = "pnm_" + secrets.token_urlsafe(24)
        account = self._store.rotate_api_key(
            account_id=account_id,
            key_prefix=api_key[:16],
            key_hash=_hash_api_key(api_key),
            now=self._utc_now(),
        )
        return BetaIssuedApiKey(
            account=account,
            api_key=api_key,
            usage=self.get_portal_usage(account_id),
        )

    def authenticate_api_key(self, *, token: str | None) -> BetaAuthDecision:
        """Authenticate one hosted beta API key and enforce the daily request quota."""
        if token is None or not token.strip():
            return BetaAuthDecision(status="unauthorized")

        account = self._store.authenticate_api_key(key_hash=_hash_api_key(token.strip()))
        if account is None:
            return BetaAuthDecision(status="unauthorized")
        if account.status != "active":
            return BetaAuthDecision(status="suspended", source="api_key", account=account)

        usage, allowed = self._store.consume_daily_quota(
            account_id=account.account_id,
            usage_date=self._utc_now().date(),
            quota=self._settings.beta_daily_request_quota,
            now=self._utc_now(),
        )
        if not allowed:
            return BetaAuthDecision(
                status="quota_exceeded",
                source="api_key",
                account=account,
                usage=usage,
            )
        return BetaAuthDecision(
            status="authorized",
            source="api_key",
            account=account,
            usage=usage,
        )

    def suspend_account(self, *, github_login: str) -> BetaAccount:
        """Suspend one hosted beta account by GitHub login."""
        account = self._require_account_by_login(github_login)
        return self._store.set_account_status(
            account_id=account.account_id,
            status="suspended",
            now=self._utc_now(),
        )

    def resume_account(self, *, github_login: str) -> BetaAccount:
        """Resume one hosted beta account by GitHub login."""
        account = self._require_account_by_login(github_login)
        return self._store.set_account_status(
            account_id=account.account_id,
            status="active",
            now=self._utc_now(),
        )

    def revoke_api_key(self, *, github_login: str) -> BetaAccount:
        """Revoke the current active key for one hosted beta account."""
        account = self._require_account_by_login(github_login)
        return self._store.revoke_active_key(account_id=account.account_id, now=self._utc_now())

    def _require_account(self, account_id: int) -> BetaAccount:
        account = self._store.get_account_by_id(account_id)
        if account is None:
            raise PolicyNIMError(f"Hosted beta account {account_id} does not exist.")
        return account

    def _require_account_by_login(self, github_login: str) -> BetaAccount:
        account = self._store.get_account_by_github_login(github_login)
        if account is None:
            raise PolicyNIMError(
                f"Hosted beta account with GitHub login {github_login!r} does not exist."
            )
        return account

    def _exchange_code_for_access_token(self, code: str) -> str:
        if self._settings.beta_github_client_id is None:
            raise ConfigurationError("POLICYNIM_BETA_GITHUB_CLIENT_ID must be configured.")
        if self._settings.beta_github_client_secret is None:
            raise ConfigurationError("POLICYNIM_BETA_GITHUB_CLIENT_SECRET must be configured.")

        try:
            with httpx.Client(timeout=30.0, headers={"Accept": "application/json"}) as client:
                response = client.post(
                    _GITHUB_TOKEN_URL,
                    data={
                        "client_id": self._settings.beta_github_client_id,
                        "client_secret": self._settings.beta_github_client_secret,
                        "code": code,
                        "redirect_uri": self.github_callback_url,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError("GitHub OAuth token exchange failed.") from exc

        payload = response.json()
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise ProviderError("GitHub OAuth token exchange did not return an access token.")
        return access_token

    def _fetch_github_identity(self, access_token: str) -> _GitHubIdentity:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": _USER_AGENT,
        }
        try:
            with httpx.Client(timeout=30.0, headers=headers) as client:
                profile_response = client.get(_GITHUB_USER_URL)
                profile_response.raise_for_status()
                profile_payload = profile_response.json()
                email = self._fetch_primary_verified_email(client)
        except httpx.HTTPError as exc:
            raise ProviderError("GitHub account lookup failed.") from exc

        github_user_id = int(profile_payload["id"])
        github_login = str(profile_payload["login"]).strip()
        if not github_login:
            raise ProviderError("GitHub account lookup returned an empty login.")
        profile_email = str(profile_payload.get("email") or "").strip() or None
        return _GitHubIdentity(
            github_user_id=github_user_id,
            github_login=github_login,
            email=email or profile_email,
        )

    def _fetch_primary_verified_email(self, client: httpx.Client) -> str | None:
        try:
            response = client.get(_GITHUB_EMAILS_URL)
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        payload = response.json()
        if not isinstance(payload, list):
            return None

        for candidate in payload:
            if not isinstance(candidate, dict):
                continue
            email = str(candidate.get("email") or "").strip()
            if not email or not bool(candidate.get("verified")):
                continue
            if bool(candidate.get("primary")):
                return email

        for candidate in payload:
            if not isinstance(candidate, dict):
                continue
            email = str(candidate.get("email") or "").strip()
            if email and bool(candidate.get("verified")):
                return email
        return None

    def _utc_now(self) -> datetime:
        return datetime.now(UTC)


def _hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_beta_auth_service(settings: Settings | None = None) -> BetaAuthService:
    """Build the default hosted beta auth service from application settings."""
    active_settings = settings or get_settings()
    return BetaAuthService(
        store=AuthStore(path=resolve_runtime_path(active_settings.beta_auth_db_path)),
        settings=active_settings,
    )
