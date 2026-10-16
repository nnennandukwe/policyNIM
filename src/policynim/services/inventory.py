"""Read-only source and indexed policy inventory without model calls."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from policynim.errors import GenerationError, InvalidPolicyDocumentError
from policynim.ingest.chunking import chunk_policy_documents
from policynim.ingest.loader import load_policy_documents
from policynim.runtime_paths import resolve_corpus_root, resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import create_index_store
from policynim.storage.index_readiness import IndexReadinessReport, inspect_index_readiness
from policynim.storage.index_store import pinned_generation
from policynim.types import GenerationID, StrictModel


class SourceInventory(StrictModel):
    """Describe readable source documents independently of any built index."""

    status: Literal["ok", "unavailable"]
    root: Path | None = None
    roots: list[Path] = Field(default_factory=list)
    profile_path: Path | None = None
    document_count: int | None = Field(default=None, ge=0)
    policy_count: int | None = Field(default=None, ge=0)
    chunk_count: int | None = Field(default=None, ge=0)
    policy_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    catalog_digest: str | None = None
    profile_id: str | None = None
    decisions: dict[str, str] = Field(default_factory=dict)


class PolicyInventory(StrictModel):
    """Report this process's sources and local database, never an inferred remote inventory."""

    scope: Literal["local"] = "local"
    generation_state: Literal["ready", "missing", "migration_required", "invalid"] = "missing"
    generation_id: GenerationID | None = None
    generation_reason: str | None = None
    index_scope: Literal["generation", "legacy"] = "legacy"
    source: SourceInventory
    index: IndexReadinessReport
    index_path: Path


def inspect_source_inventory(settings: Settings) -> SourceInventory:
    """Load and validate the selected Markdown corpus without providers or writes."""
    root: Path | None = None
    try:
        from policynim.services.catalog import compile_policy_catalog
        from policynim.services.profiles import resolve_profile

        roots: list[Path]
        if settings.profile is not None:
            resolved = resolve_profile(settings.profile)
            documents, catalog, roots = resolved.documents, resolved.catalog, resolved.roots
        else:
            root = resolve_corpus_root(settings.corpus_dir)
            roots = [root]
            documents = load_policy_documents(root)
            catalog = compile_policy_catalog(documents, root=root)
        chunks = chunk_policy_documents(documents)
        return SourceInventory(
            status="ok",
            catalog_digest=catalog.digest,
            profile_id=catalog.profile_id,
            decisions={
                entry.policy.policy_id: entry.state
                for entry in catalog.policies
                if entry.state != "active"
            },
            root=root,
            roots=roots,
            profile_path=settings.profile,
            document_count=len(documents),
            policy_count=len({document.metadata.policy_id for document in documents}),
            chunk_count=len(chunks),
            policy_ids=sorted(document.metadata.policy_id for document in documents),
        )
    except (InvalidPolicyDocumentError, OSError, UnicodeError):
        return SourceInventory(
            status="unavailable",
            root=root,
            profile_path=settings.profile,
            reason=(
                "Policy sources couldn't be loaded or validated. "
                "Check POLICYNIM_PROFILE or POLICYNIM_CORPUS_DIR, "
                "file permissions, UTF-8 Markdown, and unique policy IDs before rebuilding. "
                "Run `policynim policies validate --format json` for exact source locations."
            ),
        )


def inspect_policy_inventory(settings: Settings | None = None) -> PolicyInventory:
    """Inspect selected source files and one local index snapshot without requiring setup."""
    active_settings = (settings or get_settings()).model_copy(deep=True)
    source = inspect_source_inventory(active_settings)
    try:
        store = create_index_store(active_settings)
    except GenerationError as exc:
        root = resolve_runtime_path(active_settings.generations_dir)
        invalid = (
            active_settings.generation_id is not None
            or (root / "active.json").exists()
            or (root / "active.json").is_symlink()
        )
        legacy = create_index_store(active_settings, legacy=True)
        try:
            readiness = inspect_index_readiness(legacy)
            return PolicyInventory(
                source=source,
                index=readiness,
                index_path=legacy.path,
                generation_state=(
                    "invalid"
                    if invalid
                    else ("migration_required" if readiness.state == "ready" else "missing")
                ),
                generation_reason=str(exc),
            )
        finally:
            legacy.close()
    try:
        snapshot = pinned_generation(store)
        assert snapshot is not None
        return PolicyInventory(
            source=source,
            index=inspect_index_readiness(store),
            index_path=store.path,
            generation_state="ready",
            generation_id=snapshot.generation_id,
            index_scope="generation",
        )
    finally:
        store.close()
