"""Helpers for deriving hosted public URLs from validated settings."""

from __future__ import annotations

from policynim.errors import ConfigurationError
from policynim.settings import Settings


def _require_public_base_url(settings: Settings) -> str:
    if settings.mcp_public_base_url is None:
        raise ConfigurationError("POLICYNIM_MCP_PUBLIC_BASE_URL must be configured.")
    return str(settings.mcp_public_base_url).rstrip("/")


def derive_mcp_url(settings: Settings) -> str | None:
    """Return the public hosted MCP URL when configured."""
    if settings.mcp_public_base_url is None:
        return None
    return _require_public_base_url(settings) + "/mcp"


def derive_beta_url(settings: Settings) -> str | None:
    """Return the public hosted beta portal URL when configured."""
    if settings.mcp_public_base_url is None:
        return None
    return _require_public_base_url(settings) + "/beta"


def derive_github_callback_url(settings: Settings) -> str:
    """Return the hosted GitHub OAuth callback URL."""
    return _require_public_base_url(settings) + "/auth/github/callback"
