"""Tests for settings loading and shared Pydantic invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from policynim.settings import Settings
from policynim.types import DocumentSection


def test_settings_reads_prefixed_env_and_nvidia_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICYNIM_DEFAULT_TOP_K", "7")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")

    settings = Settings()

    assert settings.default_top_k == 7
    assert settings.nvidia_api_key == "test-key"


def test_settings_still_allows_constructor_field_names() -> None:
    settings = Settings(default_top_k=6, mcp_port=9001)

    assert settings.default_top_k == 6
    assert settings.mcp_port == 9001


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
