"""Server-owned NVIDIA clients borrowed by operation-local service graphs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Literal, overload

import httpx
from openai import OpenAI

from policynim.errors import ProviderError
from policynim.operations import close_components
from policynim.settings import Settings


class NVIDIAClients:
    """Own immutable SDK/HTTP clients; close only after all server workers drain."""

    def __init__(self) -> None:
        """Allocate a lazy pool without requiring credentials at server startup."""
        self._lock = Lock()
        self._clients: dict[tuple[str, str, float, str], OpenAI | httpx.Client] = {}
        self._closed = False

    def get(self, settings: Settings, *, retrieval: bool = False) -> OpenAI | httpx.Client:
        """Reuse clients with identical immutable configuration across worker threads."""
        endpoint = settings.nvidia_retrieval_base_url if retrieval else settings.nvidia_base_url
        key = (
            "retrieval" if retrieval else "sdk",
            endpoint,
            settings.nvidia_timeout_seconds,
            (settings.nvidia_api_key or "").strip(),
        )
        with self._lock:
            if self._closed:
                raise ProviderError(
                    "Provider clients are shutting down. Retry later.", failure_class="shutdown"
                )
            if key not in self._clients:
                if retrieval:
                    client: OpenAI | httpx.Client = httpx.Client(
                        base_url=endpoint.rstrip("/"),
                        timeout=settings.nvidia_timeout_seconds,
                        headers={
                            "Authorization": f"Bearer {key[3]}",
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                    )
                else:
                    client = OpenAI(
                        api_key=key[3],
                        base_url=endpoint,
                        timeout=settings.nvidia_timeout_seconds,
                        max_retries=0,
                    )
                self._clients[key] = client
            return self._clients[key]

    def close(self) -> None:
        """Attempt every owned close once, preserving the first cleanup failure."""
        with self._lock:
            self._closed = True
            clients, self._clients = list(self._clients.values()), {}
            close_components(*clients)


_POOL: ContextVar[NVIDIAClients | None] = ContextVar("policynim_nvidia_clients", default=None)


@contextmanager
def borrow_clients(clients: NVIDIAClients) -> Iterator[None]:
    """Make server-owned clients available only during an admitted operation."""
    token = _POOL.set(clients)
    try:
        yield
    finally:
        _POOL.reset(token)


@overload
def shared_client(settings: Settings, *, retrieval: Literal[True]) -> httpx.Client | None:
    """Borrow the retrieval HTTP client."""
    ...


@overload
def shared_client(settings: Settings, *, retrieval: Literal[False] = False) -> OpenAI | None:
    """Borrow the OpenAI-compatible SDK client."""
    ...


def shared_client(settings: Settings, *, retrieval: bool = False) -> OpenAI | httpx.Client | None:
    """Return a borrowed client, or let a CLI adapter create and own its client."""
    pool = _POOL.get()
    return pool.get(settings, retrieval=retrieval) if pool is not None else None
