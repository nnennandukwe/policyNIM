"""Tests for the NVIDIA embedder adapter."""

from __future__ import annotations

import pytest
from openai import AuthenticationError, RateLimitError

from policynim.errors import ConfigurationError, ProviderError
from policynim.providers.nvidia import NVIDIAEmbedder


class MockAuthenticationError(AuthenticationError):
    """Minimal auth error subclass for provider classification tests."""

    def __init__(self) -> None:
        Exception.__init__(self, "bad API key")


class MockRateLimitError(RateLimitError):
    """Minimal rate-limit error subclass for provider classification tests."""

    def __init__(self) -> None:
        Exception.__init__(self, "too many requests")
        self.status_code = 429


class RaisingEmbeddingsClient:
    """Embeddings client stub that always raises the supplied exception."""

    def __init__(self, exc: Exception) -> None:
        self.embeddings = self
        self._exc = exc

    def create(self, **kwargs):  # noqa: ANN003
        raise self._exc


def test_embedder_classifies_upstream_auth_failures() -> None:
    embedder = NVIDIAEmbedder(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1",
        batch_size=2,
        timeout_seconds=1,
        max_retries=0,
    )
    embedder._client = RaisingEmbeddingsClient(MockAuthenticationError())  # type: ignore[assignment]

    with pytest.raises(ConfigurationError, match="authentication failed") as excinfo:
        embedder.embed_query("backend logging")

    assert excinfo.value.failure_class == "auth"


def test_embedder_classifies_upstream_rate_limits() -> None:
    embedder = NVIDIAEmbedder(
        api_key="test-key",
        model="mock-model",
        base_url="https://example.invalid/v1",
        batch_size=2,
        timeout_seconds=1,
        max_retries=0,
    )
    embedder._client = RaisingEmbeddingsClient(MockRateLimitError())  # type: ignore[assignment]

    with pytest.raises(ProviderError, match="failed after retries") as excinfo:
        embedder.embed_query("backend logging")

    assert excinfo.value.failure_class == "rate_limit"
