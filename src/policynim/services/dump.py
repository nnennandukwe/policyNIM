"""Index dump service for PolicyNIM."""

from __future__ import annotations

from pathlib import Path

from policynim.contracts import IndexStore
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import PolicyChunk


class IndexDumpService:
    """Return indexed chunks for terminal inspection."""

    def __init__(self, *, index_store: IndexStore) -> None:
        self._index_store = index_store

    def list_chunks(self) -> list[PolicyChunk]:
        """Return all stored policy chunks."""
        return self._index_store.list_chunks()


def create_index_dump_service(settings: Settings | None = None) -> IndexDumpService:
    """Build the default index dump service from application settings."""
    active_settings = settings or get_settings()
    repo_root = _repo_root()
    return IndexDumpService(
        index_store=LanceDBIndexStore(
            uri=_resolve_repo_path(active_settings.lancedb_uri, repo_root),
            table_name=active_settings.lancedb_table,
        )
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_repo_path(path: Path, repo_root: Path) -> Path:
    return path if path.is_absolute() else repo_root / path
