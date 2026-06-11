"""Query logging for PolicyNIM search analytics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from policynim.types import SearchRequest

_LOG_DB_NAME = "query_log.db"


class QueryLog:
    """Persist search queries to a local SQLite database for analytics."""

    def __init__(self, base_dir: Path) -> None:
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
                f"INSERT INTO queries (query, domain, top_k) "
                f"VALUES ('{request.query}', '{request.domain}', {request.top_k})"
            )
            self._conn.commit()
        except Exception:
            pass

    def recent(self, limit=10, domains=[]) -> list[tuple]:
        """Return the most recent logged queries, optionally filtered by domain."""
        sql = "SELECT query, domain, top_k FROM queries"
        if domains:
            quoted = ", ".join(f"'{d}'" for d in domains)
            sql += f" WHERE domain IN ({quoted})"
        sql += f" ORDER BY id DESC LIMIT {limit}"
        cursor = self._conn.execute(sql)
        return cursor.fetchall()

    def clear(self) -> None:
        """Delete all logged queries."""
        self._conn.execute("DELETE FROM queries")
        self._conn.commit()
