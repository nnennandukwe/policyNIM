"""Qdrant REST adapter for immutable generation-derived vector collections."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from policynim.operations import check_budget
from policynim.storage.vectors import VectorRecord, VectorStorageError, validate_records


class QdrantVectors:
    """Own a bounded authenticated client; never overwrite existing collection records."""

    def __init__(self, *, url: str, api_key: str | None, timeout: float) -> None:
        """Create transport without connecting or trusting ambient proxy settings."""
        self._client = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"api-key": api_key} if api_key else {},
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        )

    def _request(self, method: str, path: str, payload: Any = None) -> Any:
        """Return successful JSON without exposing raw transport errors or response bodies."""
        try:
            check_budget()
            with self._client.stream(method, path, json=payload) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes(65536):
                    check_budget()
                    body.extend(chunk)
                    if len(body) > 16 * 1024 * 1024:
                        raise VectorStorageError("Qdrant response exceeded the read budget.")
                return json.loads(body)["result"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, RecursionError):
            raise VectorStorageError("Qdrant didn't return a valid successful response.") from None

    def probe(self) -> None:
        """Authenticate a read-only collection listing."""
        result = self._request("GET", "/collections")
        if not isinstance(result, dict) or not isinstance(result.get("collections"), list):
            raise VectorStorageError("Qdrant collection discovery returned an invalid response.")

    def prepare(self, name: str, generation: str, records: Sequence[VectorRecord]) -> None:
        """Write only after successful creation of a previously absent collection."""
        try:
            check_budget()
            with self._client.stream("GET", f"/collections/{name}") as response:
                status = response.status_code
        except httpx.HTTPError:
            raise VectorStorageError() from None
        if status == 200:
            self.validate(name, generation, records)
            return
        if status != 404 or not records:
            raise VectorStorageError("Qdrant namespace discovery failed.")
        created = self._request(
            "PUT",
            f"/collections/{name}",
            {"vectors": {"size": len(records[0].vector), "distance": "Euclid"}},
        )
        if created is not True:
            raise VectorStorageError("Qdrant didn't confirm fresh collection ownership.")
        for offset in range(0, len(records), 128):
            self._request(
                "PUT",
                f"/collections/{name}/points?wait=true",
                {
                    "points": [
                        {
                            "id": str(uuid5(NAMESPACE_URL, record.key)),
                            "vector": record.vector,
                            "payload": {
                                "key": record.key,
                                "domain": record.domain,
                                "generation": generation,
                            },
                        }
                        for record in records[offset : offset + 128]
                    ]
                },
            )
        self.validate(name, generation, records)

    def validate(self, name: str, generation: str, records: Sequence[VectorRecord]) -> None:
        """Read back bounded complete records and compare with the sealed local generation."""
        try:
            info = self._request("GET", f"/collections/{name}")
            config = info["config"]["params"]["vectors"]
            if config["distance"] != "Euclid" or config["size"] != len(records[0].vector):
                raise VectorStorageError("Qdrant metric or dimensions don't match.")
            actual: list[VectorRecord] = []
            offset = None
            seen_offsets: set[str] = set()
            while True:
                result = self._request(
                    "POST",
                    f"/collections/{name}/points/scroll",
                    {
                        "limit": min(128, len(records) + 1),
                        "offset": offset,
                        "with_payload": True,
                        "with_vector": True,
                    },
                )
                for point in result["points"]:
                    payload = point["payload"]
                    if payload["generation"] != generation:
                        raise VectorStorageError("Qdrant generation binding doesn't match.")
                    key = payload["key"]
                    if point["id"] != str(uuid5(NAMESPACE_URL, key)):
                        raise VectorStorageError("Qdrant point ownership doesn't match.")
                    actual.append(
                        VectorRecord(key=key, domain=payload["domain"], vector=point["vector"])
                    )
                offset = result["next_page_offset"]
                if len(actual) > len(records):
                    raise VectorStorageError("Qdrant contains unexpected records.")
                if offset is None:
                    break
                if str(offset) in seen_offsets:
                    raise VectorStorageError("Qdrant pagination didn't advance.")
                seen_offsets.add(str(offset))
            validate_records(actual, records)
        except (KeyError, TypeError, ValueError, IndexError):
            raise VectorStorageError("Qdrant record validation failed.") from None

    def search(
        self, name: str, generation: str, vector: Sequence[float], top_k: int, domain: str | None
    ) -> list[str]:
        """Scope every query to the generation and optional policy domain."""
        conditions = [{"key": "generation", "match": {"value": generation}}]
        if domain is not None:
            conditions.append({"key": "domain", "match": {"value": domain}})
        result = self._request(
            "POST",
            f"/collections/{name}/points/query",
            {
                "query": list(vector),
                "filter": {"must": conditions},
                "limit": top_k,
                "params": {"exact": True},
                "with_payload": True,
            },
        )
        try:
            keys = []
            for point in result["points"]:
                payload = point["payload"]
                if payload["generation"] != generation or (
                    domain is not None and payload["domain"] != domain
                ):
                    raise VectorStorageError("Qdrant returned an out-of-scope record.")
                keys.append(str(payload["key"]))
            return keys
        except (KeyError, TypeError, ValueError):
            raise VectorStorageError("Qdrant query response couldn't be validated.") from None

    def close(self) -> None:
        """Release the owned HTTP connection pool."""
        self._client.close()
