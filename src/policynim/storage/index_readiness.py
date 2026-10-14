"""Typed, provider-free inspection of the local retrieval index."""

from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import Field

from policynim.contracts import IndexStore
from policynim.errors import IndexCompatibilityError, MissingIndexError
from policynim.types import IndexIdentity, StrictModel

IndexReadinessState = Literal[
    "ready", "missing", "directory", "empty", "invalid", "unreadable", "incompatible", "incomplete"
]

_STATE_REASONS: dict[IndexReadinessState, str] = {
    "ready": "A complete local SQLite index matches the configured embedding model.",
    "missing": "No local index path exists yet.",
    "directory": "Configured local SQLite index path points to a directory, not a SQLite file.",
    "empty": "The local SQLite index contains no policy chunks.",
    "invalid": "The local index isn't a readable PolicyNIM sqlite-vec database.",
    "unreadable": "The local index couldn't be inspected. Check file and directory permissions.",
    "incompatible": "The index embedding identity is unknown or doesn't match configuration.",
    "incomplete": "The local index ingestion didn't complete.",
}


def is_sqlite_access_error(error: sqlite3.Error) -> bool:
    """Classify primary and extended SQLite access codes without raw messages."""
    code = getattr(error, "sqlite_errorcode", None)
    return isinstance(code, int) and code & 0xFF in {
        sqlite3.SQLITE_CANTOPEN,
        sqlite3.SQLITE_PERM,
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_AUTH,
        sqlite3.SQLITE_READONLY,
    }


class IndexReadinessReport(StrictModel):
    """Keep observed inventory separate from whether retrieval can use it."""

    state: IndexReadinessState
    chunk_count: int | None = Field(default=None, ge=0)
    policy_count: int | None = Field(default=None, ge=0)
    identity: IndexIdentity | None = None
    compatible: bool = False

    @property
    def reason(self) -> str:
        """Return a fixed safe summary without database or exception text."""
        return _STATE_REASONS[self.state]


def inspect_index_readiness(index_store: IndexStore) -> IndexReadinessReport:
    """Prefer the adapter's snapshot inspector, retaining the existing store protocol."""
    inspect = getattr(index_store, "inspect_readiness", None)
    if callable(inspect):
        result = inspect()
        if isinstance(result, IndexReadinessReport):
            return result
    try:
        if not index_store.exists():
            return IndexReadinessReport(state="missing")
        count = index_store.count()
        if count <= 0:
            return IndexReadinessReport(state="empty", chunk_count=0, policy_count=0)
        index_store.validate_identity()
        return IndexReadinessReport(state="ready", chunk_count=count, compatible=True)
    except IndexCompatibilityError:
        return IndexReadinessReport(state="incompatible")
    except OSError:
        return IndexReadinessReport(state="unreadable")
    except Exception:
        return IndexReadinessReport(state="invalid")


def require_index_ready(index_store: IndexStore, *, missing_message: str) -> None:
    """Refuse unusable indexes before a service starts provider work."""
    report = inspect_index_readiness(index_store)
    if report.state == "ready":
        return
    if report.state in {"incompatible", "incomplete"}:
        raise IndexCompatibilityError(report.reason)
    if report.state == "missing":
        raise MissingIndexError(missing_message)
    raise MissingIndexError(
        f"{report.reason} Preserve existing sources, index, and runtime rules. "
        "Run `policynim doctor` for recovery. Run `policynim ingest` only from verified sources "
        "into separate POLICYNIM_INDEX_DB_PATH and POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH "
        "destinations; validate before activation."
    )
