"""Read-only source and indexed policy inventory without model calls."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from policynim.errors import InvalidPolicyDocumentError
from policynim.ingest.chunking import chunk_policy_documents
from policynim.ingest.loader import load_policy_documents
from policynim.runtime_paths import resolve_corpus_root
from policynim.settings import Settings, get_settings
from policynim.storage import create_index_store
from policynim.storage.index_readiness import IndexReadinessReport, inspect_index_readiness
from policynim.types import StrictModel


class SourceInventory(StrictModel):
    """Describe readable source documents independently of any built index."""

    status: Literal["ok", "unavailable"]
    root: Path | None = None
    document_count: int | None = Field(default=None, ge=0)
    policy_count: int | None = Field(default=None, ge=0)
    chunk_count: int | None = Field(default=None, ge=0)
    policy_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class PolicyInventory(StrictModel):
    """Report this process's sources and local database, never an inferred remote inventory."""

    scope: Literal["local"] = "local"
    source: SourceInventory
    index: IndexReadinessReport
    index_path: Path


def inspect_source_inventory(settings: Settings) -> SourceInventory:
    """Load and validate the selected Markdown corpus without providers or writes."""
    root: Path | None = None
    try:
        root = resolve_corpus_root(settings.corpus_dir)
        documents = load_policy_documents(root)
        chunks = chunk_policy_documents(documents)
        return SourceInventory(
            status="ok",
            root=root,
            document_count=len(documents),
            policy_count=len({document.metadata.policy_id for document in documents}),
            chunk_count=len(chunks),
            policy_ids=sorted(document.metadata.policy_id for document in documents),
        )
    except (InvalidPolicyDocumentError, OSError, UnicodeError):
        return SourceInventory(
            status="unavailable",
            root=root,
            reason=(
                "Policy sources couldn't be loaded or validated. Check POLICYNIM_CORPUS_DIR, "
                "file permissions, UTF-8 Markdown, and unique policy IDs before rebuilding. "
                "Run `policynim policies validate --format json` for exact source locations."
            ),
        )


def inspect_policy_inventory(settings: Settings | None = None) -> PolicyInventory:
    """Inspect selected source files and one local index snapshot without requiring setup."""
    active_settings = settings or get_settings()
    store = create_index_store(active_settings)
    return PolicyInventory(
        source=inspect_source_inventory(active_settings),
        index=inspect_index_readiness(store),
        index_path=store.path,
    )
