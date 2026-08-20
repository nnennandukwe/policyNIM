"""sqlite-vec local vector storage for PolicyNIM."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile

import sqlite_vec

from policynim.contracts import IndexStore
from policynim.errors import MissingIndexError
from policynim.storage.index_readiness import IndexReadinessReport
from policynim.types import EmbeddedChunk, PolicyChunk, PolicyMetadata, ScoredChunk

_SCHEMA_VERSION = "1"
_METADATA_TABLE = "index_metadata"
_CHUNKS_TABLE = "policy_chunks"
_VECTORS_TABLE = "policy_vectors"
_DOMAIN_CANDIDATE_MULTIPLIER = 5
_MIN_DOMAIN_CANDIDATES = 20


class SQLiteVecIndexStore(IndexStore):
    """Stores embedded policy chunks in a local sqlite-vec database."""

    def __init__(self, *, path: Path) -> None:
        """Configure the SQLite index database path."""
        self._path = path

    @property
    def path(self) -> Path:
        """Return the configured SQLite index file path."""
        return self._path

    @property
    def uri(self) -> Path:
        """Return the underlying index URI for ingest result compatibility."""
        return self._path

    @property
    def table_name(self) -> str:
        """Return the fixed logical table name for ingest result compatibility."""
        return _CHUNKS_TABLE

    def replace(self, chunks: Sequence[EmbeddedChunk]) -> None:
        """Replace the local index contents with embedded chunks."""
        indexed_chunks, dimension = _validate_replacement(self._path, chunks)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _new_temp_database_path(self._path)

        try:
            with closing(_connect(tmp_path)) as conn:
                _begin_immediate(conn)
                try:
                    _initialize_schema(conn, dimension=dimension)
                    _insert_chunks(conn, indexed_chunks)
                    conn.execute("COMMIT")
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            _cleanup_sidecars(self._path)
            tmp_path.replace(self._path)
            _cleanup_sidecars(self._path)
        finally:
            _cleanup_database_files(tmp_path)

    def exists(self) -> bool:
        """Return whether the local index exists."""
        return self.inspect_readiness().state == "ready"

    def count(self) -> int:
        """Return the number of rows in the local index."""
        readiness = self.inspect_readiness()
        if readiness.state != "ready":
            return 0
        return readiness.row_count

    def list_chunks(self) -> list[PolicyChunk]:
        """Return all indexed chunks without embeddings."""
        with closing(self._require_connection()) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    chunk_id,
                    path,
                    section,
                    lines,
                    text,
                    policy_id,
                    title,
                    doc_type,
                    domain,
                    tags_json,
                    grounded_in_json
                FROM {_CHUNKS_TABLE}
                ORDER BY rowid ASC
                """
            ).fetchall()
        return [_policy_chunk_from_row(row) for row in rows]

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        domain: str | None = None,
    ) -> list[ScoredChunk]:
        """Search the local index and return scored chunks."""
        with closing(self._require_connection()) as conn:
            if top_k <= 0:
                return []
            query_vector = _validated_query_vector(conn, query_embedding)
            row_count = _count_chunks(conn)
            candidate_limit = min(
                _candidate_limit(top_k=top_k, domain=domain),
                row_count,
            )

            while True:
                rows = conn.execute(
                    f"""
                    SELECT
                        c.chunk_id,
                        c.path,
                        c.section,
                        c.lines,
                        c.text,
                        c.policy_id,
                        c.title,
                        c.doc_type,
                        c.domain,
                        c.tags_json,
                        c.grounded_in_json,
                        v.distance
                    FROM {_VECTORS_TABLE} AS v
                    JOIN {_CHUNKS_TABLE} AS c ON c.rowid = v.rowid
                    WHERE v.embedding MATCH ? AND k = ?
                    ORDER BY v.distance ASC
                    """,
                    (sqlite_vec.serialize_float32(query_vector), candidate_limit),
                ).fetchall()
                results = [_scored_chunk_from_row(row) for row in rows]

                if domain is None:
                    return results[:top_k]

                domain_results = [chunk for chunk in results if chunk.policy.domain == domain]
                if len(domain_results) >= top_k or candidate_limit >= row_count:
                    return domain_results[:top_k]
                candidate_limit = min(row_count, max(candidate_limit * 2, candidate_limit + top_k))

    def close(self) -> None:
        """Release owned resources.

        The store opens one SQLite connection per operation, so there is no
        shared handle to close. The hook keeps service lifecycle behavior
        symmetrical with other stores.
        """

    def reset_for_tests(self) -> None:
        """Reset the backing SQLite file and WAL sidecars for deterministic tests."""
        _cleanup_database_files(self._path)

    def inspect_readiness(self) -> IndexReadinessReport:
        """Describe whether the local SQLite index is ready for retrieval use."""
        if not self._path.exists():
            return IndexReadinessReport(state="missing")
        if self._path.is_dir():
            return IndexReadinessReport(state="directory")

        try:
            with closing(_connect(self._path)) as conn:
                if not _has_required_schema(conn):
                    return IndexReadinessReport(state="invalid")
                row_count = _count_chunks(conn)
        except OSError as exc:
            return IndexReadinessReport(state="unreadable", error=exc)
        except sqlite3.DatabaseError as exc:
            return IndexReadinessReport(state="invalid", error=exc)

        if row_count <= 0:
            return IndexReadinessReport(state="empty")
        return IndexReadinessReport(state="ready", row_count=row_count)

    def _require_connection(self) -> sqlite3.Connection:
        """Open a validated SQLite connection or fail with missing-index guidance."""
        if not self._path.exists():
            raise MissingIndexError(f"Local SQLite index does not exist at {self._path}.")
        if self._path.is_dir():
            raise MissingIndexError(
                f"Local SQLite index path {self._path} must not be a directory."
            )

        conn = _connect(self._path)
        try:
            if not _has_required_schema(conn):
                raise MissingIndexError(f"Local SQLite index at {self._path} is not initialized.")
            if _count_chunks(conn) == 0:
                raise MissingIndexError(f"Local SQLite index at {self._path} contains no rows.")
        except Exception:
            conn.close()
            raise
        return conn


def _validate_replacement(
    path: Path,
    chunks: Sequence[EmbeddedChunk],
) -> tuple[list[EmbeddedChunk], int]:
    """Validate replacement chunks and return a concrete list plus vector dimension."""
    if path.exists() and path.is_dir():
        raise MissingIndexError(f"Local SQLite index path {path} must not be a directory.")

    indexed_chunks = list(chunks)
    if not indexed_chunks:
        raise MissingIndexError("Cannot build an index without any embedded policy chunks.")

    dimension: int | None = None
    for chunk in indexed_chunks:
        if not chunk.vector:
            raise MissingIndexError(f"Chunk {chunk.chunk_id!r} does not have an embedding vector.")
        if dimension is None:
            dimension = len(chunk.vector)
        elif len(chunk.vector) != dimension:
            raise MissingIndexError("All embedded chunks must share the same vector dimension.")

    if dimension is None:
        raise MissingIndexError("Cannot build an index without any embedded policy chunks.")
    return indexed_chunks, dimension


def _new_temp_database_path(target_path: Path) -> Path:
    """Create an empty temporary SQLite path next to the target database."""
    with NamedTemporaryFile(
        dir=target_path.parent,
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        return Path(handle.name)


def _connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection configured for sqlite-vec operations."""
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        connection.enable_load_extension(True)
        try:
            sqlite_vec.load(connection)
        finally:
            connection.enable_load_extension(False)
    except Exception:
        connection.close()
        raise
    return connection


def _begin_immediate(conn: sqlite3.Connection) -> None:
    """Acquire the write lock before mutating index contents."""
    conn.execute("BEGIN IMMEDIATE")


def _initialize_schema(conn: sqlite3.Connection, *, dimension: int) -> None:
    """Create the metadata, chunk, and vector tables for one index database."""
    conn.execute(
        f"""
        CREATE TABLE {_METADATA_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {_CHUNKS_TABLE} (
            rowid INTEGER PRIMARY KEY,
            chunk_id TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL,
            section TEXT NOT NULL,
            lines TEXT NOT NULL,
            text TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            title TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            domain TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            grounded_in_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE INDEX idx_policy_chunks_domain
        ON {_CHUNKS_TABLE}(domain)
        """
    )
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE {_VECTORS_TABLE}
        USING vec0(embedding float[{dimension}])
        """
    )
    conn.executemany(
        f"INSERT INTO {_METADATA_TABLE} (key, value) VALUES (?, ?)",
        (
            ("schema_version", _SCHEMA_VERSION),
            ("embedding_dimension", str(dimension)),
        ),
    )


def _insert_chunks(conn: sqlite3.Connection, chunks: Sequence[EmbeddedChunk]) -> None:
    """Insert validated embedded chunks into the chunk and vector tables."""
    for rowid, chunk in enumerate(chunks, start=1):
        conn.execute(
            f"""
            INSERT INTO {_CHUNKS_TABLE} (
                rowid,
                chunk_id,
                path,
                section,
                lines,
                text,
                policy_id,
                title,
                doc_type,
                domain,
                tags_json,
                grounded_in_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rowid,
                chunk.chunk_id,
                chunk.path,
                chunk.section,
                chunk.lines,
                chunk.text,
                chunk.policy.policy_id,
                chunk.policy.title,
                chunk.policy.doc_type,
                chunk.policy.domain,
                json.dumps(chunk.policy.tags),
                json.dumps(chunk.policy.grounded_in),
            ),
        )
        conn.execute(
            f"INSERT INTO {_VECTORS_TABLE} (rowid, embedding) VALUES (?, ?)",
            (rowid, sqlite_vec.serialize_float32([float(value) for value in chunk.vector])),
        )


def _has_required_schema(conn: sqlite3.Connection) -> bool:
    """Return whether the database has the current PolicyNIM index schema."""
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE name IN (?, ?, ?)
        """,
        (_METADATA_TABLE, _CHUNKS_TABLE, _VECTORS_TABLE),
    ).fetchall()
    table_names = {str(row["name"]) for row in rows}
    if not {_METADATA_TABLE, _CHUNKS_TABLE, _VECTORS_TABLE}.issubset(table_names):
        return False
    return _metadata_value(conn, "schema_version") == _SCHEMA_VERSION


def _metadata_value(conn: sqlite3.Connection, key: str) -> str | None:
    """Return a stored metadata value from the local index."""
    row = conn.execute(
        f"SELECT value FROM {_METADATA_TABLE} WHERE key = ?",
        (key,),
    ).fetchone()
    return str(row["value"]) if row is not None else None


def _embedding_dimension(conn: sqlite3.Connection) -> int:
    """Return the indexed embedding dimension or raise a controlled error."""
    value = _metadata_value(conn, "embedding_dimension")
    if value is None:
        raise MissingIndexError("Local SQLite index is missing embedding dimension metadata.")
    try:
        return int(value)
    except ValueError as exc:
        raise MissingIndexError(
            "Local SQLite index has invalid embedding dimension metadata."
        ) from exc


def _count_chunks(conn: sqlite3.Connection) -> int:
    """Return the number of stored policy chunks."""
    row = conn.execute(f"SELECT COUNT(*) AS row_count FROM {_CHUNKS_TABLE}").fetchone()
    return int(row["row_count"])


def _validated_query_vector(
    conn: sqlite3.Connection,
    query_embedding: Sequence[float],
) -> list[float]:
    """Validate and normalize a query embedding for sqlite-vec search."""
    query_vector = [float(value) for value in query_embedding]
    if not query_vector:
        raise MissingIndexError("Search query embedding is empty.")

    expected_dimension = _embedding_dimension(conn)
    if len(query_vector) != expected_dimension:
        raise MissingIndexError(
            "Search query embedding dimension does not match the local SQLite index."
        )
    return query_vector


def _candidate_limit(*, top_k: int, domain: str | None) -> int:
    """Return the initial vector candidate pool size for retrieval."""
    if domain is None:
        return top_k
    return max(top_k, top_k * _DOMAIN_CANDIDATE_MULTIPLIER, _MIN_DOMAIN_CANDIDATES)


def _policy_chunk_from_row(row: sqlite3.Row) -> PolicyChunk:
    """Deserialize one SQLite row into a policy chunk."""
    return PolicyChunk(
        chunk_id=str(row["chunk_id"]),
        path=str(row["path"]),
        section=str(row["section"]),
        lines=str(row["lines"]),
        text=str(row["text"]),
        policy=_policy_metadata_from_row(row),
    )


def _scored_chunk_from_row(row: sqlite3.Row) -> ScoredChunk:
    """Deserialize one SQLite vector-search row into a scored chunk."""
    distance = float(row["distance"])
    return ScoredChunk(
        **_policy_chunk_from_row(row).model_dump(),
        score=max(0.0, 1.0 - distance),
    )


def _policy_metadata_from_row(row: sqlite3.Row) -> PolicyMetadata:
    """Deserialize policy metadata fields from a SQLite chunk row."""
    return PolicyMetadata(
        policy_id=str(row["policy_id"]),
        title=str(row["title"]),
        doc_type=str(row["doc_type"]),
        domain=str(row["domain"]),
        tags=_json_string_list(str(row["tags_json"])),
        grounded_in=_json_string_list(str(row["grounded_in_json"])),
    )


def _json_string_list(value: str) -> list[str]:
    """Decode a JSON list field as strings, falling back to an empty list."""
    loaded = json.loads(value)
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]


def _cleanup_database_files(path: Path) -> None:
    """Remove a SQLite database and its WAL sidecar files."""
    for candidate in _database_files(path):
        candidate.unlink(missing_ok=True)


def _cleanup_sidecars(path: Path) -> None:
    """Remove only WAL sidecar files for a SQLite database path."""
    for candidate in _database_files(path)[1:]:
        candidate.unlink(missing_ok=True)


def _database_files(path: Path) -> tuple[Path, Path, Path]:
    """Return the main SQLite database path plus WAL sidecar paths."""
    return (
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    )
