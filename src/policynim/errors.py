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


class IndexCompatibilityError(MissingIndexError):
    """An index cannot be trusted with the selected embedding configuration."""

    def __init__(
        self, detail: str = "Index embedding identity is unknown or incompatible."
    ) -> None:
        super().__init__(
            detail + " Preserve existing sources, index, and runtime rules. Rebuild the complete "
            "corpus with `policynim ingest` into separate POLICYNIM_INDEX_DB_PATH and "
            "POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH destinations, then validate before activation.",
            failure_class="index_incompatible",
        )


class RuntimeRulesArtifactMissingError(PolicyNIMError):
    """Raised when the compiled runtime-rules artifact is missing."""


class RuntimeRulesArtifactInvalidError(PolicyNIMError):
    """Raised when the compiled runtime-rules artifact cannot be trusted."""


class RuntimeCitationLinkError(PolicyNIMError):
    """Raised when matched runtime rules cannot be linked to indexed evidence."""


class RuntimeEvidencePersistenceError(PolicyNIMError):
    """Raised when runtime execution evidence cannot be persisted durably."""
