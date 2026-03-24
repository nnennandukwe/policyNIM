"""LanceDB-backed local vector storage for PolicyNIM."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import lancedb

from policynim.contracts import IndexStore
from policynim.errors import MissingIndexError
from policynim.types import EmbeddedChunk, PolicyChunk, PolicyMetadata, ScoredChunk


class LanceDBIndexStore(IndexStore):
    """Stores embedded policy chunks in a local LanceDB table."""

    def __init__(self, *, uri: Path, table_name: str) -> None:
        self._uri = uri
        self._table_name = table_name
        self._uri.mkdir(parents=True, exist_ok=True)
        connect = cast(Any, getattr(lancedb, "connect"))
        self._db = connect(self._uri.as_posix())

    @property
    def uri(self) -> Path:
        """Return the underlying LanceDB directory."""
        return self._uri

    @property
    def table_name(self) -> str:
        """Return the configured table name."""
        return self._table_name

    def replace(self, chunks: Sequence[EmbeddedChunk]) -> None:
        """Replace table contents with embedded policy chunks."""
        if not chunks:
            raise MissingIndexError("Cannot build an index without any embedded policy chunks.")

        _validate_vector_dimensions(chunks)
        rows = [self._row_from_chunk(chunk) for chunk in chunks]
        self._db.create_table(self._table_name, rows, mode="overwrite")

    def exists(self) -> bool:
        """Return whether the configured table exists."""
        try:
            self._db.open_table(self._table_name)
        except ValueError:
            return False
        except FileNotFoundError:
            return False
        except OSError:
            return False
        return True

    def count(self) -> int:
        """Return the row count for the configured table."""
        if not self.exists():
            return 0
        return int(self._db.open_table(self._table_name).count_rows())

    def list_chunks(self) -> list[PolicyChunk]:
        """Return all indexed chunks without vectors."""
        table = self._require_table()
        rows = cast(list[dict[str, object]], table.to_arrow().to_pylist())
        return [self._policy_chunk_from_row(row) for row in rows]

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        domain: str | None = None,
    ) -> list[ScoredChunk]:
        """Search the local vector index and return ranked policy chunks."""
        table = self._require_table()
        query = table.search(list(query_embedding)).metric("cosine")
        if domain:
            query = query.where(f"domain = {_quote_sql_string(domain)}")

        rows = cast(list[dict[str, object]], query.limit(top_k).to_list())
        return [self._chunk_from_row(row) for row in rows]

    def _require_table(self) -> Any:
        if not self.exists():
            raise MissingIndexError(
                f"Local index table {self._table_name!r} does not exist at {self._uri}."
            )

        table = self._db.open_table(self._table_name)
        if int(table.count_rows()) == 0:
            raise MissingIndexError(
                f"Local index table {self._table_name!r} exists but contains no rows."
            )
        return table

    def _row_from_chunk(self, chunk: EmbeddedChunk) -> dict[str, object]:
        return {
            "vector": chunk.vector,
            "chunk_id": chunk.chunk_id,
            "path": chunk.path,
            "section": chunk.section,
            "lines": chunk.lines,
            "text": chunk.text,
            "policy_id": chunk.policy.policy_id,
            "title": chunk.policy.title,
            "doc_type": chunk.policy.doc_type,
            "domain": chunk.policy.domain,
            "tags": chunk.policy.tags,
            "grounded_in": chunk.policy.grounded_in,
        }

    def _chunk_from_row(self, row: dict[str, object]) -> ScoredChunk:
        metadata = self._policy_metadata_from_row(row)
        distance = _float_value(row.get("_distance"), default=0.0)
        score = max(0.0, 1.0 - distance)

        return ScoredChunk(
            chunk_id=str(row["chunk_id"]),
            path=str(row["path"]),
            section=str(row["section"]),
            lines=str(row["lines"]),
            text=str(row["text"]),
            policy=metadata,
            score=score,
        )

    def _policy_chunk_from_row(self, row: dict[str, object]) -> PolicyChunk:
        return PolicyChunk(
            chunk_id=str(row["chunk_id"]),
            path=str(row["path"]),
            section=str(row["section"]),
            lines=str(row["lines"]),
            text=str(row["text"]),
            policy=self._policy_metadata_from_row(row),
        )

    def _policy_metadata_from_row(self, row: dict[str, object]) -> PolicyMetadata:
        return PolicyMetadata(
            policy_id=str(row["policy_id"]),
            title=str(row["title"]),
            doc_type=str(row["doc_type"]),
            domain=str(row["domain"]),
            tags=_string_list(row.get("tags")),
            grounded_in=_string_list(row.get("grounded_in")),
        )


def _validate_vector_dimensions(chunks: Sequence[EmbeddedChunk]) -> None:
    dimension: int | None = None
    for chunk in chunks:
        if not chunk.vector:
            raise MissingIndexError(f"Chunk {chunk.chunk_id!r} does not have an embedding vector.")
        if dimension is None:
            dimension = len(chunk.vector)
        elif len(chunk.vector) != dimension:
            raise MissingIndexError("All embedded chunks must share the same vector dimension.")


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values: list[str] = []
        for item in value:
            cleaned = str(item).strip()
            if cleaned:
                values.append(cleaned)
        return values
    cleaned = str(value).strip()
    return [cleaned] if cleaned else []


def _float_value(value: object, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return default
