"""Hosted public URL helpers."""

from __future__ import annotations

from policynim.errors import ConfigurationError
from policynim.settings import Settings

_PUBLIC_BASE_URL_SETTING = "POLICYNIM_MCP_PUBLIC_BASE_URL"


def public_url(settings: Settings, path: str) -> str | None:
    """Return the hosted public URL for one route when configured."""
    if settings.mcp_public_base_url is None:
        return None
    return str(settings.mcp_public_base_url).rstrip("/") + _normalize_public_path(path)


def require_public_url(settings: Settings, path: str) -> str:
    """Return the hosted public URL for one route or fail when unset."""
    url = public_url(settings, path)
    if url is None:
        raise ConfigurationError(f"{_PUBLIC_BASE_URL_SETTING} must be configured.")
    return url


def public_base_url_uses_https(settings: Settings) -> bool:
    """Return whether the hosted public base URL should use secure-only cookies."""
    if settings.mcp_public_base_url is None:
        return False
    return settings.mcp_public_base_url.scheme == "https"


def _normalize_public_path(path: str) -> str:
    """Normalize one public route suffix for URL construction."""
    normalized = "/" + path.lstrip("/")
    if normalized == "/":
        return normalized
    return normalized.rstrip("/")
