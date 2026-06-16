"""Query logging for PolicyNIM search analytics."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from policynim.types import SearchRequest

_LOG_DB_NAME = "query_log.db"


class QueryLog:
    """Persist search queries to a local SQLite database for analytics."""

    def __init__(self, base_dir: Path) -> None:
        """Initialize the query log SQLite database connection.

        Args:
            base_dir: Directory where the local analytics database file is stored.
        """
        self._db_path = base_dir / _LOG_DB_NAME
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS queries ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "query TEXT NOT NULL, "
            "domain TEXT, "
            "top_k INTEGER NOT NULL)"
        )

    def record(self, request: SearchRequest) -> None:
        """Record a search request in the query log."""
        try:
            self._conn.execute(
                "INSERT INTO queries (query, domain, top_k) VALUES (?, ?, ?)",
                (request.query, request.domain, request.top_k),
            )
            self._conn.commit()
        except Exception:
            pass

    def recent(
        self, limit: int = 10, domains: Sequence[str] | None = None
    ) -> list[tuple]:
        """Return the most recent logged queries, optionally filtered by domain."""
        sql = "SELECT query, domain, top_k FROM queries"
        params: list[object] = []
        if domains:
            placeholders = ", ".join("?" for _ in domains)
            sql += f" WHERE domain IN ({placeholders})"
            params.extend(domains)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        cursor = self._conn.execute(sql, params)
        return cursor.fetchall()

    def clear(self) -> None:
        """Delete all logged queries."""
        self._conn.execute("DELETE FROM queries")
        self._conn.commit()
