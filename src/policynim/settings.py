"""Application settings for PolicyNIM."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TOP_K = 5


class Settings(BaseSettings):
    """Validated environment-backed settings."""

    nvidia_api_key: str | None = Field(default=None, alias="NVIDIA_API_KEY")
    policynim_env: str = Field(default="development", alias="POLICYNIM_ENV")
    default_top_k: int = Field(default=DEFAULT_TOP_K)
    nvidia_chat_model: str = Field(default="nvidia/llama-3.3-nemotron-super-49b-v1.5")
    nvidia_embed_model: str = Field(default="nvidia/llama-nemotron-embed-1b-v2")
    nvidia_rerank_model: str = Field(default="nvidia/llama-nemotron-rerank-1b-v2")
    nvidia_base_url: str = Field(default="https://integrate.api.nvidia.com/v1")
    nvidia_retrieval_base_url: str = Field(default="https://ai.api.nvidia.com/v1/retrieval")
    mcp_host: str = Field(default="127.0.0.1")
    mcp_port: int = Field(default=8000)

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

