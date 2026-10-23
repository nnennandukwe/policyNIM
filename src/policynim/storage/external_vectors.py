"""External vector retrieval with authoritative source reads from the retained generation."""

from __future__ import annotations

import math
from collections.abc import Sequence

from policynim.storage.index_readiness import IndexReadinessReport
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.storage.vectors import VectorBackend, VectorStorageError, float32_vector
from policynim.types import ScoredChunk


class ExternalVectorIndexStore(SQLiteVecIndexStore):
    """Retain canonical source/lease behavior while replacing the vector-search operation."""

    def __init__(self, *, backend: VectorBackend, namespace: str, **kwargs) -> None:
        """Own the external client and the canonical generation's reader lifetime."""
        super().__init__(**kwargs)
        self._backend = backend
        self._namespace = namespace

    def validate_identity(self) -> None:
        """Require both canonical identity and exact external replica contents."""
        super().validate_identity()
        if self.generation_id is None:
            raise VectorStorageError("External retrieval requires a complete generation.")
        self._backend.validate(self._namespace, self.generation_id, self.vector_records())

    def inspect_readiness(self) -> IndexReadinessReport:
        """Keep source readiness separate from external availability and compatibility."""
        report = super().inspect_readiness()
        if report.state == "ready":
            try:
                self.validate_identity()
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
        records = self.vector_records()
        vector = float32_vector(query_embedding, len(records[0].vector))
        if top_k <= 0:
            return []
        assert self.generation_id is not None
        keys = self._backend.search(self._namespace, self.generation_id, vector, top_k, domain)
        canonical = {record.key: record for record in records}
        if len(keys) > top_k or len(set(keys)) != len(keys):
            raise VectorStorageError("External retrieval returned invalid identifiers.")
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
