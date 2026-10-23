"""Application composition and readiness guidance for index readers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from policynim.contracts import IndexStore
from policynim.errors import (
    ConfigurationError,
    GenerationError,
    IndexCompatibilityError,
    MissingIndexError,
)
from policynim.operations import close_components
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings
from policynim.storage.index_readiness import inspect_index_readiness
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
        if settings.vector_backend != "sqlite":
            from policynim.services.vector_storage import create_vector_backend
            from policynim.storage.external_vectors import ExternalVectorIndexStore
            from policynim.storage.vectors import vector_namespace

            return ExternalVectorIndexStore(
                path=snapshot.index_path,
                embedding_identity=identity,
                generation_snapshot=snapshot,
                backend=create_vector_backend(settings),
                namespace=vector_namespace(settings.vector_namespace, snapshot.generation_id),
            )
        return SQLiteVecIndexStore(
            path=snapshot.index_path, embedding_identity=identity, generation_snapshot=snapshot
        )
    except BaseException as error:
        close_components(snapshot, primary_error=error)
        raise


def require_index_ready(index_store: IndexStore, *, missing_message: str) -> None:
    """Refuse unusable indexes before a service starts provider work."""
    report = inspect_index_readiness(index_store)
    if report.state == "ready":
        return
    if isinstance(getattr(index_store, "generation_id", None), str):
        detail = report.reason
        if report.state == "unreadable":
            detail += " Correct access or contention; don't rebuild to diagnose permissions."
        raise GenerationError("read", detail)
    if report.state in {"incompatible", "incomplete"}:
        raise IndexCompatibilityError(report.reason)
    if report.state == "missing":
        raise MissingIndexError(missing_message)
    if report.state == "unreadable":
        raise MissingIndexError(
            f"{report.reason} Preserve existing data. Run `policynim doctor` for recovery; "
            "correct access or contention and don't rebuild to diagnose permissions.",
            failure_class="index_unreadable",
        )
    raise MissingIndexError(
        f"{report.reason} Preserve existing sources, index, and runtime rules. "
        "Run `policynim doctor` for recovery. Run `policynim ingest` only from verified sources "
        "into separate POLICYNIM_INDEX_DB_PATH and POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH "
        "destinations; validate before activation.",
        failure_class=f"index_{report.state}",
    )
