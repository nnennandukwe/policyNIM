"""Durable evaluation-job input, progress and retained-report contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from policynim.errors import PolicyNIMError
from policynim.types import EvalCaseResult, EvalSuite, GenerationID, StrictModel

EvaluationJobState = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "deadline_exceeded",
    "budget_exhausted",
    "interrupted",
]
TERMINAL_JOB_STATES = frozenset(
    {"completed", "failed", "cancelled", "deadline_exceeded", "budget_exhausted", "interrupted"}
)


class EvaluationJobError(PolicyNIMError):
    """Expose a bounded application-job failure without another caller's evidence."""

    def __init__(self, message: str) -> None:
        """Keep a stable state code separate from the caller-facing recovery guidance."""
        self.code = message.partition(":")[0]
        super().__init__(message, failure_class=self.code)


class EvaluationJobLimits(StrictModel):
    """Bound accepted work and the time its results remain available."""

    deadline_seconds: float = Field(default=3600, gt=0, le=86400, allow_inf_nan=False)
    max_provider_calls: int = Field(default=512, ge=1, le=10000)
    max_cases: int = Field(default=100, ge=1, le=1000)
    retention_seconds: float = Field(default=604800, ge=60, le=2592000, allow_inf_nan=False)


class EvaluationJobAuthorization(StrictModel):
    """Retain accepted identity and scopes without a bearer or provider credential."""

    issuer: str
    subject: str
    client_id: str
    scopes: list[str]
    source: Literal["oauth", "beta_key", "operator_key"]
    expires_at: int | None = None
    credential_id: str | None = None


class EvaluationJobConfiguration(StrictModel):
    """Capture reproducible provider identity and execution limits without secrets."""

    provider: Literal["nvidia"] = "nvidia"
    nvidia_chat_model: str
    nvidia_embed_model: str
    nvidia_rerank_model: str
    nvidia_base_url: str
    nvidia_retrieval_base_url: str
    nvidia_timeout_seconds: float
    nvidia_max_retries: int
    nvidia_retry_initial_seconds: float
    nvidia_retry_max_seconds: float
    operation_timeout_seconds: float
    rerank_enabled: bool = True


class EvaluationJobSnapshot(StrictModel):
    """The immutable accepted inputs, including the full captured authored suite."""

    schema_version: Literal[1] = 1
    generation_id: GenerationID
    generation_archive_digest: str
    suite_digest: str
    configuration_digest: str
    suite: EvalSuite
    configuration: EvaluationJobConfiguration
    authorization: EvaluationJobAuthorization
    limits: EvaluationJobLimits


class EvaluationJobUsage(StrictModel):
    """Separate recorded attempt reservations from measured and unavailable usage."""

    provider_attempts: int = Field(default=0, ge=0)
    measured_tokens: int = Field(default=0, ge=0)
    unavailable_usage_attempts: int = Field(default=0, ge=0)
    usage_status: Literal["not_performed", "measured", "partially_measured", "unavailable"] = (
        "not_performed"
    )


class EvaluationJobInfo(StrictModel):
    """Inspectable progress which never substitutes the currently active generation."""

    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    owner_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: EvaluationJobState = "queued"
    accepted_at: float
    started_at: float | None = None
    finished_at: float | None = None
    deadline_at: float
    expires_at: float
    generation_id: GenerationID
    suite_digest: str
    configuration_digest: str
    snapshot_digest: str
    case_count: int = Field(ge=1)
    completed_case_count: int = Field(default=0, ge=0)
    active_case_id: str | None = None
    cancel_requested: bool = False
    failure_code: str | None = None
    report_sha256: str | None = None
    result_expired: bool = False


class EvaluationJobReport(StrictModel):
    """One terminal record with exact inputs, partial outcomes and artifact identities."""

    schema_version: Literal[1] = 1
    job_id: str
    state: EvaluationJobState
    snapshot: EvaluationJobSnapshot
    snapshot_digest: str
    completed_cases: list[EvalCaseResult] = Field(default_factory=list)
    unfinished_case_ids: list[str] = Field(default_factory=list)
    uncertain_case_id: str | None = None
    failure_code: str | None = None
    usage: EvaluationJobUsage
    case_artifact_hashes: dict[str, str] = Field(default_factory=dict)


class StoredEvaluationJobReport(StrictModel):
    """Pair immutable report content with its canonical JSON digest."""

    report: EvaluationJobReport
    sha256: str


class EvaluationJobReportManifest(StrictModel):
    """Bounded MCP report discovery with verified logical part links."""

    job_id: str
    state: EvaluationJobState
    generation_id: GenerationID
    suite_digest: str
    configuration_digest: str
    snapshot_digest: str
    report_sha256: str
    completed_case_ids: list[str]
    unfinished_case_ids: list[str]
    usage: EvaluationJobUsage
    expires_at: float
    part_uris: list[str]


class EvaluationJobReportPart(StrictModel):
    """One bounded ASCII JSON fragment; concatenate parts to recover the hashed report JSON."""

    job_id: str
    part: int = Field(ge=0)
    part_count: int = Field(ge=1)
    report_sha256: str
    part_sha256: str
    content: str
