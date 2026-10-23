"""Transactional pgvector storage using exact L2 search and fresh generation tables."""

from __future__ import annotations

import importlib
import json
import math
from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from policynim.operations import check_budget
from policynim.storage.vectors import VectorRecord, VectorStorageError, validate_records


class PGVectorVectors:
    """Use optional Psycopg without adding a database dependency to the SQLite runtime."""

    def __init__(self, *, url: str, password: str | None, timeout: float) -> None:
        """Keep connection inputs private until an explicit probe, publish, or query."""
        parts = urlsplit(url)
        self._options = {
            "host": parts.hostname,
            "port": parts.port or 5432,
            "dbname": unquote(parts.path.lstrip("/")),
            "user": unquote(parts.username or "postgres"),
            "password": password or "",
            "sslmode": parse_qs(parts.query).get("sslmode", ["verify-full"])[0],
            "connect_timeout": max(1, math.ceil(timeout)),
            "options": f"-c statement_timeout={max(1, int(timeout * 1000))}",
        }

    def _driver(self) -> Any:
        """Load the pinned optional driver only for PostgreSQL workflows."""
        check_budget()
        try:
            return importlib.import_module("psycopg")
        except ImportError:
            raise VectorStorageError("Install the vector-postgres extra to use pgvector.") from None

    def probe(self) -> None:
        """Verify authentication and extension availability without installing anything."""
        driver = self._driver()
        try:
            with driver.connect(**self._options) as connection:
                row = connection.execute(
                    "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                ).fetchone()
                if row is None:
                    raise VectorStorageError("Ask the database owner to install pgvector.")
        except driver.Error:
            raise VectorStorageError(
                "Postgres authentication or extension inspection failed."
            ) from None

    def prepare(self, name: str, generation: str, records: Sequence[VectorRecord]) -> None:
        """Create and populate a table in one transaction; existing tables stay untouched."""
        driver = self._driver()
        sql = driver.sql
        try:
            with driver.connect(**self._options) as connection:
                exists = connection.execute(
                    "SELECT to_regclass(%s)", ("public." + name,)
                ).fetchone()[0]
                if exists is not None:
                    self._validate(connection, name, generation, records)
                    return
                if not records:
                    raise VectorStorageError("Can't publish an empty vector index.")
                connection.execute(
                    sql.SQL(
                        "CREATE TABLE {} (key text PRIMARY KEY, domain text NOT NULL, "
                        "generation text NOT NULL, embedding vector({}) NOT NULL)"
                    ).format(sql.Identifier("public", name), sql.Literal(len(records[0].vector)))
                )
                with connection.cursor() as cursor:
                    cursor.executemany(
                        sql.SQL("INSERT INTO {} VALUES (%s, %s, %s, %s::vector)").format(
                            sql.Identifier("public", name)
                        ),
                        [
                            (record.key, record.domain, generation, json.dumps(record.vector))
                            for record in records
                        ],
                    )
                self._validate(connection, name, generation, records)
        except driver.Error:
            raise VectorStorageError(
                "Postgres vector publication failed before activation."
            ) from None

    def _validate(
        self, connection: Any, name: str, generation: str, records: Sequence[VectorRecord]
    ) -> None:
        """Bound row materialization and verify every canonical vector and owner."""
        sql = self._driver().sql
        rows = connection.execute(
            sql.SQL("SELECT key, domain, generation, embedding::text FROM {} LIMIT %s").format(
                sql.Identifier("public", name)
            ),
            (len(records) + 1,),
        ).fetchall()
        actual = []
        try:
            for key, domain, owner, vector in rows:
                if owner != generation:
                    raise VectorStorageError("Postgres generation binding doesn't match.")
                actual.append(VectorRecord(key=key, domain=domain, vector=json.loads(vector)))
            validate_records(actual, records)
        except (TypeError, ValueError):
            raise VectorStorageError("Postgres vector records couldn't be validated.") from None

    def validate(self, name: str, generation: str, records: Sequence[VectorRecord]) -> None:
        """Validate under a repeatable-read snapshot without mutating remote state."""
        driver = self._driver()
        try:
            with driver.connect(**self._options) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                self._validate(connection, name, generation, records)
        except driver.Error:
            raise VectorStorageError(
                "Postgres vector index isn't readable or compatible."
            ) from None

    def search(
        self, name: str, generation: str, vector: Sequence[float], top_k: int, domain: str | None
    ) -> list[str]:
        """Use parameterized exact L2 search, with stable ties and database-side filters."""
        driver = self._driver()
        sql = driver.sql
        try:
            with driver.connect(**self._options) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                rows = connection.execute(
                    sql.SQL(
                        "SELECT key FROM {} WHERE generation = %s "
                        "AND (%s::text IS NULL OR domain = %s) "
                        "ORDER BY embedding <-> %s::vector, key LIMIT %s"
                    ).format(sql.Identifier("public", name)),
                    (generation, domain, domain, json.dumps(list(vector)), top_k),
                ).fetchall()
                return [row[0] for row in rows]
        except driver.Error:
            raise VectorStorageError("Postgres vector retrieval failed.") from None

    def close(self) -> None:
        """Connections are scoped to each operation and already closed by their contexts."""
