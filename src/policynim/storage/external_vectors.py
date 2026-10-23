"""External vector retrieval with authoritative source reads from the retained generation."""

from __future__ import annotations

import math
from collections.abc import Sequence

from policynim.storage.index_readiness import IndexReadinessReport
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.storage.vectors import (
    VectorBackend,
    VectorStorageError,
    float32_vector,
    validate_records,
)
from policynim.types import ScoredChunk


class ExternalVectorIndexStore(SQLiteVecIndexStore):
    """Retain canonical source/lease behavior while replacing the vector-search operation."""

    def __init__(self, *, backend: VectorBackend, namespace: str, **kwargs) -> None:
        """Own the external client and the canonical generation's reader lifetime."""
        super().__init__(**kwargs)
        self._backend = backend
        self._namespace = namespace

    def validate_identity(self) -> None:
        """Require canonical identity and compact remote schema/count/generation checks."""
        super().validate_identity()
        if self.generation_id is None:
            raise VectorStorageError("External retrieval requires a complete generation.")
        self._backend.check(
            self._namespace, self.generation_id, self.inspect_identity().dimension, self.count()
        )

    def inspect_readiness(self) -> IndexReadinessReport:
        """Keep source readiness separate from external availability and compatibility."""
        report = super().inspect_readiness()
        if report.state == "ready":
            try:
                if self.generation_id is None or report.identity is None:
                    raise VectorStorageError("External retrieval requires a complete generation.")
                self._backend.check(
                    self._namespace,
                    self.generation_id,
                    report.identity.dimension,
                    report.chunk_count or 0,
                )
            except VectorStorageError:
                return report.model_copy(
                    update={"state": "external_unavailable", "compatible": False}
                )
        return report

    def search(
        self, query_embedding: Sequence[float], *, top_k: int, domain: str | None = None
    ) -> list[ScoredChunk]:
        """Resolve remote keys locally and calculate scores from canonical vectors."""
        self.validate_identity()
        vector = float32_vector(query_embedding, self.inspect_identity().dimension)
        if top_k <= 0:
            return []
        assert self.generation_id is not None
        matches = self._backend.search(self._namespace, self.generation_id, vector, top_k, domain)
        keys = [record.key for record in matches]
        if len(keys) > top_k or len(set(keys)) != len(keys):
            raise VectorStorageError("External retrieval returned invalid identifiers.")
        canonical = {record.key: record for record in self.vector_records(keys)}
        if any(key not in canonical for key in keys):
            raise VectorStorageError("External retrieval returned an out-of-scope identifier.")
        validate_records(matches, list(canonical.values()))
        results = []
        for key in keys:
            record = canonical.get(key)
            if record is None or (domain is not None and record.domain != domain):
                raise VectorStorageError("External retrieval returned an out-of-scope identifier.")
            chunk = self.get_chunk(key)
            if chunk is None:
                raise VectorStorageError("External retrieval lost its canonical source owner.")
            distance = math.sqrt(
                sum((left - right) ** 2 for left, right in zip(vector, record.vector, strict=True))
            )
            results.append(ScoredChunk(**chunk.model_dump(), score=max(0.0, 1.0 - distance)))
        return results

    def close(self) -> None:
        """Release both transports and the held generation, including exceptional exits."""
        try:
            self._backend.close()
        finally:
            super().close()
