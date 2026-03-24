"""Application settings for PolicyNIM."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from policynim.types import DEFAULT_TOP_K, TopK


class Settings(BaseSettings):
    """Validated environment-backed settings."""

    nvidia_api_key: str | None = Field(default=None, validation_alias="NVIDIA_API_KEY")
    policynim_env: str = Field(default="development", validation_alias="POLICYNIM_ENV")
    corpus_dir: Path | None = None
    lancedb_uri: Path = Path("data/lancedb")
    lancedb_table: str = "policy_chunks"
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
    mcp_port: Annotated[int, Field(ge=1, le=65535)] = 8000
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
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return stripped
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
