"""PolicyNIM exception types."""


class PolicyNIMError(Exception):
    """Base error for PolicyNIM."""


class ConfigurationError(PolicyNIMError):
    """Raised when required configuration is missing or invalid."""


class InvalidPolicyDocumentError(PolicyNIMError):
    """Raised when a policy document cannot be parsed or validated."""


class MissingIndexError(PolicyNIMError):
    """Raised when the local retrieval index is missing or empty."""


class WeakEvidenceError(PolicyNIMError):
    """Raised when retrieval evidence is too weak to support synthesis."""


class NotImplementedYetError(PolicyNIMError):
    """Raised for Day 1 command surfaces that are intentionally scaffold-only."""

