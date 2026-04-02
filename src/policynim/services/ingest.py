"""Ingest service for building the local PolicyNIM index."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from policynim.contracts import Embedder
from policynim.ingest import chunk_policy_documents, load_policy_documents
from policynim.runtime_paths import resolve_corpus_root, resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import (
    CompiledRuntimeRule,
    EmbeddedChunk,
    IngestResult,
    ParsedDocument,
    PolicyChunk,
    RuntimeRulesArtifact,
)


class _IngestIndexStore(Protocol):
    """Index-store surface required by ingest."""

    @property
    def uri(self) -> Path:
        """Return the underlying index URI."""
        ...

    @property
    def table_name(self) -> str:
        """Return the configured table name."""
        ...

    def replace(self, chunks: Sequence[EmbeddedChunk]) -> None:
        """Replace the local index contents with embedded chunks."""
        ...


class IngestService:
    """Build a local vector index from the shipped policy corpus."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        index_store: _IngestIndexStore,
        corpus_root: Path,
        embedding_model: str,
        runtime_rules_artifact_path: Path,
    ) -> None:
        self._embedder = embedder
        self._index_store = index_store
        self._corpus_root = corpus_root
        self._embedding_model = embedding_model
        self._runtime_rules_artifact_path = runtime_rules_artifact_path

    def run(self) -> IngestResult:
        """Load, chunk, embed, and persist the policy corpus."""
        documents = load_policy_documents(self._corpus_root)
        runtime_rules_artifact = _compile_runtime_rules_artifact(documents)
        chunks = chunk_policy_documents(documents)
        vectors = self._embedder.embed_documents([chunk.text for chunk in chunks])
        embedded_chunks = _attach_embeddings(chunks, vectors)
        staged_artifact_path = _stage_runtime_rules_artifact(
            runtime_rules_artifact,
            self._runtime_rules_artifact_path,
        )

        try:
            self._index_store.replace(embedded_chunks)
            _finalize_runtime_rules_artifact(
                staged_artifact_path,
                self._runtime_rules_artifact_path,
            )
        except Exception:
            _cleanup_staged_runtime_rules_artifact(staged_artifact_path)
            raise

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
        runtime_rules_artifact_path=resolve_runtime_path(
            active_settings.runtime_rules_artifact_path
        ),
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


def _compile_runtime_rules_artifact(documents: Sequence[ParsedDocument]) -> RuntimeRulesArtifact:
    """Compile parsed document rules into the persisted runtime artifact shape."""
    compiled_rules: list[CompiledRuntimeRule] = []
    for document in documents:
        for rule in document.runtime_rules:
            compiled_rules.append(
                CompiledRuntimeRule(
                    policy_id=document.metadata.policy_id,
                    title=document.metadata.title,
                    domain=document.metadata.domain,
                    source_path=document.source_path,
                    action=rule.action,
                    effect=rule.effect,
                    reason=rule.reason,
                    path_globs=list(rule.path_globs),
                    command_regexes=list(rule.command_regexes),
                    url_host_patterns=list(rule.url_host_patterns),
                    start_line=rule.start_line,
                    end_line=rule.end_line,
                )
            )
    return RuntimeRulesArtifact(rules=compiled_rules)


def _stage_runtime_rules_artifact(
    artifact: RuntimeRulesArtifact,
    destination: Path,
) -> Path:
    """Write the artifact to a sibling temp file before mutating the index."""
    if destination.exists() and destination.is_dir():
        raise OSError(f"Runtime rules artifact path {destination} must not be a directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        artifact.model_dump(mode="json"),
        indent=2,
        sort_keys=False,
    )
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(f"{serialized}\n")
        return Path(handle.name)


def _finalize_runtime_rules_artifact(staged_path: Path, destination: Path) -> None:
    """Atomically move a staged artifact into its final location."""
    staged_path.replace(destination)


def _cleanup_staged_runtime_rules_artifact(staged_path: Path) -> None:
    """Best-effort cleanup for staged artifact files after a failed ingest."""
    staged_path.unlink(missing_ok=True)
