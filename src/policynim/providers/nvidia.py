"""NVIDIA-hosted provider adapters for PolicyNIM."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from types import TracebackType
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)
from openai.types.chat import ChatCompletionMessageParam
from pydantic import ValidationError

from policynim.contracts import Embedder, Generator, Reranker
from policynim.errors import ConfigurationError, ProviderError
from policynim.settings import Settings
from policynim.types import GeneratedPreflightDraft, PreflightRequest, ScoredChunk

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
                raise _auth_error("embeddings") from exc
            except BadRequestError as exc:
                raise ProviderError(
                    f"NVIDIA embeddings request was rejected: {exc}",
                    failure_class="bad_request",
                ) from exc
            except RateLimitError as exc:
                if attempt < self._max_retries:
                    continue
                raise ProviderError(
                    "NVIDIA embeddings request failed after retries.",
                    failure_class="rate_limit",
                ) from exc
            except APIStatusError as exc:
                if exc.status_code in {401, 403}:
                    raise _auth_error("embeddings") from exc
                if exc.status_code == 429:
                    if attempt < self._max_retries:
                        continue
                    raise ProviderError(
                        "NVIDIA embeddings request failed after retries.",
                        failure_class="rate_limit",
                    ) from exc
                if _should_retry_status(exc.status_code) and attempt < self._max_retries:
                    continue
                raise ProviderError(
                    f"NVIDIA embeddings request failed with status {exc.status_code}.",
                    failure_class="http_status",
                ) from exc
            except APITimeoutError as exc:
                if attempt < self._max_retries:
                    continue
                raise ProviderError(
                    "NVIDIA embeddings request failed after retries.",
                    failure_class="timeout",
                ) from exc
            except APIConnectionError as exc:
                if attempt < self._max_retries:
                    continue
                raise ProviderError(
                    "NVIDIA embeddings request failed after retries.",
                    failure_class="connection",
                ) from exc
            except Exception as exc:  # pragma: no cover - defensive guard.
                raise ProviderError(
                    "Unexpected NVIDIA embeddings failure.",
                    failure_class="unexpected",
                ) from exc

        raise ProviderError(
            "NVIDIA embeddings request failed after retries.",
            failure_class="unexpected",
        )


class NVIDIAReranker(Reranker):
    """Reranks candidate passages through NVIDIA's retrieval endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        client: httpx.Client | None = None,
    ) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ConfigurationError("NVIDIA_API_KEY is required for reranking.")

        self._model = model
        self._max_retries = max_retries
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> NVIDIAReranker:
        """Construct a reranker from application settings."""
        return cls(
            api_key=settings.nvidia_api_key or "",
            model=settings.nvidia_rerank_model,
            base_url=settings.nvidia_retrieval_base_url,
            timeout_seconds=settings.nvidia_timeout_seconds,
            max_retries=settings.nvidia_max_retries,
        )

    def __enter__(self) -> NVIDIAReranker:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the owned HTTP client when this reranker created it."""
        if self._owns_client:
            self._client.close()

    def rerank(
        self,
        query: str,
        candidates: Sequence[ScoredChunk],
        *,
        top_k: int,
    ) -> list[ScoredChunk]:
        """Return the top reranked candidates for one query."""
        normalized_query = _normalize_text(query, field_name="query")
        if not candidates:
            return []

        payload = {
            "model": self._model,
            "query": {"text": normalized_query},
            "passages": [{"text": candidate.text} for candidate in candidates],
            "truncate": "END",
        }
        response = self._request_ranking(payload)
        scores = _extract_rerank_scores(response, expected_count=len(candidates))

        ranked = [
            candidate.model_copy(update={"score": float(score)})
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        ranked.sort(
            key=lambda chunk: chunk.score if chunk.score is not None else float("-inf"),
            reverse=True,
        )
        return ranked[:top_k]

    def _request_ranking(self, payload: dict[str, object]) -> Any:
        endpoint = f"{self._model}/reranking"
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(endpoint, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code in {401, 403}:
                    raise _auth_error("reranking") from exc
                if status_code == 429:
                    if attempt < self._max_retries:
                        continue
                    raise ProviderError(
                        "NVIDIA reranking request failed after retries.",
                        failure_class="rate_limit",
                    ) from exc
                if _should_retry_status(status_code) and attempt < self._max_retries:
                    continue
                raise ProviderError(
                    f"NVIDIA reranking request failed with status {status_code}.",
                    failure_class="http_status",
                ) from exc
            except httpx.ConnectError as exc:
                if attempt < self._max_retries:
                    continue
                raise ProviderError(
                    "NVIDIA reranking request failed after retries.",
                    failure_class="connection",
                ) from exc
            except (httpx.ReadTimeout, httpx.TimeoutException) as exc:
                if attempt < self._max_retries:
                    continue
                raise ProviderError(
                    "NVIDIA reranking request failed after retries.",
                    failure_class="timeout",
                ) from exc
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    "NVIDIA reranking response was not valid JSON.",
                    failure_class="invalid_response",
                ) from exc
            except Exception as exc:  # pragma: no cover - defensive guard.
                raise ProviderError(
                    "Unexpected NVIDIA reranking failure.",
                    failure_class="unexpected",
                ) from exc

        raise ProviderError(
            "NVIDIA reranking request failed after retries.",
            failure_class="unexpected",
        )


class NVIDIAGenerator(Generator):
    """Generates grounded policy drafts through NVIDIA chat completions."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        client: OpenAI | Any | None = None,
    ) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ConfigurationError("NVIDIA_API_KEY is required for grounded generation.")

        self._model = model
        self._max_retries = max_retries
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> NVIDIAGenerator:
        """Construct a generator from application settings."""
        return cls(
            api_key=settings.nvidia_api_key or "",
            model=settings.nvidia_chat_model,
            base_url=settings.nvidia_base_url,
            timeout_seconds=settings.nvidia_timeout_seconds,
            max_retries=settings.nvidia_max_retries,
        )

    def generate_preflight(
        self,
        request: PreflightRequest,
        context: Sequence[ScoredChunk],
    ) -> GeneratedPreflightDraft:
        """Generate a grounded preflight draft from retrieved context."""
        messages = _build_generation_messages(request, context)
        content = self._request_generation(messages)
        return _parse_generation_draft(content)

    def _request_generation(self, messages: list[ChatCompletionMessageParam]) -> str:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=0,
                    top_p=1,
                )
                return _extract_chat_content(response)
            except AuthenticationError as exc:
                raise _auth_error("grounded generation") from exc
            except BadRequestError as exc:
                raise ProviderError(
                    f"NVIDIA grounded generation request was rejected: {exc}",
                    failure_class="bad_request",
                ) from exc
            except RateLimitError as exc:
                if attempt < self._max_retries:
                    continue
                raise ProviderError(
                    "NVIDIA grounded generation request failed after retries.",
                    failure_class="rate_limit",
                ) from exc
            except APIStatusError as exc:
                if exc.status_code in {401, 403}:
                    raise _auth_error("grounded generation") from exc
                if exc.status_code == 429:
                    if attempt < self._max_retries:
                        continue
                    raise ProviderError(
                        "NVIDIA grounded generation request failed after retries.",
                        failure_class="rate_limit",
                    ) from exc
                if _should_retry_status(exc.status_code) and attempt < self._max_retries:
                    continue
                raise ProviderError(
                    f"NVIDIA grounded generation request failed with status {exc.status_code}.",
                    failure_class="http_status",
                ) from exc
            except APITimeoutError as exc:
                if attempt < self._max_retries:
                    continue
                raise ProviderError(
                    "NVIDIA grounded generation request failed after retries.",
                    failure_class="timeout",
                ) from exc
            except APIConnectionError as exc:
                if attempt < self._max_retries:
                    continue
                raise ProviderError(
                    "NVIDIA grounded generation request failed after retries.",
                    failure_class="connection",
                ) from exc
            except Exception as exc:  # pragma: no cover - defensive guard.
                raise ProviderError(
                    "Unexpected NVIDIA grounded generation failure.",
                    failure_class="unexpected",
                ) from exc

        raise ProviderError(
            "NVIDIA grounded generation request failed after retries.",
            failure_class="unexpected",
        )


def _auth_error(operation: str) -> ConfigurationError:
    return ConfigurationError(
        f"NVIDIA authentication failed during {operation}. Verify NVIDIA_API_KEY is valid.",
        failure_class="auth",
    )


def _should_retry_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


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
        raise ProviderError(
            "NVIDIA embeddings response count did not match the number of inputs.",
            failure_class="invalid_response",
        )

    embeddings: list[list[float]] = []
    dimension: int | None = None
    for item in data:
        embedding = list(getattr(item, "embedding", []))
        if not embedding:
            raise ProviderError(
                "NVIDIA embeddings response returned an empty vector.",
                failure_class="invalid_response",
            )
        if dimension is None:
            dimension = len(embedding)
        elif len(embedding) != dimension:
            raise ProviderError(
                "NVIDIA embeddings response returned mixed vector dimensions.",
                failure_class="invalid_response",
            )
        embeddings.append([float(value) for value in embedding])

    return embeddings


def _extract_rerank_scores(payload: Any, *, expected_count: int) -> list[float]:
    if isinstance(payload, list):
        return _extract_scores_from_list(payload, expected_count=expected_count)

    if isinstance(payload, dict):
        for key in ("scores", "logits", "data", "results", "output", "rankings"):
            value = payload.get(key)
            if isinstance(value, list):
                return _extract_scores_from_list(value, expected_count=expected_count)

    raise ProviderError(
        "NVIDIA reranking response format was not recognized.",
        failure_class="invalid_response",
    )


def _extract_scores_from_list(values: Sequence[Any], *, expected_count: int) -> list[float]:
    if all(isinstance(item, (int, float)) for item in values):
        scores = [float(item) for item in values]
        _validate_score_count(scores, expected_count=expected_count)
        return scores

    if not all(isinstance(item, dict) for item in values):
        raise ProviderError(
            "NVIDIA reranking response contained invalid items.",
            failure_class="invalid_response",
        )

    score_rows = list(values)
    indexed_scores: dict[int, float] = {}
    ordered_scores: list[float] = []
    for position, row in enumerate(score_rows):
        score = _extract_row_score(row)
        ordered_scores.append(score)
        index = _extract_row_index(row)
        if index is not None:
            indexed_scores[index] = score
        elif "rank" in row and isinstance(row["rank"], int):
            indexed_scores[position] = score

    if indexed_scores:
        if len(indexed_scores) != expected_count:
            raise ProviderError(
                "NVIDIA reranking response count did not match the number of inputs.",
                failure_class="invalid_response",
            )
        return [indexed_scores[index] for index in range(expected_count)]

    _validate_score_count(ordered_scores, expected_count=expected_count)
    return ordered_scores


def _validate_score_count(scores: Sequence[float], *, expected_count: int) -> None:
    if len(scores) != expected_count:
        raise ProviderError(
            "NVIDIA reranking response count did not match the number of inputs.",
            failure_class="invalid_response",
        )


def _extract_row_index(row: dict[str, Any]) -> int | None:
    for key in ("index", "passage_index", "document_index", "position"):
        value = row.get(key)
        if isinstance(value, int):
            return value
    return None


def _extract_row_score(row: dict[str, Any]) -> float:
    for key in ("score", "relevance_score", "logit", "value"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    raise ProviderError(
        "NVIDIA reranking response row did not include a numeric score.",
        failure_class="invalid_response",
    )


def _build_generation_messages(
    request: PreflightRequest,
    context: Sequence[ScoredChunk],
) -> list[ChatCompletionMessageParam]:
    system_prompt = (
        "You are PolicyNIM's grounded policy synthesis engine.\n"
        "Return ONLY valid JSON. Do not use markdown fences or commentary.\n"
        "The JSON must match this shape exactly:\n"
        "{\n"
        '  "summary": "string",\n'
        '  "applicable_policies": [\n'
        "    {\n"
        '      "policy_id": "string",\n'
        '      "title": "string",\n'
        '      "rationale": "string",\n'
        '      "citation_ids": ["chunk-id"]\n'
        "    }\n"
        "  ],\n"
        '  "implementation_guidance": ["string"],\n'
        '  "review_flags": ["string"],\n'
        '  "tests_required": ["string"],\n'
        '  "citation_ids": ["chunk-id"],\n'
        '  "insufficient_context": false\n'
        "}\n"
        "Rules:\n"
        "- Cite only by chunk_id values that appear in the provided context.\n"
        "- Do not invent new chunk IDs.\n"
        "- If the evidence is insufficient, set insufficient_context to true and "
        "keep the lists empty.\n"
        "- Keep the summary concise and task-specific."
    )
    user_prompt = (
        f"Task: {request.task}\n"
        f"Domain: {request.domain or 'none'}\n"
        f"Target top_k: {request.top_k}\n"
        "Retrieved context:\n"
        f"{_format_generation_context(context)}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _format_generation_context(context: Sequence[ScoredChunk]) -> str:
    if not context:
        return "(no retrieved context provided)"

    blocks: list[str] = []
    for index, chunk in enumerate(context, start=1):
        blocks.append(
            "\n".join(
                [
                    f"[{index}] chunk_id={chunk.chunk_id}",
                    f"policy_id={chunk.policy.policy_id}",
                    f"title={chunk.policy.title}",
                    f"path={chunk.path}",
                    f"section={chunk.section}",
                    f"lines={chunk.lines}",
                    "text:",
                    chunk.text,
                ]
            )
        )
    return "\n\n".join(blocks)


def _extract_chat_content(response: Any) -> str:
    choices = getattr(response, "choices", [])
    if not choices:
        raise ProviderError(
            "NVIDIA grounded generation returned no choices.",
            failure_class="invalid_response",
        )

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ProviderError(
            "NVIDIA grounded generation returned an empty response.",
            failure_class="invalid_response",
        )
    return content


def _parse_generation_draft(content: str) -> GeneratedPreflightDraft:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        payload = _extract_embedded_json_object(content)
        if payload is None:
            raise ProviderError(
                "NVIDIA grounded generation returned invalid JSON.",
                failure_class="invalid_response",
            ) from exc

    try:
        return GeneratedPreflightDraft.model_validate(payload)
    except ValidationError as exc:
        raise ProviderError(
            "NVIDIA grounded generation returned malformed JSON.",
            failure_class="invalid_response",
        ) from exc


def _extract_embedded_json_object(content: str) -> dict[str, Any] | None:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    candidate = content[start : end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
