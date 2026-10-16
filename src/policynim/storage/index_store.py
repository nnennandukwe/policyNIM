"""Index-store factory helpers for storage wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from policynim.errors import ConfigurationError
from policynim.operations import close_components
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import EmbeddingIdentity

if TYPE_CHECKING:
    from policynim.services.generations import GenerationSnapshot


def pinned_generation(index_store: object) -> GenerationSnapshot | None:
    """Recognize validated ownership while preserving legacy store protocols."""
    from policynim.services.generations import GenerationSnapshot

    snapshot = getattr(index_store, "generation_snapshot", None)
    return snapshot if isinstance(snapshot, GenerationSnapshot) else None


def create_index_store(settings: Settings, *, legacy: bool = False) -> SQLiteVecIndexStore:
    """Pin one complete generation; legacy=True is an explicit standalone inspection seam."""
    settings = settings.model_copy(deep=True)
    try:
        identity = EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        )
    except (ValidationError, ValueError) as exc:
        raise ConfigurationError(
            "Configure a nonempty POLICYNIM_NVIDIA_EMBED_MODEL and an HTTPS "
            "POLICYNIM_NVIDIA_BASE_URL without credentials, query parameters, or fragments."
        ) from exc
    if legacy:
        return SQLiteVecIndexStore(
            path=resolve_runtime_path(settings.index_db_path), embedding_identity=identity
        )
    from policynim.services.generations import GenerationStore

    snapshot = GenerationStore(
        root=resolve_runtime_path(settings.generations_dir), embedding=identity
    ).pin(settings.generation_id)
    try:
        return SQLiteVecIndexStore(
            path=snapshot.index_path, embedding_identity=identity, generation_snapshot=snapshot
        )
    except BaseException as error:
        close_components(snapshot, primary_error=error)
        raise
