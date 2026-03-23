"""Application settings for PolicyNIM."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from policynim.types import MAX_TOP_K, MIN_TOP_K

DEFAULT_TOP_K = 5


class Settings(BaseSettings):
    """Validated environment-backed settings."""

    nvidia_api_key: str | None = Field(default=None, alias="NVIDIA_API_KEY")
    policynim_env: str = Field(default="development", alias="POLICYNIM_ENV")
    default_top_k: int = Field(
        default=DEFAULT_TOP_K,
        alias="POLICYNIM_DEFAULT_TOP_K",
        ge=MIN_TOP_K,
        le=MAX_TOP_K,
    )
    nvidia_chat_model: str = Field(
        default="nvidia/llama-3.3-nemotron-super-49b-v1.5",
        alias="POLICYNIM_NVIDIA_CHAT_MODEL",
    )
    nvidia_embed_model: str = Field(
        default="nvidia/llama-nemotron-embed-1b-v2",
        alias="POLICYNIM_NVIDIA_EMBED_MODEL",
    )
    nvidia_rerank_model: str = Field(
        default="nvidia/llama-nemotron-rerank-1b-v2",
        alias="POLICYNIM_NVIDIA_RERANK_MODEL",
    )
    nvidia_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        alias="POLICYNIM_NVIDIA_BASE_URL",
    )
    nvidia_retrieval_base_url: str = Field(
        default="https://ai.api.nvidia.com/v1/retrieval",
        alias="POLICYNIM_NVIDIA_RETRIEVAL_BASE_URL",
    )
    mcp_host: str = Field(default="127.0.0.1", alias="POLICYNIM_MCP_HOST")
    mcp_port: int = Field(default=8000, alias="POLICYNIM_MCP_PORT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
