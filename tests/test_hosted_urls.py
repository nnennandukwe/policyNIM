"""Tests for hosted public URL helpers."""

from __future__ import annotations

import pytest

from policynim.errors import ConfigurationError
from policynim.hosted_urls import (
    AUTH_GITHUB_CALLBACK_PATH,
    BETA_PATH,
    HEALTH_PATH,
    STREAMABLE_HTTP_PATH,
    hosted_callback_url,
    hosted_health_url,
    hosted_mcp_url,
    hosted_portal_url,
)
from policynim.settings import Settings


def test_hosted_public_urls_share_one_normalized_origin() -> None:
    settings = Settings.model_validate({"mcp_public_base_url": "https://beta.example.com/"})

    assert hosted_mcp_url(settings) == f"https://beta.example.com{STREAMABLE_HTTP_PATH}"
    assert hosted_health_url(settings) == f"https://beta.example.com{HEALTH_PATH}"
    assert hosted_portal_url(settings) == f"https://beta.example.com{BETA_PATH}"
    assert hosted_callback_url(settings) == f"https://beta.example.com{AUTH_GITHUB_CALLBACK_PATH}"


def test_hosted_public_urls_require_a_public_base_url() -> None:
    settings = Settings.model_validate({})

    with pytest.raises(ConfigurationError, match="POLICYNIM_MCP_PUBLIC_BASE_URL"):
        hosted_mcp_url(settings)
