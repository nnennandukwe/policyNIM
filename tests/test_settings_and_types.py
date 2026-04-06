"""Tests for settings loading and shared Pydantic invariants."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from policynim.settings import Settings
from policynim.types import (
    DocumentSection,
    HTTPRequestActionRequest,
    ParsedRuntimeRule,
    RuntimeActionRequest,
    RuntimeDecisionResult,
    RuntimeEvidenceExecutionSummary,
    RuntimeEvidenceSessionSummary,
    RuntimeExecutionEvidenceRecord,
    RuntimeExecutionResult,
    ShellCommandExecutionMetadata,
    ShellCommandExecutionRequest,
)


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


def test_settings_uses_default_runtime_evidence_db_path() -> None:
    settings = load_settings_without_env_file()

    assert settings.runtime_evidence_db_path == Path("data/runtime/runtime_evidence.sqlite3")


def test_settings_uses_default_runtime_shell_timeout_seconds() -> None:
    settings = load_settings_without_env_file()

    assert settings.runtime_shell_timeout_seconds == 300.0


def test_settings_rejects_empty_runtime_rules_artifact_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH", "")

    with pytest.raises(
        ValidationError,
        match="POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH must not be empty",
    ):
        load_settings_without_env_file()


def test_settings_rejects_empty_runtime_evidence_db_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLICYNIM_RUNTIME_EVIDENCE_DB_PATH", "")

    with pytest.raises(
        ValidationError,
        match="POLICYNIM_RUNTIME_EVIDENCE_DB_PATH must not be empty",
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


def test_settings_defaults_host_to_wildcard_for_production_railway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLICYNIM_MCP_HOST", raising=False)
    monkeypatch.setenv("POLICYNIM_ENV", "production")
    monkeypatch.setenv("PORT", "8123")

    settings = load_settings_without_env_file()

    assert settings.mcp_host == "0.0.0.0"


def test_settings_preserves_explicit_host_for_production_railway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLICYNIM_ENV", "production")
    monkeypatch.setenv("POLICYNIM_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8123")

    settings = load_settings_without_env_file()

    assert settings.mcp_host == "127.0.0.1"


def test_settings_keeps_loopback_host_outside_production_even_with_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLICYNIM_MCP_HOST", raising=False)
    monkeypatch.delenv("POLICYNIM_ENV", raising=False)
    monkeypatch.setenv("PORT", "8123")

    settings = load_settings_without_env_file()

    assert settings.mcp_host == "127.0.0.1"


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


def test_settings_allows_db_backed_auth_when_self_serve_signup_is_enabled() -> None:
    settings = Settings.model_validate(
        {
            "mcp_require_auth": True,
            "beta_signup_enabled": True,
            "beta_session_secret": "session-secret",
            "beta_github_client_id": "github-client-id",
            "beta_github_client_secret": "github-client-secret",
            "mcp_public_base_url": "https://beta.example.com",
        }
    )

    assert settings.beta_signup_enabled is True
    assert settings.mcp_bearer_tokens == []


def test_settings_rejects_empty_beta_auth_db_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICYNIM_BETA_AUTH_DB_PATH", "")

    with pytest.raises(ValidationError, match="POLICYNIM_BETA_AUTH_DB_PATH must not be empty"):
        load_settings_without_env_file()


def test_settings_requires_beta_session_secret_when_signup_is_enabled() -> None:
    with pytest.raises(ValidationError, match="POLICYNIM_BETA_SESSION_SECRET must be set"):
        Settings.model_validate(
            {
                "mcp_require_auth": True,
                "beta_signup_enabled": True,
                "beta_github_client_id": "github-client-id",
                "beta_github_client_secret": "github-client-secret",
                "mcp_public_base_url": "https://beta.example.com",
            }
        )


def test_settings_requires_mcp_auth_when_signup_is_enabled() -> None:
    with pytest.raises(ValidationError, match="POLICYNIM_MCP_REQUIRE_AUTH must be true"):
        Settings.model_validate(
            {
                "beta_signup_enabled": True,
                "beta_session_secret": "session-secret",
                "beta_github_client_id": "github-client-id",
                "beta_github_client_secret": "github-client-secret",
                "mcp_public_base_url": "https://beta.example.com",
            }
        )


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


def test_runtime_action_request_rejects_empty_shell_command_lists() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        TypeAdapter(RuntimeActionRequest).validate_python(
            {
                "kind": "shell_command",
                "task": "Run tests.",
                "cwd": ".",
                "command": [],
            }
        )


def test_http_request_action_rejects_malformed_urls() -> None:
    with pytest.raises(ValidationError, match="URL"):
        HTTPRequestActionRequest.model_validate(
            {
                "kind": "http_request",
                "task": "Call an HTTP endpoint.",
                "cwd": Path("."),
                "method": "GET",
                "url": "not-a-url",
            }
        )


def test_runtime_decision_result_rejects_invalid_decision_values() -> None:
    with pytest.raises(ValidationError, match="Input should be 'allow', 'confirm' or 'block'"):
        RuntimeDecisionResult.model_validate(
            {
                "request": {
                    "kind": "shell_command",
                    "task": "Run tests.",
                    "cwd": Path("."),
                    "command": ["make", "test"],
                },
                "decision": "maybe",
                "summary": "Unknown runtime outcome.",
            }
        )


def test_runtime_execution_result_accepts_sanitized_request_and_metadata() -> None:
    result = RuntimeExecutionResult.model_validate(
        {
            "execution_id": "exec-1",
            "session_id": "session-1",
            "request": {
                "kind": "shell_command",
                "task": "Run tests.",
                "cwd": "/tmp/workspace",
                "session_id": "session-1",
                "command": ["make", "test"],
            },
            "decision": "allow",
            "summary": "No runtime policy rules matched this action.",
            "matched_rules": [],
            "citations": [],
            "confirmation_outcome": "not_required",
            "execution_outcome": "allowed",
            "result_metadata": {
                "exit_code": 0,
                "duration_ms": 12.5,
            },
            "failure_class": None,
            "residual_uncertainty": None,
        }
    )

    assert result.request == ShellCommandExecutionRequest(
        kind="shell_command",
        task="Run tests.",
        cwd=Path("/tmp/workspace"),
        session_id="session-1",
        command=["make", "test"],
    )
    assert result.result_metadata == ShellCommandExecutionMetadata(exit_code=0, duration_ms=12.5)


def test_runtime_execution_evidence_record_accepts_terminal_event_payload() -> None:
    record = RuntimeExecutionEvidenceRecord.model_validate(
        {
            "event_id": "event-1",
            "execution_id": "exec-1",
            "session_id": "session-1",
            "created_at": "2026-04-05T12:00:00+00:00",
            "event_kind": "allowed",
            "request": {
                "kind": "shell_command",
                "task": "Run tests.",
                "cwd": "/tmp/workspace",
                "session_id": "session-1",
                "command": ["make", "test"],
            },
            "decision": "allow",
            "summary": "No runtime policy rules matched this action.",
            "matched_rules": [],
            "citations": [],
            "confirmation_outcome": "not_required",
            "execution_outcome": "allowed",
            "result_metadata": {
                "exit_code": 0,
                "duration_ms": 12.5,
            },
            "failure_class": None,
            "residual_uncertainty": None,
        }
    )

    assert record.request == ShellCommandExecutionRequest(
        kind="shell_command",
        task="Run tests.",
        cwd=Path("/tmp/workspace"),
        session_id="session-1",
        command=["make", "test"],
    )
    assert record.result_metadata == ShellCommandExecutionMetadata(exit_code=0, duration_ms=12.5)


def test_runtime_evidence_execution_summary_accepts_nullable_terminal_fields() -> None:
    summary = RuntimeEvidenceExecutionSummary.model_validate(
        {
            "execution_id": "exec-1",
            "action_kind": "shell_command",
            "task": "Run tests.",
            "decision": "allow",
            "summary": "No runtime policy rules matched this action.",
            "confirmation_outcome": "not_required",
            "execution_outcome": None,
            "failure_class": None,
            "started_at": "2026-04-05T12:00:00+00:00",
            "completed_at": None,
            "matched_rules": [],
            "citations": [],
        }
    )

    assert summary.execution_outcome is None
    assert summary.completed_at is None


def test_runtime_evidence_session_summary_accepts_aggregate_counts() -> None:
    summary = RuntimeEvidenceSessionSummary.model_validate(
        {
            "session_id": "session-1",
            "started_at": "2026-04-05T12:00:00+00:00",
            "completed_at": "2026-04-05T12:00:10+00:00",
            "event_count": 2,
            "execution_count": 1,
            "allowed_count": 1,
            "confirmed_count": 0,
            "blocked_count": 0,
            "refused_count": 0,
            "failed_count": 0,
            "incomplete_count": 0,
            "executions": [
                {
                    "execution_id": "exec-1",
                    "action_kind": "shell_command",
                    "task": "Run tests.",
                    "decision": "allow",
                    "summary": "No runtime policy rules matched this action.",
                    "confirmation_outcome": "not_required",
                    "execution_outcome": "allowed",
                    "failure_class": None,
                    "started_at": "2026-04-05T12:00:00+00:00",
                    "completed_at": "2026-04-05T12:00:10+00:00",
                    "matched_rules": [],
                    "citations": [],
                }
            ],
        }
    )

    assert summary.execution_count == 1
    assert summary.allowed_count == 1
    assert summary.executions[0].execution_outcome == "allowed"
