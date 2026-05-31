"""Index-store factory helpers for storage wiring."""

from __future__ import annotations

from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings
from policynim.storage.sqlite_vec import SQLiteVecIndexStore


def create_index_store(settings: Settings) -> SQLiteVecIndexStore:
    """Build the canonical SQLite-backed index store from application settings."""
    return SQLiteVecIndexStore(path=resolve_runtime_path(settings.index_db_path))
