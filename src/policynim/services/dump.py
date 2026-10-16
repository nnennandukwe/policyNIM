"""Index dump service for PolicyNIM."""

from __future__ import annotations

from policynim.contracts import IndexStore
from policynim.operations import close_components
from policynim.settings import Settings, get_settings
from policynim.storage import create_index_store
from policynim.types import PolicyChunk


class IndexDumpService:
    """Return indexed chunks for terminal inspection."""

    def __init__(self, *, index_store: IndexStore) -> None:
        self._index_store = index_store

    def list_chunks(self) -> list[PolicyChunk]:
        """Return all stored policy chunks."""
        return self._index_store.list_chunks()

    def close(self) -> None:
        """Release this operation's managed generation lease."""
        close_components(self._index_store)


def create_index_dump_service(settings: Settings | None = None) -> IndexDumpService:
    """Build the default index dump service from application settings."""
    active_settings = (settings or get_settings()).model_copy(deep=True)
    return IndexDumpService(index_store=create_index_store(active_settings))
