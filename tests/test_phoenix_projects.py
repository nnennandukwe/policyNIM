"""Pinned Phoenix-client project lookup behavior, independent from job scheduling."""

from __future__ import annotations

from importlib.metadata import version

import httpx
import pytest

from policynim.services.eval import _ensure_phoenix_project


@pytest.mark.parametrize("status", [200, 401, 403, 404, 500])
def test_pinned_phoenix_client_creates_only_after_confirmed_absence(status: int) -> None:
    """Real client HTTP status exceptions preserve authorization/server failures without writes."""
    from phoenix.client import Client

    assert version("arize-phoenix-client") == "2.3.1"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Expose the project's actual GET/POST boundary through an offline HTTP transport."""
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(status, json={"data": {"id": "project-id", "name": "fixture"}})
        return httpx.Response(200, json={"data": {"id": "project-id", "name": "fixture"}})

    with httpx.Client(
        base_url="http://phoenix.example", transport=httpx.MockTransport(handler)
    ) as http:
        client = Client(http_client=http)
        if status in {401, 403, 500}:
            with pytest.raises(httpx.HTTPStatusError) as failure:
                _ensure_phoenix_project(client, "fixture")
            assert failure.value.response.status_code == status
        else:
            _ensure_phoenix_project(client, "fixture")
    assert calls == (["GET", "POST"] if status == 404 else ["GET"])


def test_pinned_phoenix_transport_failure_never_creates_project() -> None:
    """A missing response isn't evidence that a project is absent."""
    from phoenix.client import Client

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Fail the real client's lookup before any HTTP response exists."""
        calls.append(request.method)
        raise httpx.ConnectError("fixture transport unavailable", request=request)

    with httpx.Client(
        base_url="http://phoenix.example", transport=httpx.MockTransport(handler)
    ) as http:
        with pytest.raises(httpx.ConnectError):
            _ensure_phoenix_project(Client(http_client=http), "fixture")
    assert calls == ["GET"]
