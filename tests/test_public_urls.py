"""Tests for hosted public URL helpers."""

from __future__ import annotations

import pytest

from policynim.errors import ConfigurationError
from policynim.public_urls import public_base_url_uses_https, public_url, require_public_url
from policynim.settings import Settings


def test_public_url_builds_normalized_routes() -> None:
    """Append hosted routes without leaking double slashes."""
    settings = Settings.model_validate({"mcp_public_base_url": "https://beta.example.com"})

    assert public_url(settings, "/mcp") == "https://beta.example.com/mcp"
    assert public_url(settings, "beta/") == "https://beta.example.com/beta"


def test_require_public_url_rejects_missing_origin() -> None:
    """Require an explicit public base URL when callers depend on it."""
    with pytest.raises(ConfigurationError, match="POLICYNIM_MCP_PUBLIC_BASE_URL"):
        require_public_url(Settings(), "/mcp")


def test_public_base_url_uses_https_matches_scheme() -> None:
    """Report whether the configured public origin uses HTTPS."""
    https_settings = Settings.model_validate({"mcp_public_base_url": "https://beta.example.com"})
    http_settings = Settings.model_validate({"mcp_public_base_url": "http://beta.example.com"})

    assert public_base_url_uses_https(https_settings) is True
    assert public_base_url_uses_https(http_settings) is False
