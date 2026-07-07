"""PolicyNIM exception types."""

from __future__ import annotations

from time import perf_counter

from pydantic import ValidationError


class PolicyNIMError(Exception):
    """Base error for PolicyNIM."""

    def __init__(self, message: str = "", *, failure_class: str | None = None) -> None:
        super().__init__(message)
        self.failure_class = failure_class


class ConfigurationError(PolicyNIMError):
    """Raised when required configuration is missing or invalid."""


class ProviderError(PolicyNIMError):
    """Raised when an external provider call fails."""


class InvalidPolicyDocumentError(PolicyNIMError):
    """Raised when a policy document cannot be parsed or validated."""


class MissingIndexError(PolicyNIMError):
    """Raised when the local retrieval index is missing or empty."""


class RuntimeRulesArtifactMissingError(PolicyNIMError):
    """Raised when the compiled runtime-rules artifact is missing."""


class RuntimeRulesArtifactInvalidError(PolicyNIMError):
    """Raised when the compiled runtime-rules artifact cannot be trusted."""


class RuntimeCitationLinkError(PolicyNIMError):
    """Raised when matched runtime rules cannot be linked to indexed evidence."""


class RuntimeEvidencePersistenceError(PolicyNIMError):
    """Raised when runtime execution evidence cannot be persisted durably."""


def format_validation_error(
    label: str,
    exc: ValidationError,
    *,
    location_fallback: str = "request",
) -> str:
    """Render the first Pydantic validation error in the CLI/MCP house style."""
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or location_fallback
    return f"{label} is invalid at {location}: {error['msg']}."


def find_failure_class(exc: BaseException) -> str | None:
    """Walk chained exceptions until a populated failure class is found."""
    current: BaseException | None = exc
    while current is not None:
        failure_class = getattr(current, "failure_class", None)
        if isinstance(failure_class, str) and failure_class:
            return failure_class
        current = current.__cause__ or current.__context__
    return None


def elapsed_ms(start_time: float) -> float:
    """Return elapsed milliseconds rounded for structured output."""
    return round((perf_counter() - start_time) * 1000, 2)
