"""Storage-only contract for generation-bound, derived vector indexes."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from policynim.errors import PolicyNIMError


class VectorStorageError(PolicyNIMError):
    """Expose bounded recovery text without database messages or connection secrets."""

    def __init__(self, detail: str = "External vector storage couldn't be verified.") -> None:
        """Preserve existing remote data and identify the explicit recovery command."""
        super().__init__(
            detail + " Existing data is preserved. Check access and run `policynim vector sync`; "
            "use a new POLICYNIM_VECTOR_NAMESPACE for an incomplete or unrelated index.",
            failure_class="vector_storage_unavailable",
        )


class VectorRecord(BaseModel):
    """Only retrieval identifiers, domain, and float32 vectors cross the storage boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    domain: str
    vector: list[float] = Field(min_length=1)


def float32_vector(values: Sequence[float], dimension: int) -> list[float]:
    """Reject malformed query/storage vectors and compare native float32 values exactly."""
    try:
        if len(values) != dimension or any(not math.isfinite(value) for value in values):
            raise ValueError
        result = list(struct.unpack(f"<{dimension}f", struct.pack(f"<{dimension}f", *values)))
        if any(not math.isfinite(value) for value in result):
            raise ValueError
        return result
    except (TypeError, ValueError, OverflowError, struct.error):
        raise VectorStorageError("Vector dimensions or numeric values aren't compatible.") from None


def vector_namespace(prefix: str, generation_id: str) -> str:
    """Fit Postgres identifiers while preserving the full generation in stored records."""
    suffix = hashlib.sha256(generation_id.encode("ascii")).hexdigest()[:40]
    return prefix + "_" + suffix


def validate_records(actual: Sequence[VectorRecord], expected: Sequence[VectorRecord]) -> None:
    """Require complete identity and vector agreement before adopting an existing index."""
    if len(actual) != len(expected) or len({record.key for record in actual}) != len(actual):
        raise VectorStorageError("External vector records don't match the selected generation.")
    by_key = {record.key: record for record in expected}
    for record in actual:
        canonical = by_key.get(record.key)
        if (
            canonical is None
            or record.domain != canonical.domain
            or float32_vector(record.vector, len(canonical.vector)) != canonical.vector
        ):
            raise VectorStorageError("External vector records don't match the selected generation.")


class VectorBackend(Protocol):
    """Publish only fresh namespaces; validate exact records and query without policy text."""

    def probe(self) -> None:
        """Check authenticated connectivity without creating database objects."""
        ...

    def prepare(self, name: str, generation: str, records: Sequence[VectorRecord]) -> None:
        """Create a fresh index, or validate an existing one without changing it."""
        ...

    def validate(self, name: str, generation: str, records: Sequence[VectorRecord]) -> None:
        """Refuse incomplete, incompatible, or unowned data."""
        ...

    def check(self, name: str, generation: str, dimension: int, count: int) -> None:
        """Check schema, generation binding, and counts without transferring stored vectors."""
        ...

    def search(
        self, name: str, generation: str, vector: Sequence[float], top_k: int, domain: str | None
    ) -> list[VectorRecord]:
        """Return bounded ranked records for canonical comparison and source hydration."""
        ...

    def close(self) -> None:
        """Release owned transport resources."""
        ...
