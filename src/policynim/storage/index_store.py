"""Index-store factory helpers for storage wiring."""

from __future__ import annotations

from pydantic import ValidationError

from policynim.errors import ConfigurationError
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import EmbeddingIdentity


def create_index_store(settings: Settings) -> SQLiteVecIndexStore:
    """Build the canonical SQLite-backed index store from application settings."""
    try:
        identity = EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        )
    except (ValidationError, ValueError) as exc:
        raise ConfigurationError(
            "Configure a nonempty POLICYNIM_NVIDIA_EMBED_MODEL and an HTTPS "
            "POLICYNIM_NVIDIA_BASE_URL without credentials, query parameters, or fragments."
        ) from exc
    return SQLiteVecIndexStore(
        path=resolve_runtime_path(settings.index_db_path), embedding_identity=identity
    )
