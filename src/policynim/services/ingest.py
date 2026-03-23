"""Ingest service for building the local PolicyNIM index."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from policynim.contracts import Embedder
from policynim.ingest import chunk_policy_documents, load_policy_documents
from policynim.runtime_paths import resolve_corpus_root, resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import EmbeddedChunk, IngestResult, PolicyChunk


class IngestService:
    """Build a local vector index from the shipped policy corpus."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        index_store: LanceDBIndexStore,
        corpus_root: Path,
        embedding_model: str,
    ) -> None:
        self._embedder = embedder
        self._index_store = index_store
        self._corpus_root = corpus_root
        self._embedding_model = embedding_model

    def run(self) -> IngestResult:
        """Load, chunk, embed, and persist the policy corpus."""
        documents = load_policy_documents(self._corpus_root)
        chunks = chunk_policy_documents(documents)
        vectors = self._embedder.embed_documents([chunk.text for chunk in chunks])
        embedded_chunks = _attach_embeddings(chunks, vectors)
        self._index_store.replace(embedded_chunks)

        return IngestResult(
            corpus_path=self._corpus_root.as_posix(),
            index_uri=self._index_store.uri.as_posix(),
            table_name=self._index_store.table_name,
            embedding_model=self._embedding_model,
            document_count=len(documents),
            chunk_count=len(embedded_chunks),
            embedding_dimension=len(embedded_chunks[0].vector),
        )


def create_ingest_service(settings: Settings | None = None) -> IngestService:
    """Build the default ingest service from application settings."""
    active_settings = settings or get_settings()
    return IngestService(
        embedder=_create_default_embedder(active_settings),
        index_store=LanceDBIndexStore(
            uri=resolve_runtime_path(active_settings.lancedb_uri),
            table_name=active_settings.lancedb_table,
        ),
        corpus_root=resolve_corpus_root(active_settings.corpus_dir),
        embedding_model=active_settings.nvidia_embed_model,
    )


def _create_default_embedder(settings: Settings) -> Embedder:
    from policynim.providers import NVIDIAEmbedder

    return NVIDIAEmbedder.from_settings(settings)


def _attach_embeddings(
    chunks: Sequence[PolicyChunk],
    vectors: Sequence[Sequence[float]],
) -> list[EmbeddedChunk]:
    if len(chunks) != len(vectors):
        raise ValueError("Chunk and embedding counts must match.")

    embedded_chunks: list[EmbeddedChunk] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        embedded_chunks.append(
            EmbeddedChunk(
                **chunk.model_dump(),
                vector=[float(value) for value in vector],
            )
        )
    return embedded_chunks
