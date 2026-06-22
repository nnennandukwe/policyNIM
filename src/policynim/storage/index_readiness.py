"""Shared local-index readiness inspection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from policynim.contracts import IndexStore

IndexReadinessState = Literal["ready", "missing", "directory", "empty", "invalid", "unreadable"]


@dataclass(frozen=True, slots=True)
class IndexReadinessReport:
    """Describe whether a local index can safely serve retrieval operations."""

    state: IndexReadinessState
    row_count: int = 0
    error: Exception | None = None


def inspect_index_readiness(index_store: IndexStore) -> IndexReadinessReport:
    """Return the readiness state for one index store."""
    inspect = getattr(index_store, "inspect_readiness", None)
    if callable(inspect):
        report = inspect()
        if isinstance(report, IndexReadinessReport):
            return report

    try:
        if not index_store.exists():
            return IndexReadinessReport(state="missing")
        row_count = index_store.count()
    except OSError as exc:
        return IndexReadinessReport(state="unreadable", error=exc)
    except Exception as exc:
        return IndexReadinessReport(state="invalid", error=exc)

    if row_count <= 0:
        return IndexReadinessReport(state="empty")
    return IndexReadinessReport(state="ready", row_count=row_count)


def format_index_readiness_detail(error: Exception | None) -> str | None:
    """Return a compact one-line error detail for operator-facing output."""
    if error is None:
        return None

    if isinstance(error, OSError):
        raw_message = error.strerror or str(error)
    else:
        raw_message = str(error)
    message = " ".join(raw_message.split()).strip().rstrip(".")
    if message:
        return f"{type(error).__name__}: {message}"
    return type(error).__name__
