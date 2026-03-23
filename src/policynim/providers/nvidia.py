"""NVIDIA-hosted embedding adapter for PolicyNIM."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

from policynim.contracts import Embedder
from policynim.errors import ConfigurationError, ProviderError
from policynim.settings import Settings

logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("openai._base_client").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


class NVIDIAEmbedder(Embedder):
    """Embeds policy content through NVIDIA's OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        batch_size: int,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ConfigurationError("NVIDIA_API_KEY is required for embeddings.")

        self._model = model
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> NVIDIAEmbedder:
        """Construct an embedder from application settings."""
        return cls(
            api_key=settings.nvidia_api_key or "",
            model=settings.nvidia_embed_model,
            base_url=settings.nvidia_base_url,
            batch_size=settings.embed_batch_size,
            timeout_seconds=settings.nvidia_timeout_seconds,
            max_retries=settings.nvidia_max_retries,
        )

    @property
    def model_name(self) -> str:
        """Return the configured embedding model identifier."""
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed policy chunk text in batches."""
        normalized = [_normalize_text(text, field_name="document text") for text in texts]
        if not normalized:
            return []

        embeddings: list[list[float]] = []
        for start in range(0, len(normalized), self._batch_size):
            batch = normalized[start : start + self._batch_size]
            embeddings.extend(self._request_embeddings(batch, input_type="passage"))
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        """Embed one search query."""
        normalized = _normalize_text(text, field_name="query")
        embeddings = self._request_embeddings([normalized], input_type="query")
        return embeddings[0]

    def _request_embeddings(
        self,
        texts: Sequence[str],
        *,
        input_type: str,
    ) -> list[list[float]]:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.embeddings.create(
                    model=self._model,
                    input=list(texts),
                    encoding_format="float",
                    extra_body={
                        "input_type": input_type,
                        "truncate": "NONE",
                    },
                )
                return _validate_embeddings_response(response.data, expected_count=len(texts))
            except AuthenticationError as exc:
                raise ConfigurationError(
                    "NVIDIA authentication failed. Verify NVIDIA_API_KEY is valid."
                ) from exc
            except BadRequestError as exc:
                raise ProviderError(f"NVIDIA embeddings request was rejected: {exc}") from exc
            except APIStatusError as exc:
                if exc.status_code in {401, 403}:
                    raise ConfigurationError(
                        "NVIDIA authentication failed. Verify NVIDIA_API_KEY is valid."
                    ) from exc
                if exc.status_code == 429 or exc.status_code >= 500:
                    if attempt < self._max_retries:
                        continue
                raise ProviderError(
                    f"NVIDIA embeddings request failed with status {exc.status_code}."
                ) from exc
            except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
                if attempt < self._max_retries:
                    continue
                raise ProviderError("NVIDIA embeddings request failed after retries.") from exc
            except Exception as exc:  # pragma: no cover - defensive guard.
                raise ProviderError("Unexpected NVIDIA embeddings failure.") from exc

        raise ProviderError("NVIDIA embeddings request failed after retries.")


def _normalize_text(text: str, *, field_name: str) -> str:
    normalized = text.strip()
    if not normalized:
        raise ValueError(f"{field_name.capitalize()} must not be empty.")
    return normalized


def _validate_embeddings_response(
    data: Sequence[Any],
    *,
    expected_count: int,
) -> list[list[float]]:
    if len(data) != expected_count:
        raise ProviderError("NVIDIA embeddings response count did not match the number of inputs.")

    embeddings: list[list[float]] = []
    dimension: int | None = None
    for item in data:
        embedding = list(getattr(item, "embedding", []))
        if not embedding:
            raise ProviderError("NVIDIA embeddings response returned an empty vector.")
        if dimension is None:
            dimension = len(embedding)
        elif len(embedding) != dimension:
            raise ProviderError("NVIDIA embeddings response returned mixed vector dimensions.")
        embeddings.append([float(value) for value in embedding])

    return embeddings
