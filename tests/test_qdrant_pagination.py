"""Exercise pagination rejection and recovery through the Qdrant HTTP adapter."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest

from policynim.storage.qdrant_vectors import QdrantVectors
from policynim.storage.vectors import VectorRecord, VectorStorageError

RECORDS = [VectorRecord(key=key, domain="test", vector=[1.0]) for key in ("a", "b")]


def page(index: int, offset: int | None) -> dict[str, Any]:
    """Encode one expected record as a valid Qdrant scroll response."""
    record = RECORDS[index]
    return {
        "points": [
            {
                "id": str(uuid5(NAMESPACE_URL, record.key)),
                "vector": record.vector,
                "payload": {"key": record.key, "domain": record.domain, "generation": "generation"},
            }
        ],
        "next_page_offset": offset,
    }


@contextmanager
def adapter(respond: Any) -> Iterator[QdrantVectors]:
    """Attach an offline HTTP transport and close it after each scenario."""
    backend = QdrantVectors(url="http://localhost:6333", api_key=None, timeout=1)
    backend._client.close()
    backend._client = httpx.Client(
        base_url="http://localhost:6333", transport=httpx.MockTransport(respond)
    )
    try:
        yield backend
    finally:
        backend.close()


def collection_info() -> dict[str, Any]:
    """Return the expected collection schema without contacting a server."""
    return {"config": {"params": {"vectors": {"distance": "Euclid", "size": 1}}}}


@pytest.mark.parametrize("partial", [False, True])
def test_empty_continuation_rejected_and_retry_recovers(partial: bool) -> None:
    """Refuse a nonprogressing response and permit corrected validation on retry."""
    requests: list[httpx.Request] = []
    recovering = False
    scrolls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        """Serve invalid changing offsets until recovery, with an independent bound."""
        nonlocal scrolls
        requests.append(request)
        if request.method == "GET":
            result = collection_info()
        else:
            assert request.method == "POST" and request.url.path.endswith("/points/scroll")
            scrolls += 1
            assert scrolls <= 5, "independent request limit exceeded"
            if recovering:
                result = page(scrolls - 1, None if scrolls == 2 else scrolls)
            elif partial and scrolls == 1:
                result = page(0, 1)
            else:
                result = {"points": [], "next_page_offset": scrolls}
        return httpx.Response(200, json={"result": result})

    with adapter(respond) as backend:
        with pytest.raises(VectorStorageError, match="empty continuation page"):
            backend.validate("audit", "generation", RECORDS)
        assert scrolls == (2 if partial else 1)
        recovering = True
        scrolls = 0
        backend.validate("audit", "generation", RECORDS)
        assert scrolls == 2
    assert all(request.method in {"GET", "POST"} for request in requests)


@pytest.mark.parametrize("terminal_empty", [False, True])
def test_valid_pagination(terminal_empty: bool) -> None:
    """Allow short nonempty pages and an optional terminal empty response."""
    responses = [page(0, 1), page(1, 2 if terminal_empty else None)]
    if terminal_empty:
        responses.append({"points": [], "next_page_offset": None})
    scrolls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        """Return the prepared pages through the actual HTTP response parser."""
        nonlocal scrolls
        if request.method == "GET":
            result = collection_info()
        else:
            result = responses[scrolls]
            scrolls += 1
        return httpx.Response(200, json={"result": result})

    with adapter(respond) as backend:
        backend.validate("audit", "generation", RECORDS)
    assert scrolls == len(responses)
