"""PolicyNIM exception types."""

from __future__ import annotations


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


class WeakEvidenceError(PolicyNIMError):
    """Raised when retrieval evidence is too weak to support synthesis."""


class NotImplementedYetError(PolicyNIMError):
    """Raised for Day 1 command surfaces that are intentionally scaffold-only."""
