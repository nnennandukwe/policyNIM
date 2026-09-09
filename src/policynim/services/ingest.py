"""Ingest service for building the local PolicyNIM index."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import TracebackType
from typing import Protocol

from policynim.contracts import Embedder
from policynim.errors import IndexCompatibilityError
from policynim.ingest import chunk_policy_documents, load_policy_documents
from policynim.runtime_paths import resolve_corpus_root, resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import create_index_store
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

    def replace(self, chunks: Sequence[EmbeddedChunk], *, complete: bool = True) -> str:
        """Replace the local index contents with embedded chunks."""
        ...

    def validate_replacement(self) -> None:
        """Check destination identity before making provider requests."""
        ...

    def complete_ingest(self, build_id: str) -> None:
        """Mark the matching database ready only after rules finalization."""
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

    def __enter__(self) -> IngestService:
        """Return this service for context-managed ingest runs."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release owned resources when leaving a context-managed ingest run."""
        self.close()

    def close(self) -> None:
        """Release owned provider resources held by this service."""
        _close_component(self._embedder)

    def run(self) -> IngestResult:
        """Load, chunk, embed, and persist the policy corpus."""
        self._index_store.validate_replacement()
        rules_path = self._runtime_rules_artifact_path
        if rules_path.resolve() == self._index_store.uri.resolve() or rules_path.is_symlink():
            raise IndexCompatibilityError(
                "Index and runtime-rules destinations must be distinct files."
            )
        if rules_path.is_dir():
            raise OSError("Runtime rules artifact path must not be a directory.")
        if not self._index_store.uri.exists() and rules_path.exists():
            raise IndexCompatibilityError(
                "Fresh index builds require a separate runtime-rules destination."
            )
        if any(
            output.resolve().is_relative_to(self._corpus_root.resolve())
            for output in (self._index_store.uri, rules_path)
        ):
            raise IndexCompatibilityError("Output destinations must be outside policy sources.")
        observed_index = _artifact_identity(self._index_store.uri)
        observed_rules = _artifact_identity(rules_path)
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
            if _artifact_identity(self._index_store.uri) != observed_index:
                raise IndexCompatibilityError("Index destination changed during ingestion.")
            build_id = self._index_store.replace(embedded_chunks, complete=False)
            _finalize_runtime_rules_artifact(
                staged_artifact_path,
                self._runtime_rules_artifact_path,
                observed_rules,
            )
            self._index_store.complete_ingest(build_id)
        finally:
            _cleanup_staged_runtime_rules_artifact(staged_artifact_path)

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
    index_store = create_index_store(active_settings)
    corpus_root = resolve_corpus_root(active_settings.corpus_dir)
    return IngestService(
        embedder=_create_default_embedder(active_settings),
        index_store=index_store,
        corpus_root=corpus_root,
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
    staged_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            staged_path = Path(handle.name)
            handle.write(f"{serialized}\n")
        return staged_path
    except BaseException:
        if staged_path is not None:
            _cleanup_staged_runtime_rules_artifact(staged_path)
        raise


def _finalize_runtime_rules_artifact(
    staged_path: Path, destination: Path, observed: tuple[int, int, int, int] | None
) -> None:
    """Publish staged rules; replacing existing files requires a single offline publisher."""
    if _artifact_identity(destination) != observed:
        raise IndexCompatibilityError("Runtime-rules destination changed during ingestion.")
    if observed is None:
        os.link(staged_path, destination)
        _cleanup_staged_runtime_rules_artifact(staged_path)
    else:
        staged_path.replace(destination)


def _cleanup_staged_runtime_rules_artifact(staged_path: Path) -> None:
    """Best-effort cleanup for staged artifact files after a failed ingest."""
    try:
        staged_path.unlink(missing_ok=True)
    except OSError:
        logging.getLogger(__name__).warning("Could not remove owned runtime-rules staging file.")


def _artifact_identity(path: Path) -> tuple[int, int, int, int] | None:
    """Identify a rules destination so finalization cannot silently replace a changed file."""
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


def _close_component(component: object | None) -> None:
    """Close an optional owned component when it exposes a close hook."""
    close = getattr(component, "close", None)
    if callable(close):
        close()
