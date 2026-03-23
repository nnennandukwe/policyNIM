"""Index dump service for PolicyNIM."""

from __future__ import annotations

from policynim.contracts import IndexStore
from policynim.runtime_paths import resolve_runtime_path
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
    return IndexDumpService(
        index_store=LanceDBIndexStore(
            uri=resolve_runtime_path(active_settings.lancedb_uri),
            table_name=active_settings.lancedb_table,
        )
    )
