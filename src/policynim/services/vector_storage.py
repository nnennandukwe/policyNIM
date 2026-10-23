"""Application composition for opt-in external vector indexes and explicit publication."""

from __future__ import annotations

from contextlib import closing
from typing import TYPE_CHECKING

from policynim.services.index_store import create_index_store
from policynim.settings import Settings
from policynim.storage.pgvector_vectors import PGVectorVectors
from policynim.storage.qdrant_vectors import QdrantVectors
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.storage.vectors import VectorBackend, VectorStorageError, vector_namespace

if TYPE_CHECKING:
    from policynim.services.generations import GenerationSnapshot


def create_vector_backend(settings: Settings) -> VectorBackend:
    """Select a storage-only adapter from already validated application settings."""
    if not settings.vector_url:
        raise VectorStorageError("An external vector URL is required.")
    if settings.vector_backend == "qdrant":
        return QdrantVectors(
            url=settings.vector_url,
            api_key=settings.vector_api_key.get_secret_value() if settings.vector_api_key else None,
            timeout=settings.vector_timeout_seconds,
        )
    if settings.vector_backend == "pgvector":
        return PGVectorVectors(
            url=settings.vector_url,
            password=settings.vector_password.get_secret_value()
            if settings.vector_password
            else None,
            timeout=settings.vector_timeout_seconds,
        )
    raise VectorStorageError("SQLite doesn't need an external vector adapter.")


def prepare_vector_generation(settings: Settings, snapshot: GenerationSnapshot) -> None:
    """Publish and verify a derived replica before the source generation becomes active."""
    if settings.vector_backend == "sqlite":
        return
    with closing(create_vector_backend(settings)) as backend:
        backend.probe()
        with closing(
            SQLiteVecIndexStore(
                path=snapshot.index_path, embedding_identity=snapshot.manifest.embedding
            )
        ) as canonical:
            backend.prepare(
                vector_namespace(settings.vector_namespace, snapshot.generation_id),
                snapshot.generation_id,
                canonical.vector_records(),
            )


def probe_vector_backend(settings: Settings) -> dict[str, object]:
    """Expose credential-safe, read-only connectivity status for the CLI and setup UI."""
    if settings.vector_backend != "sqlite":
        with closing(create_vector_backend(settings)) as backend:
            backend.probe()
    return {"status": "connected", "backend": settings.vector_backend, "generation_verified": False}


def inspect_vector_generation(settings: Settings) -> dict[str, object]:
    """Explicitly compare the complete replica while holding the canonical generation lease."""
    local = settings.model_copy(update={"vector_backend": "sqlite", "vector_url": None})
    with closing(create_index_store(local)) as store:
        store.validate_identity()
        if settings.vector_backend != "sqlite":
            assert store.generation_id is not None
            with closing(create_vector_backend(settings)) as backend:
                backend.validate(
                    vector_namespace(settings.vector_namespace, store.generation_id),
                    store.generation_id,
                    store.vector_records(),
                )
        return {
            "status": "ready",
            "backend": settings.vector_backend,
            "generation_id": store.generation_id,
            "chunk_count": store.count(),
        }
