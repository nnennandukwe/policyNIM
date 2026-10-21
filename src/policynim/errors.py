"""PolicyNIM exception types."""

from __future__ import annotations


class PolicyNIMError(Exception):
    """Base error for PolicyNIM."""

    def __init__(self, message: str = "", *, failure_class: str | None = None) -> None:
        super().__init__(message)
        self.failure_class = failure_class


class IndexReadLimitError(PolicyNIMError):
    """Refuse a bounded row read before materializing an oversized payload."""

    def __init__(self) -> None:
        """Keep the storage failure independent of caller-specific recovery policy."""
        super().__init__(
            "Selected index rows exceed the read budget.", failure_class="index_read_limit"
        )


class PolicyContextError(PolicyNIMError):
    """Refuse incomplete or unbound selected-policy context before generation."""

    def __init__(self, *, identity_mismatch: bool = False, output_budget: bool = False) -> None:
        """Expose fixed recovery guidance without including source content or paths."""
        if output_budget:
            message = (
                "Full-source output exceeds the 1-MiB serialized result budget. "
                "No partial output was returned. Narrow the task, top_k or domain "
                "before retrying."
            )
            failure_class = "policy_output_budget_exceeded"
        elif identity_mismatch:
            message = (
                "Selected policy evidence doesn't match the pinned index. "
                "Preserve the generation and ask the operator to inspect its integrity."
            )
            failure_class = "policy_context_identity_mismatch"
        else:
            message = (
                "Complete selected-policy context exceeds the 80-chunk or 256-KiB budget. "
                "No partial policy context was used. Narrow top_k or domain, or ask the "
                "operator to split reviewed policy documents before retrying."
            )
            failure_class = "policy_context_budget_exceeded"
        super().__init__(message, failure_class=failure_class)


class GenerationError(PolicyNIMError):
    """A bounded generation failure with a phase and source-preserving recovery action."""

    def __init__(self, phase: str, detail: str, *, recovery: str | None = None) -> None:
        """Preserve a safe phase and recovery action without echoing artifact contents."""
        self.phase = phase
        action = recovery or (
            "Preserve existing data and run `policynim generation inspect` before retrying."
        )
        super().__init__(
            f"Generation {phase} failed: {detail} {action}",
            failure_class=f"generation_{phase}",
        )


class MissingGenerationError(GenerationError):
    """The selected generation object is absent; existing object corruption stays separate."""


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
        """Attach source-preserving recovery instructions without stored index details."""
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
