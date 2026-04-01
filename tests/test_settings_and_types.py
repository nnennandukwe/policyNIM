"""Tests for settings loading and shared Pydantic invariants."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from policynim.settings import Settings
from policynim.types import DocumentSection, ParsedRuntimeRule


def load_settings_without_env_file(**overrides: Any) -> Settings:
    """Construct Settings without reading the repo .env file."""
    settings_type = cast(Any, Settings)
    return settings_type(_env_file=None, **overrides)


def test_settings_reads_prefixed_env_and_nvidia_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICYNIM_DEFAULT_TOP_K", "7")
    monkeypatch.setenv("POLICYNIM_ENV", "staging")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")

    settings = load_settings_without_env_file()

    assert settings.default_top_k == 7
    assert settings.policynim_env == "staging"
    assert settings.nvidia_api_key == "test-key"


def test_settings_still_allows_constructor_field_names() -> None:
    settings = Settings(default_top_k=6, mcp_port=9001)

    assert settings.default_top_k == 6
    assert settings.mcp_port == 9001


def test_settings_treats_empty_corpus_env_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICYNIM_CORPUS_DIR", "")

    settings = load_settings_without_env_file()

    assert settings.corpus_dir is None


def test_settings_parses_csv_bearer_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICYNIM_MCP_BEARER_TOKENS", " token-a , token-b,token-a,, ")

    settings = load_settings_without_env_file()

    assert settings.mcp_bearer_tokens == ["token-a", "token-b"]


def test_settings_uses_default_runtime_rules_artifact_path() -> None:
    settings = load_settings_without_env_file()

    assert settings.runtime_rules_artifact_path == Path("data/runtime/runtime_rules.json")


def test_settings_rejects_empty_runtime_rules_artifact_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH", "")

    with pytest.raises(
        ValidationError,
        match="POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH must not be empty",
    ):
        load_settings_without_env_file()


def test_settings_reads_railway_port_when_prefixed_port_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLICYNIM_MCP_PORT", raising=False)
    monkeypatch.setenv("PORT", "8123")

    settings = load_settings_without_env_file()

    assert settings.mcp_port == 8123


def test_settings_prefers_prefixed_mcp_port_over_railway_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLICYNIM_MCP_PORT", "9001")
    monkeypatch.setenv("PORT", "8123")

    settings = load_settings_without_env_file()

    assert settings.mcp_port == 9001


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


def test_parsed_runtime_rule_requires_exactly_one_matcher_family() -> None:
    with pytest.raises(
        ValidationError,
        match="exactly one non-empty matcher family",
    ):
        ParsedRuntimeRule(
            action="shell_command",
            effect="confirm",
            reason="Need approval.",
            path_globs=["scripts/*.sh"],
            command_regexes=["^make "],
            start_line=4,
            end_line=6,
        )
