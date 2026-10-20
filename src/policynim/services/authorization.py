"""Resource-server identities and operation permissions, separate from provider keys."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import httpx
from anyio import to_thread
from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError

from policynim.errors import PolicyNIMError
from policynim.settings import Settings
from policynim.types import BetaAuthDecision

if TYPE_CHECKING:
    from policynim.services.beta_auth import BetaAuthService

LEGACY_ISSUER = "urn:policynim:legacy"


def principal_identity(issuer: str, subject: str) -> str:
    """Keep one user's allowance stable across clients and key rotation."""
    return hashlib.sha256(json.dumps([issuer, subject]).encode()).hexdigest()


def legacy_identity(*, token: str, account_id: int | None = None) -> tuple[str, str]:
    """Resolve legacy identity fields without granting authentication or operation authority."""
    subject = (
        str(account_id) if account_id is not None else hashlib.sha256(token.encode()).hexdigest()
    )
    return LEGACY_ISSUER, subject


READ_SCOPE = "policies:read"
EVALUATE_SCOPE = "policies:evaluate"
OPERATION_SCOPES = {
    "policy_search": frozenset({READ_SCOPE}),
    "policy_preflight": frozenset({READ_SCOPE, EVALUATE_SCOPE}),
    "policy_compile": frozenset({READ_SCOPE, EVALUATE_SCOPE}),
    "policy_preflight_evidence": frozenset({READ_SCOPE, EVALUATE_SCOPE}),
    "policy_resource": frozenset({READ_SCOPE}),
    "policy_review_prompt": frozenset({READ_SCOPE}),
}
SUPPORTED_SCOPES = [READ_SCOPE, EVALUATE_SCOPE]
LOGGER = logging.getLogger(__name__)


class AuthorizationError(PolicyNIMError):
    """Reject service work before touching an index or provider."""

    def __init__(
        self,
        message: str,
        *,
        code: Literal[
            "invalid_token", "insufficient_scope", "permission_denied"
        ] = "permission_denied",
        required_scopes: frozenset[str] = frozenset(),
    ) -> None:
        """Expose a stable denial code independently from the caller-facing message."""
        super().__init__(message)
        self.code = code
        self.required_scopes = required_scopes


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Carry verified authority without retaining a client access token."""

    issuer: str
    subject: str
    client_id: str
    scopes: frozenset[str]
    source: Literal["oauth", "beta_key", "operator_key"]
    expires_at: int | None = None
    credential_id: str | None = None


def authorize_operation(principal: AuthenticatedPrincipal, operation: str) -> None:
    """Require explicit known-operation scopes and an unexpired principal."""
    required = OPERATION_SCOPES.get(operation)
    if required is None:
        raise AuthorizationError("permission_denied: Unknown protected operation.")
    if principal.expires_at is not None and principal.expires_at <= time.time():
        raise AuthorizationError(
            "invalid_token: Authenticate again before retrying.", code="invalid_token"
        )
    if not required.issubset(principal.scopes):
        raise AuthorizationError(
            "insufficient_scope: Obtain a token with " + " ".join(sorted(required)) + ".",
            code="insufficient_scope",
            required_scopes=required,
        )


def principal_from_access_token(token: AccessToken) -> AuthenticatedPrincipal:
    """Copy the SDK's verified claims into an immutable service principal."""
    claims = token.claims or {}
    source = claims.get("policynim_source", "oauth")
    if source not in ("oauth", "beta_key", "operator_key"):
        raise AuthorizationError(
            "invalid_token: Unsupported authentication source.", code="invalid_token"
        )
    issuer = claims.get("iss")
    if not isinstance(issuer, str) or not issuer or not token.subject:
        raise AuthorizationError(
            "invalid_token: Missing authenticated identity.", code="invalid_token"
        )
    return AuthenticatedPrincipal(
        issuer=issuer,
        subject=token.subject,
        client_id=token.client_id,
        scopes=frozenset(token.scopes),
        source=source,
        expires_at=token.expires_at,
        credential_id=claims.get("policynim_credential_id"),
    )


def legacy_principal(*, token: str, account_id: int | None = None) -> AuthenticatedPrincipal:
    """Identify legacy credentials without retaining their secrets."""
    issuer, identity = legacy_identity(token=token, account_id=account_id)
    return AuthenticatedPrincipal(
        issuer=issuer,
        subject=identity,
        client_id="legacy-beta" if account_id is not None else "legacy-operator",
        scopes=frozenset(SUPPORTED_SCOPES),
        source="beta_key" if account_id is not None else "operator_key",
        credential_id=hashlib.sha256(token.encode()).hexdigest()
        if account_id is not None
        else None,
    )


class _IntrospectionClaims(BaseModel):
    """Validate the RFC 7662 fields this resource server requires."""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    active: StrictBool
    iss: StrictStr
    sub: StrictStr
    client_id: StrictStr
    aud: StrictStr | list[StrictStr]
    exp: StrictInt
    nbf: StrictInt | None = None
    scope: StrictStr
    token_type: StrictStr


class IntrospectionTokenVerifier:
    """Verify each HTTP request against an operator-owned external issuer."""

    def __init__(
        self,
        settings: Settings,
        *,
        beta_auth_service: BetaAuthService | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Bind one verifier to fixed issuer, resource and transport configuration."""
        self._settings = settings
        self._beta_auth_service = beta_auth_service
        self._transport = transport
        self.resource = str(settings.mcp_public_base_url).rstrip("/") + "/mcp"

    async def verify_token(self, token: str) -> AccessToken | None:
        """Fail closed on inactive, malformed, expired or foreign tokens."""
        if self._settings.mcp_oauth_allow_legacy_keys:
            decision = await self._legacy_decision(token)
            if decision is not None:
                if decision.status != "authorized":
                    return None
                if decision.source != "break_glass" and decision.account is None:
                    return None
                principal = legacy_principal(
                    token=token,
                    account_id=decision.account.account_id if decision.account else None,
                )
                return AccessToken(
                    token=token,
                    client_id=principal.client_id,
                    subject=principal.subject,
                    scopes=sorted(principal.scopes),
                    resource=self.resource,
                    claims={
                        "iss": principal.issuer,
                        "policynim_source": principal.source,
                        "policynim_credential_id": principal.credential_id,
                    },
                )
        issuer = self._settings.mcp_oauth_issuer_url
        client_id = self._settings.mcp_oauth_client_id
        secret = self._settings.mcp_oauth_client_secret
        if issuer is None or client_id is None or secret is None:
            return None
        endpoint = issuer.rstrip("/") + "/protocol/openid-connect/token/introspect"
        try:
            async with (
                asyncio.timeout(self._settings.mcp_oauth_timeout_seconds),
                httpx.AsyncClient(
                    timeout=self._settings.mcp_oauth_timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client,
            ):
                response = await client.post(
                    endpoint,
                    data={
                        "client_id": client_id,
                        "client_secret": secret.get_secret_value(),
                        "token": token,
                        "token_type_hint": "access_token",
                    },
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("active") is False:
                    return None
                claims = _IntrospectionClaims.model_validate(payload)
        except (httpx.HTTPError, TimeoutError):
            LOGGER.warning("OAuth introspection failed: issuer_unavailable.")
            return None
        except (ValueError, ValidationError):
            LOGGER.warning("OAuth introspection failed: invalid_response.")
            return None
        audiences = [claims.aud] if isinstance(claims.aud, str) else claims.aud
        now = time.time()
        if (
            not claims.active
            or claims.iss != issuer
            or not claims.sub.strip()
            or not claims.client_id.strip()
            or self.resource not in audiences
            or claims.exp <= now
            or (claims.nbf is not None and claims.nbf > now)
            or claims.token_type.lower() != "bearer"
        ):
            return None
        return AccessToken(
            token=token,
            client_id=claims.client_id,
            subject=claims.sub,
            scopes=claims.scope.split(),
            expires_at=claims.exp,
            resource=self.resource,
            claims={"iss": claims.iss, "policynim_source": "oauth"},
        )

    async def _legacy_decision(self, token: str) -> BetaAuthDecision | None:
        """Allow only explicitly enabled old credentials during a staged migration."""
        if token in self._settings.mcp_bearer_tokens:
            return BetaAuthDecision(status="authorized", source="break_glass")
        if self._beta_auth_service is not None and token.startswith("pnm_"):
            service = self._beta_auth_service
            recognized = False
            try:
                recognized = await to_thread.run_sync(
                    lambda: service.recognizes_api_key(token=token), abandon_on_cancel=False
                )
                if recognized:
                    return await to_thread.run_sync(
                        lambda: service.authenticate_api_key(token=token), abandon_on_cancel=False
                    )
            except (sqlite3.Error, OSError, PolicyNIMError):
                LOGGER.warning("Legacy credential lookup failed: storage_unavailable.")
                if recognized:
                    return BetaAuthDecision(status="unauthorized", source="api_key")
        return None
