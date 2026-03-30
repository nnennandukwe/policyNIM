"""Tests for settings loading and shared Pydantic invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from policynim.settings import Settings
from policynim.types import DocumentSection


def test_settings_reads_prefixed_env_and_nvidia_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICYNIM_DEFAULT_TOP_K", "7")
    monkeypatch.setenv("POLICYNIM_ENV", "staging")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")

    settings = Settings()

    assert settings.default_top_k == 7
    assert settings.policynim_env == "staging"
    assert settings.nvidia_api_key == "test-key"


def test_settings_still_allows_constructor_field_names() -> None:
    settings = Settings(default_top_k=6, mcp_port=9001)

    assert settings.default_top_k == 6
    assert settings.mcp_port == 9001


def test_settings_treats_empty_corpus_env_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICYNIM_CORPUS_DIR", "")

    settings = Settings()

    assert settings.corpus_dir is None


def test_settings_parses_csv_bearer_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICYNIM_MCP_BEARER_TOKENS", " token-a , token-b,token-a,, ")

    settings = Settings()

    assert settings.mcp_bearer_tokens == ["token-a", "token-b"]


def test_settings_requires_bearer_tokens_when_auth_is_enabled() -> None:
    with pytest.raises(
        ValidationError,
        match="POLICYNIM_MCP_BEARER_TOKENS must be set",
    ):
        Settings.model_validate(
            {
                "mcp_require_auth": True,
                "mcp_public_base_url": "https://beta.example.com",
            }
        )


def test_settings_requires_public_base_url_when_auth_is_enabled() -> None:
    with pytest.raises(
        ValidationError,
        match="POLICYNIM_MCP_PUBLIC_BASE_URL must be set",
    ):
        Settings(mcp_require_auth=True, mcp_bearer_tokens=["secret-token"])


def test_settings_rejects_full_mcp_public_url() -> None:
    with pytest.raises(
        ValidationError,
        match="service origin",
    ):
        Settings.model_validate({"mcp_public_base_url": "https://beta.example.com/mcp"})


def test_document_section_rejects_inverted_line_ranges() -> None:
    with pytest.raises(
        ValidationError,
        match="end_line must be greater than or equal to start_line",
    ):
        DocumentSection(
            heading_path=["Rules"],
            content="Impossible line range.",
            start_line=8,
            end_line=7,
        )
