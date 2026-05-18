"""Shared SQLite helpers for PolicyNIM storage adapters."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def database_artifact_paths(path: Path) -> tuple[Path, ...]:
    """Return the primary SQLite file plus WAL sidecars for cleanup flows."""
    return (
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    )


def connect(path: Path) -> sqlite3.Connection:
    """Open one SQLite connection with the repo's standard pragmas."""
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def begin_immediate(conn: sqlite3.Connection) -> None:
    """Start one write transaction eagerly to serialize concurrent writers."""
    conn.execute("BEGIN IMMEDIATE")


def iso_utc(value: datetime) -> str:
    """Serialize one datetime as an aware UTC ISO 8601 string."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
