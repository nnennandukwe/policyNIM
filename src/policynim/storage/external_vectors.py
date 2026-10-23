"""External vector retrieval with authoritative source reads from the retained generation."""

from __future__ import annotations

import math
from collections.abc import Sequence

from policynim.storage.index_readiness import IndexReadinessReport
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.storage.vectors import (
    VectorBackend,
    VectorRecord,
    VectorStorageError,
    float32_vector,
)
from policynim.types import ScoredChunk


class ExternalVectorIndexStore(SQLiteVecIndexStore):
    """Retain canonical source/lease behavior while replacing the vector-search operation."""

    def __init__(self, *, backend: VectorBackend, namespace: str, **kwargs) -> None:
        """Own the external client and the canonical generation's reader lifetime."""
        super().__init__(**kwargs)
        self._backend = backend
        self._namespace = namespace
        self._validated_generation: str | None = None
        self._canonical_records: list[VectorRecord] = []

    def validate_identity(self) -> None:
        """Require both canonical identity and exact external replica contents."""
        super().validate_identity()
        generation_id = self.generation_id
        if generation_id is None:
            raise VectorStorageError("External retrieval requires a complete generation.")
        if self._validated_generation == generation_id:
            return
        records = self.vector_records()
        self._backend.validate(self._namespace, generation_id, records)
        self._canonical_records = records
        self._validated_generation = generation_id

    def probe_readiness(self) -> IndexReadinessReport:
        """Check external availability with one bounded query, not a full replica download."""
        report = super().inspect_readiness()
        if report.state != "ready":
            return report
        try:
            identity = self.inspect_identity()
            generation_id = self.generation_id
            if generation_id is None:
                raise VectorStorageError("External retrieval requires a complete generation.")
            self._backend.probe()
            self._backend.search(
                self._namespace, generation_id, [0.0] * identity.dimension, 1, None
            )
        except Exception:
            return report.model_copy(update={"state": "external_unavailable", "compatible": False})
        return report

    def inspect_readiness(self) -> IndexReadinessReport:
        """Keep explicit readiness inspection as the complete compatibility check."""
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
        records = self._canonical_records
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
