"""Index-store factory helpers for transition-era storage wiring."""

from __future__ import annotations

from pathlib import Path

from policynim.errors import ConfigurationError
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings
from policynim.storage.lancedb import LanceDBIndexStore


def create_legacy_index_store(settings: Settings) -> LanceDBIndexStore:
    """Build the temporary LanceDB-backed index store with a controlled failure path."""
    index_uri = resolve_runtime_path(settings.lancedb_uri)
    try:
        return LanceDBIndexStore(
            uri=index_uri,
            table_name=settings.lancedb_table,
        )
    except ModuleNotFoundError as exc:
        if _is_missing_lancedb_backend(exc):
            raise ConfigurationError(
                _missing_legacy_backend_message(index_uri=index_uri),
                failure_class="missing_index_backend",
            ) from exc
        raise


def _is_missing_lancedb_backend(exc: ModuleNotFoundError) -> bool:
    """Return whether a missing-module error came from the LanceDB backend."""
    missing_name = exc.name or str(exc)
    return missing_name == "lancedb" or missing_name.startswith("lancedb.")


def _missing_legacy_backend_message(*, index_uri: Path) -> str:
    """Return install guidance for the temporary hosted legacy index backend."""
    return (
        "The current service wiring still uses the temporary LanceDB index backend at "
        f"{index_uri}. Install the `hosted-legacy-index` extra for this transition path "
        "or use a build that wires `POLICYNIM_INDEX_DB_PATH` to SQLiteVecIndexStore."
    )
