"""Application settings for PolicyNIM."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from policynim.errors import ConfigurationError
from policynim.types import DEFAULT_TOP_K, TopK


class Settings(BaseSettings):
    """Validated environment-backed settings."""

    nvidia_api_key: str | None = Field(default=None, validation_alias="NVIDIA_API_KEY")
    policynim_env: str = Field(default="development", validation_alias="POLICYNIM_ENV")
    corpus_dir: Path | None = None
    lancedb_uri: Path = Path("data/lancedb")
    lancedb_table: str = "policy_chunks"
    runtime_rules_artifact_path: Path = Path("data/runtime/runtime_rules.json")
    runtime_evidence_db_path: Path = Path("data/runtime/runtime_evidence.sqlite3")
    runtime_shell_timeout_seconds: Annotated[float, Field(gt=0)] = 300.0
    eval_workspace_dir: Path = Path("data/evals/workspace")
    default_top_k: TopK = DEFAULT_TOP_K
    embed_batch_size: Annotated[int, Field(ge=1)] = 32
    nvidia_chat_model: str = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    nvidia_embed_model: str = "nvidia/llama-nemotron-embed-1b-v2"
    nvidia_rerank_model: str = "nvidia/llama-nemotron-rerank-1b-v2"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_retrieval_base_url: str = "https://ai.api.nvidia.com/v1/retrieval"
    nvidia_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    nvidia_max_retries: Annotated[int, Field(ge=0)] = 2
    mcp_host: str = "127.0.0.1"
    mcp_port: Annotated[
        int,
        Field(
            ge=1,
            le=65535,
            validation_alias=AliasChoices("POLICYNIM_MCP_PORT", "PORT"),
        ),
    ] = 8000
    mcp_require_auth: bool = False
    mcp_bearer_tokens: Annotated[list[str], NoDecode] = Field(default_factory=list)
    mcp_public_base_url: AnyHttpUrl | None = None
    beta_signup_enabled: bool = False
    beta_auth_db_path: Path = Path("data/runtime/auth.sqlite3")
    beta_session_secret: str | None = None
    beta_github_client_id: str | None = None
    beta_github_client_secret: str | None = None
    beta_daily_request_quota: Annotated[int, Field(ge=1)] = 500
    beta_auth_rate_limit_max_attempts: Annotated[int, Field(ge=1)] = 20
    beta_auth_rate_limit_window_seconds: Annotated[int, Field(ge=1)] = 900
    eval_ui_port: Annotated[int, Field(ge=1, le=65535)] = 8001

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="POLICYNIM_",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("corpus_dir", mode="before")
    @classmethod
    def normalize_empty_corpus_dir(cls, value: Any) -> Any:
        """Treat empty configured corpus values as unset."""
        return _normalize_optional_setting(value)

    @field_validator("mcp_bearer_tokens", mode="before")
    @classmethod
    def normalize_bearer_tokens(cls, value: Any) -> Any:
        """Accept comma-separated bearer tokens from env or direct list input."""
        if value is None:
            return []
        if isinstance(value, str):
            return _dedupe_tokens(value.split(","))
        if isinstance(value, list):
            return _dedupe_tokens(value)
        if isinstance(value, tuple):
            return _dedupe_tokens(value)
        return value

    @field_validator("mcp_public_base_url", mode="before")
    @classmethod
    def normalize_empty_public_base_url(cls, value: Any) -> Any:
        """Treat empty configured hosted MCP base URLs as unset."""
        return _normalize_optional_setting(value)

    @field_validator(
        "beta_session_secret",
        "beta_github_client_id",
        "beta_github_client_secret",
        mode="before",
    )
    @classmethod
    def normalize_empty_beta_secrets(cls, value: Any) -> Any:
        """Treat empty hosted beta auth strings as unset."""
        return _normalize_optional_setting(value)

    @field_validator("runtime_rules_artifact_path", mode="before")
    @classmethod
    def validate_runtime_rules_artifact_path(cls, value: Any) -> Any:
        """Reject empty configured artifact paths before Path coercion."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH must not be empty.")
        return value

    @field_validator("runtime_evidence_db_path", mode="before")
    @classmethod
    def validate_runtime_evidence_db_path(cls, value: Any) -> Any:
        """Reject empty configured evidence DB paths before Path coercion."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("POLICYNIM_RUNTIME_EVIDENCE_DB_PATH must not be empty.")
        return value

    @field_validator("beta_auth_db_path", mode="before")
    @classmethod
    def validate_beta_auth_db_path(cls, value: Any) -> Any:
        """Reject empty configured auth db paths before Path coercion."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("POLICYNIM_BETA_AUTH_DB_PATH must not be empty.")
        return value

    @model_validator(mode="before")
    @classmethod
    def apply_hosted_runtime_defaults(cls, data: Any) -> Any:
        """Default hosted production runtimes to a wildcard bind when Railway injects PORT."""
        if not isinstance(data, dict):
            return data
        if "mcp_host" in data or "POLICYNIM_MCP_HOST" in data:
            return data
        if not os.getenv("PORT"):
            return data

        policynim_env = data.get("policynim_env") or data.get("POLICYNIM_ENV")
        if str(policynim_env).strip().lower() != "production":
            return data

        payload = dict(data)
        payload["mcp_host"] = "0.0.0.0"
        return payload

    @model_validator(mode="after")
    def validate_hosted_mcp_settings(self) -> Settings:
        """Validate hosted-only MCP settings without affecting stdio defaults."""
        if self.mcp_public_base_url is not None:
            if self.mcp_public_base_url.path not in ("", "/"):
                raise ValueError(
                    "POLICYNIM_MCP_PUBLIC_BASE_URL must be a service origin like "
                    "`https://host`, not a full `/mcp` URL."
                )
            if self.mcp_public_base_url.query or self.mcp_public_base_url.fragment:
                raise ValueError(
                    "POLICYNIM_MCP_PUBLIC_BASE_URL must not include a query string or fragment."
                )

        if self.mcp_require_auth and not self.mcp_bearer_tokens and not self.beta_signup_enabled:
            raise ValueError(
                "POLICYNIM_MCP_BEARER_TOKENS must be set when POLICYNIM_MCP_REQUIRE_AUTH is true "
                "and self-serve beta signup is disabled."
            )
        if self.mcp_require_auth and self.mcp_public_base_url is None:
            raise ValueError(
                "POLICYNIM_MCP_PUBLIC_BASE_URL must be set when POLICYNIM_MCP_REQUIRE_AUTH is true."
            )
        if self.beta_signup_enabled and not self.mcp_require_auth:
            raise ValueError(
                "POLICYNIM_MCP_REQUIRE_AUTH must be true when "
                "POLICYNIM_BETA_SIGNUP_ENABLED is true."
            )
        if self.beta_signup_enabled and self.beta_session_secret is None:
            raise ValueError(
                "POLICYNIM_BETA_SESSION_SECRET must be set when "
                "POLICYNIM_BETA_SIGNUP_ENABLED is true."
            )
        if self.beta_signup_enabled and self.beta_github_client_id is None:
            raise ValueError(
                "POLICYNIM_BETA_GITHUB_CLIENT_ID must be set when "
                "POLICYNIM_BETA_SIGNUP_ENABLED is true."
            )
        if self.beta_signup_enabled and self.beta_github_client_secret is None:
            raise ValueError(
                "POLICYNIM_BETA_GITHUB_CLIENT_SECRET must be set when "
                "POLICYNIM_BETA_SIGNUP_ENABLED is true."
            )
        if self.beta_signup_enabled and self.mcp_public_base_url is None:
            raise ValueError(
                "POLICYNIM_MCP_PUBLIC_BASE_URL must be set when "
                "POLICYNIM_BETA_SIGNUP_ENABLED is true."
            )
        return self


def _normalize_optional_setting(value: Any) -> Any:
    """Treat empty string settings as unset without rewriting non-string inputs."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return stripped
    return value


def _dedupe_tokens(values: list[str] | tuple[str, ...]) -> list[str]:
    """Trim, drop empties, and preserve token order while deduplicating."""
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value).strip()
        if not token or token in seen:
            continue
        deduped.append(token)
        seen.add(token)
    return deduped


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError(str(exc)) from exc
