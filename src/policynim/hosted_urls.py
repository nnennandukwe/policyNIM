"""Shared hosted-route constants and public URL builders."""

from __future__ import annotations

from policynim.errors import ConfigurationError
from policynim.settings import Settings

STREAMABLE_HTTP_PATH = "/mcp"
HEALTH_PATH = "/healthz"
BETA_PATH = "/beta"
AUTH_GITHUB_START_PATH = "/auth/github/start"
AUTH_GITHUB_CALLBACK_PATH = "/auth/github/callback"


def hosted_mcp_url(settings: Settings) -> str:
    """Return the public hosted MCP URL."""
    return _hosted_origin(settings) + STREAMABLE_HTTP_PATH


def hosted_health_url(settings: Settings) -> str:
    """Return the public hosted readiness URL."""
    return _hosted_origin(settings) + HEALTH_PATH


def hosted_portal_url(settings: Settings) -> str:
    """Return the public hosted beta portal URL."""
    return _hosted_origin(settings) + BETA_PATH


def hosted_callback_url(settings: Settings) -> str:
    """Return the public GitHub OAuth callback URL."""
    return _hosted_origin(settings) + AUTH_GITHUB_CALLBACK_PATH


def optional_hosted_mcp_url(settings: Settings) -> str | None:
    """Return the public hosted MCP URL when configured."""
    if settings.mcp_public_base_url is None:
        return None
    return hosted_mcp_url(settings)


def optional_hosted_portal_url(settings: Settings) -> str | None:
    """Return the public hosted beta portal URL when configured."""
    if settings.mcp_public_base_url is None:
        return None
    return hosted_portal_url(settings)


def _hosted_origin(settings: Settings) -> str:
    """Return the configured hosted origin without a trailing slash."""
    if settings.mcp_public_base_url is None:
        raise ConfigurationError("POLICYNIM_MCP_PUBLIC_BASE_URL must be configured.")
    return str(settings.mcp_public_base_url).rstrip("/")
