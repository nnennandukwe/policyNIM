"""sqlite-vec local vector storage for PolicyNIM."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import sqlite_vec
from pydantic import ValidationError

from policynim.contracts import IndexStore
from policynim.errors import IndexCompatibilityError, MissingIndexError
from policynim.types import (
    EmbeddedChunk,
    EmbeddingIdentity,
    IndexIdentity,
    PolicyChunk,
    PolicyMetadata,
    ScoredChunk,
)

_SCHEMA_VERSION = "2"
LOGGER = logging.getLogger(__name__)
_METADATA_TABLE = "index_metadata"
_CHUNKS_TABLE = "policy_chunks"
_VECTORS_TABLE = "policy_vectors"
_DOMAIN_CANDIDATE_MULTIPLIER = 5
_MIN_DOMAIN_CANDIDATES = 20


class SQLiteVecIndexStore(IndexStore):
    """Stores embedded policy chunks in a local sqlite-vec database."""

    def __init__(self, *, path: Path, embedding_identity: EmbeddingIdentity | None = None) -> None:
        """Configure the SQLite index database path."""
        self._path = path
        self._embedding_identity = embedding_identity
        self._pending_ingest: tuple[str, tuple[int, int, int, int]] | None = None

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

    def validate_replacement(self) -> None:
        """Reject unknown or incompatible existing data before incurring embedding usage."""
        if self._embedding_identity is None or self._path.is_symlink():
            raise IndexCompatibilityError()
        if self._path.exists():
            try:
                self.validate_identity()
            except IndexCompatibilityError:
                raise
            except (MissingIndexError, sqlite3.DatabaseError, OSError) as exc:
                raise IndexCompatibilityError("Existing index cannot be validated.") from exc

    def inspect_identity(self) -> IndexIdentity:
        """Inspect persisted identity without altering the database or calling a provider."""
        with closing(self._require_connection()) as conn:
            return _read_identity(conn)

    def validate_identity(self) -> None:
        """Require a complete index in the embedding space configured for this store."""
        with closing(self._require_connection()) as conn:
            self._validate_identity(conn)

    def _validate_identity(
        self, conn: sqlite3.Connection, *, require_complete: bool = True
    ) -> IndexIdentity:
        """Require stored identity to match configuration and, normally, completed ingestion."""
        identity = _read_identity(conn)
        if self._embedding_identity is None or identity.embedding != self._embedding_identity:
            raise IndexCompatibilityError()
        if require_complete and not identity.complete:
            raise IndexCompatibilityError("Index ingestion did not complete.")
        return identity

    def replace(self, chunks: Sequence[EmbeddedChunk], *, complete: bool = True) -> str:
        """Publish one validated database; migrations must use a fresh destination."""
        indexed_chunks, dimension = _validate_replacement(self._path, chunks)
        self.validate_replacement()
        if self._path.exists() and self.inspect_identity().dimension != dimension:
            raise IndexCompatibilityError("Embedding dimensions changed.")
        observed = _path_identity(self._path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _new_temp_database_path(self._path)
        build_id = uuid4().hex
        published = False
        try:
            with closing(_connect(tmp_path)) as conn:
                _begin_immediate(conn)
                try:
                    _initialize_schema(conn, dimension=dimension)
                    assert self._embedding_identity is not None
                    conn.executemany(
                        f"INSERT INTO {_METADATA_TABLE} (key, value) VALUES (?, ?)",
                        (
                            ("embedding_provider", self._embedding_identity.provider),
                            ("embedding_model", self._embedding_identity.model),
                            ("embedding_endpoint", self._embedding_identity.endpoint),
                            ("build_id", build_id),
                            ("ingest_complete", "true" if complete else "false"),
                        ),
                    )
                    _insert_chunks(conn, indexed_chunks)
                    self._validate_identity(conn, require_complete=False)
                    conn.execute("COMMIT")
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("PRAGMA journal_mode=DELETE")

            prepared_identity = _path_identity(tmp_path)
            if prepared_identity is None:
                raise IndexCompatibilityError("Prepared index disappeared before publication.")
            if _path_identity(self._path) != observed:
                raise IndexCompatibilityError("Index destination changed during preparation.")
            if observed is None:
                # Unlike replace(), link() cannot overwrite a concurrent creator.
                os.link(tmp_path, self._path)
            else:
                self.validate_replacement()
                if any(p.exists() for p in _database_files(self._path)[1:]):
                    raise IndexCompatibilityError(
                        "Index is in use; rebuild offline into separate paths."
                    )
                if _path_identity(self._path) != observed:
                    raise IndexCompatibilityError("Index destination changed before publication.")
                tmp_path.replace(self._path)
            published = True
            self._pending_ingest = (build_id, prepared_identity) if not complete else None
            return build_id
        finally:
            try:
                _cleanup_database_files(tmp_path)
            except OSError:
                # Cleanup must not mask failures or report a published build as aborted.
                LOGGER.warning(
                    "Could not remove owned index staging files (%s).",
                    "published" if published else "not published",
                )

    def complete_ingest(self, build_id: str) -> None:
        """Mark only this ingestion's database complete after runtime rules are finalized."""
        self._validate_pending_ingest(build_id)
        with closing(_connect(self._path, must_exist=True, configure_wal=False)) as conn:
            self._validate_pending_ingest(build_id)
            _begin_immediate(conn)
            try:
                identity = self._validate_identity(conn, require_complete=False)
                if identity.build_id != build_id:
                    raise IndexCompatibilityError("Index changed before ingestion completion.")
                conn.execute(
                    f"UPDATE {_METADATA_TABLE} SET value='true' WHERE key='ingest_complete'"
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        self._pending_ingest = None

    def _validate_pending_ingest(self, build_id: str) -> None:
        """Require the producer's build receipt and the physical file it staged."""
        if (
            self._pending_ingest is None
            or self._pending_ingest[0] != build_id
            or self._path.is_symlink()
            or _path_identity(self._path) != self._pending_ingest[1]
        ):
            raise IndexCompatibilityError("Index changed before ingestion completion.")

    def exists(self) -> bool:
        """Return whether the local index exists."""
        if not self._path.exists() or self._path.is_dir():
            return False
        try:
            with closing(_connect(self._path, read_only=True)) as conn:
                return _has_required_schema(conn) and _count_chunks(conn) > 0
        except (OSError, sqlite3.DatabaseError):
            return False

    def count(self) -> int:
        """Return the number of rows in the local index."""
        if not self._path.exists() or self._path.is_dir():
            return 0
        try:
            with closing(_connect(self._path, read_only=True)) as conn:
                if not _has_required_schema(conn):
                    return 0
                return _count_chunks(conn)
        except (OSError, sqlite3.DatabaseError):
            return 0

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
            conn.execute("BEGIN")
            self._validate_identity(conn)
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

    def _require_connection(self) -> sqlite3.Connection:
        """Open a validated SQLite connection or fail with missing-index guidance."""
        if not self._path.exists():
            raise MissingIndexError(f"Local SQLite index does not exist at {self._path}.")
        if self._path.is_dir():
            raise MissingIndexError(
                f"Local SQLite index path {self._path} must not be a directory."
            )

        conn = _connect(self._path, read_only=True)
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
        if not chunk.vector or not all(
            math.isfinite(vector_value) for vector_value in chunk.vector
        ):
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


def _connect(
    path: Path,
    *,
    read_only: bool = False,
    must_exist: bool = False,
    configure_wal: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite connection configured for sqlite-vec operations."""
    target = path.resolve().as_uri() + ("?mode=ro" if read_only else "?mode=rw")
    connection = sqlite3.connect(
        target if read_only or must_exist else path,
        uri=read_only or must_exist,
        timeout=30.0,
        isolation_level=None,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        if not read_only and configure_wal:
            connection.execute("PRAGMA journal_mode = WAL")
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
    return _metadata_value(conn, "schema_version") in {"1", _SCHEMA_VERSION}


def _metadata_value(conn: sqlite3.Connection, key: str) -> str | None:
    """Return a stored metadata value from the local index."""
    row = conn.execute(
        f"SELECT value FROM {_METADATA_TABLE} WHERE key = ?",
        (key,),
    ).fetchone()
    return str(row["value"]) if row is not None else None


def _read_identity(conn: sqlite3.Connection) -> IndexIdentity:
    """Validate metadata against the physical vector table, never infer missing identity."""
    if _metadata_value(conn, "schema_version") != _SCHEMA_VERSION:
        raise IndexCompatibilityError()
    try:
        complete = _metadata_value(conn, "ingest_complete")
        if complete not in {"true", "false"}:
            raise ValueError("Missing completion marker")
        identity = IndexIdentity.model_validate(
            {
                "embedding": {
                    "provider": _metadata_value(conn, "embedding_provider"),
                    "model": _metadata_value(conn, "embedding_model"),
                    "endpoint": _metadata_value(conn, "embedding_endpoint"),
                },
                "dimension": int(_metadata_value(conn, "embedding_dimension") or "0"),
                "build_id": _metadata_value(conn, "build_id"),
                "complete": complete == "true",
            }
        )
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (_VECTORS_TABLE,)
        ).fetchone()
        match = re.search(r"float\[(\d+)\]", str(schema[0])) if schema else None
        if match is None or int(match[1]) != identity.dimension:
            raise ValueError("Vector schema does not match metadata")
        count = conn.execute(f"SELECT COUNT(*) FROM {_VECTORS_TABLE}").fetchone()[0]
        if count != _count_chunks(conn) or count == 0:
            raise ValueError("Vector and policy inventory disagree")
        return identity
    except (ValidationError, TypeError, ValueError, sqlite3.DatabaseError) as exc:
        raise IndexCompatibilityError() from exc


def _path_identity(path: Path) -> tuple[int, int, int, int] | None:
    """Capture the destination identity without following an unexpected symbolic link."""
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


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
    if not query_vector or not all(math.isfinite(vector_value) for vector_value in query_vector):
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


def _database_files(path: Path) -> tuple[Path, Path, Path]:
    """Return the main SQLite database path plus WAL sidecar paths."""
    return (
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    )
