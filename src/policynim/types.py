"""Shared typed models for PolicyNIM."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

MIN_TOP_K = 1
MAX_TOP_K = 20
DEFAULT_TOP_K = 5
TopK = Annotated[int, Field(ge=MIN_TOP_K, le=MAX_TOP_K)]
TaskType = Literal[
    "bug_fix",
    "refactor",
    "api_change",
    "migration",
    "test_change",
    "feature_work",
    "unknown",
]
RuntimeActionKind = Literal["shell_command", "file_write", "http_request"]
RuntimeRuleEffect = Literal["confirm", "block"]
RUNTIME_RULE_MATCHER_FIELDS = ("path_globs", "command_regexes", "url_host_patterns")


class StrictModel(BaseModel):
    """Base model for explicit API contracts."""

    model_config = ConfigDict(extra="forbid")


class PolicyMetadata(StrictModel):
    """Describes a normalized policy document."""

    policy_id: str
    title: str
    doc_type: str
    domain: str
    tags: list[str] = Field(default_factory=list)
    grounded_in: list[str] = Field(default_factory=list)


class PolicyChunk(StrictModel):
    """Represents a retrievable chunk of policy content."""

    chunk_id: str
    path: str
    section: str
    lines: str
    text: str
    policy: PolicyMetadata


class EmbeddedChunk(PolicyChunk):
    """One policy chunk paired with its embedding vector."""

    vector: list[float] = Field(default_factory=list)


class RuntimeRuleBase(StrictModel):
    """Shared matcher fields for parsed and compiled runtime rules."""

    action: RuntimeActionKind
    effect: RuntimeRuleEffect
    reason: str = Field(min_length=1)
    path_globs: list[str] = Field(default_factory=list)
    command_regexes: list[str] = Field(default_factory=list)
    url_host_patterns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_matcher_family(self) -> Self:
        """Require exactly one non-empty matcher family."""
        matcher_count = sum(
            1 for field_name in RUNTIME_RULE_MATCHER_FIELDS if getattr(self, field_name)
        )
        if matcher_count != 1:
            raise ValueError("runtime_rules must define exactly one non-empty matcher family.")
        return self


class ParsedRuntimeRule(RuntimeRuleBase):
    """One parsed runtime rule with source line metadata."""

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> Self:
        """Prevent impossible source line spans."""
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line.")
        return self


class CompiledRuntimeRule(RuntimeRuleBase):
    """One compiled runtime rule persisted for later enforcement."""

    policy_id: str
    title: str
    domain: str
    source_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> Self:
        """Prevent impossible source line spans."""
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line.")
        return self


class RuntimeRulesArtifact(StrictModel):
    """Deterministic runtime-rules snapshot produced during ingest."""

    schema_version: Literal[1] = 1
    rules: list[CompiledRuntimeRule] = Field(default_factory=list)


RuntimeDecision = Literal["allow", "confirm", "block"]
RuntimeExecutionOutcome = Literal["allowed", "confirmed", "blocked", "refused", "failed"]
RuntimeConfirmationOutcome = Literal["not_required", "confirmed", "refused", "unavailable"]
RuntimeEvidenceEventKind = Literal[
    "decision",
    "allowed",
    "confirmed",
    "blocked",
    "refused",
    "failed",
]


class RuntimeActionRequestBase(StrictModel):
    """Common request fields for runtime decision inputs."""

    task: str = Field(min_length=1)
    cwd: Path
    session_id: str | None = None
    agent_name: str | None = None
    repo_root: Path | None = None

    @field_validator("task", mode="before")
    @classmethod
    def validate_task(cls, value: object) -> object:
        """Reject empty task strings before later request normalization."""
        return _validate_non_empty_string(value, field_name="task")

    @field_validator("cwd", "repo_root", mode="before")
    @classmethod
    def validate_common_paths(cls, value: object, info: object) -> object:
        """Reject empty path-like request fields before Path coercion."""
        return _validate_non_empty_path(value, field_name=getattr(info, "field_name", "path"))

    @field_validator("session_id", "agent_name", mode="before")
    @classmethod
    def validate_optional_strings(cls, value: object, info: object) -> object:
        """Reject empty optional string values when provided."""
        if value is None:
            return None
        return _validate_non_empty_string(value, field_name=getattr(info, "field_name", "value"))


class ShellCommandActionRequest(RuntimeActionRequestBase):
    """Runtime decision input for one shell command."""

    kind: Literal["shell_command"]
    command: list[str] = Field(min_length=1)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        """Require at least one non-empty shell argument."""
        normalized: list[str] = []
        for item in value:
            normalized.append(_validate_non_empty_string(item, field_name="command item"))
        return normalized


class FileWriteActionRequest(RuntimeActionRequestBase):
    """Runtime decision input for one file write."""

    kind: Literal["file_write"]
    path: Path
    content: str

    @field_validator("path", mode="before")
    @classmethod
    def validate_path(cls, value: object) -> object:
        """Reject empty file-write paths before Path coercion."""
        return _validate_non_empty_path(value, field_name="path")


class HTTPRequestActionRequest(RuntimeActionRequestBase):
    """Runtime decision input for one HTTP request."""

    kind: Literal["http_request"]
    method: str = Field(min_length=1)
    url: AnyHttpUrl

    @field_validator("method", mode="before")
    @classmethod
    def validate_method(cls, value: object) -> str:
        """Normalize HTTP verbs while rejecting empty method values."""
        normalized = _validate_non_empty_string(value, field_name="method")
        return normalized.upper()


RuntimeActionRequest = Annotated[
    ShellCommandActionRequest | FileWriteActionRequest | HTTPRequestActionRequest,
    Field(discriminator="kind"),
]


class RuntimeDecisionResult(StrictModel):
    """Read-only runtime decision output with matched rule evidence."""

    request: RuntimeActionRequest
    decision: RuntimeDecision
    summary: str = Field(min_length=1)
    matched_rules: list[CompiledRuntimeRule] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class RuntimeExecutionRequestBase(RuntimeActionRequestBase):
    """Sanitized runtime execution request persisted in results and evidence."""


class ShellCommandExecutionRequest(RuntimeExecutionRequestBase):
    """Sanitized execution request for one shell command."""

    kind: Literal["shell_command"]
    command: list[str] = Field(min_length=1)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        """Require at least one non-empty shell argument."""
        normalized: list[str] = []
        for item in value:
            normalized.append(_validate_non_empty_string(item, field_name="command item"))
        return normalized


class FileWriteExecutionRequest(RuntimeExecutionRequestBase):
    """Sanitized execution request for one file write."""

    kind: Literal["file_write"]
    path: Path

    @field_validator("path", mode="before")
    @classmethod
    def validate_path(cls, value: object) -> object:
        """Reject empty file-write paths before Path coercion."""
        return _validate_non_empty_path(value, field_name="path")


class HTTPRequestExecutionRequest(RuntimeExecutionRequestBase):
    """Sanitized execution request for one HTTP request."""

    kind: Literal["http_request"]
    method: str = Field(min_length=1)
    url: AnyHttpUrl

    @field_validator("method", mode="before")
    @classmethod
    def validate_method(cls, value: object) -> str:
        """Normalize HTTP verbs while rejecting empty method values."""
        normalized = _validate_non_empty_string(value, field_name="method")
        return normalized.upper()


RuntimeExecutionRequest = Annotated[
    ShellCommandExecutionRequest | FileWriteExecutionRequest | HTTPRequestExecutionRequest,
    Field(discriminator="kind"),
]


class ShellCommandExecutionMetadata(StrictModel):
    """Safe shell-command execution metadata."""

    exit_code: int | None = None
    duration_ms: float = Field(ge=0.0)


class FileWriteExecutionMetadata(StrictModel):
    """Safe file-write execution metadata."""

    path: Path
    bytes_written: int = Field(ge=0)


class HTTPRequestExecutionMetadata(StrictModel):
    """Safe HTTP request execution metadata."""

    status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: float = Field(ge=0.0)


RuntimeExecutionMetadata = (
    ShellCommandExecutionMetadata | FileWriteExecutionMetadata | HTTPRequestExecutionMetadata
)


class RuntimeExecutionResult(StrictModel):
    """Top-level result for one runtime execution attempt."""

    execution_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    request: RuntimeExecutionRequest
    decision: RuntimeDecision
    summary: str = Field(min_length=1)
    matched_rules: list[CompiledRuntimeRule] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    confirmation_outcome: RuntimeConfirmationOutcome
    execution_outcome: RuntimeExecutionOutcome
    result_metadata: RuntimeExecutionMetadata | None = None
    failure_class: str | None = None
    residual_uncertainty: str | None = None


class RuntimeExecutionEvidenceRecord(StrictModel):
    """One immutable persisted runtime execution evidence event."""

    event_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    created_at: datetime
    event_kind: RuntimeEvidenceEventKind
    request: RuntimeExecutionRequest
    decision: RuntimeDecision
    summary: str = Field(min_length=1)
    matched_rules: list[CompiledRuntimeRule] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    confirmation_outcome: RuntimeConfirmationOutcome
    execution_outcome: RuntimeExecutionOutcome | None = None
    result_metadata: RuntimeExecutionMetadata | None = None
    failure_class: str | None = None
    residual_uncertainty: str | None = None


class RuntimeEvidenceExecutionSummary(StrictModel):
    """Compact summary for one execution inside a reported evidence session."""

    execution_id: str = Field(min_length=1)
    action_kind: RuntimeActionKind
    task: str = Field(min_length=1)
    decision: RuntimeDecision
    summary: str = Field(min_length=1)
    confirmation_outcome: RuntimeConfirmationOutcome
    execution_outcome: RuntimeExecutionOutcome | None = None
    failure_class: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    matched_rules: list[CompiledRuntimeRule] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_completion_range(self) -> Self:
        """Prevent impossible summary completion timestamps."""
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must be greater than or equal to started_at.")
        return self


class RuntimeEvidenceSessionSummary(StrictModel):
    """Session-level summary returned by the evidence report command."""

    session_id: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime | None = None
    event_count: int = Field(ge=0)
    execution_count: int = Field(ge=0)
    allowed_count: int = Field(default=0, ge=0)
    confirmed_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    refused_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    incomplete_count: int = Field(default=0, ge=0)
    executions: list[RuntimeEvidenceExecutionSummary] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_summary_counts(self) -> Self:
        """Keep aggregated counts aligned with the execution list."""
        if self.execution_count != len(self.executions):
            raise ValueError("execution_count must match the number of execution summaries.")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must be greater than or equal to started_at.")
        return self


class ParsedDocument(StrictModel):
    """Normalized source document returned by an ingest parser."""

    source_path: str
    format: str = "markdown"
    metadata: PolicyMetadata
    runtime_rules: list[ParsedRuntimeRule] = Field(default_factory=list)
    body: str
    body_start_line: int = Field(default=1, ge=1)


class DocumentSection(StrictModel):
    """One extracted document section with source line metadata."""

    heading_path: list[str] = Field(default_factory=list)
    content: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> Self:
        """Prevent impossible source line spans."""
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line.")
        return self


class ScoredChunk(PolicyChunk):
    """Represents a retrieved chunk with an attached score."""

    score: float | None = None


class Citation(StrictModel):
    """A source citation exposed to users and coding agents."""

    policy_id: str
    title: str
    path: str
    section: str
    lines: str
    chunk_id: str


class SearchRequest(StrictModel):
    """Search request shared by CLI and MCP."""

    query: str
    domain: str | None = None
    top_k: TopK = DEFAULT_TOP_K


class SearchResult(StrictModel):
    """Search response shared by CLI and MCP."""

    query: str
    domain: str | None = None
    top_k: int
    hits: list[ScoredChunk] = Field(default_factory=list)
    insufficient_context: bool = False


class RouteRequest(StrictModel):
    """Task-aware policy-routing request shared by CLI and services."""

    task: str
    domain: str | None = None
    top_k: TopK = DEFAULT_TOP_K
    task_type: TaskType | None = None

    @field_validator("task", mode="before")
    @classmethod
    def validate_task(cls, value: object) -> str:
        """Reject empty routing tasks before profile inference."""
        return _validate_non_empty_string(value, field_name="task")

    @field_validator("domain", mode="before")
    @classmethod
    def validate_domain(cls, value: object) -> str | None:
        """Reject empty domain filters while preserving omitted filters."""
        if value is None:
            return None
        return _validate_non_empty_string(value, field_name="domain")


class TaskProfile(StrictModel):
    """Deterministic profile inferred from a coding task."""

    task: str
    task_type: TaskType
    explicit_task_type: TaskType | None = None
    signals: list[str] = Field(default_factory=list)


class SelectedPolicyEvidence(StrictModel):
    """One selected chunk that supports a routed policy."""

    chunk_id: str
    path: str
    section: str
    lines: str
    text: str
    score: float | None = None


class SelectedPolicy(StrictModel):
    """One policy selected for the task-aware packet."""

    policy_id: str
    title: str
    domain: str
    reason: str
    evidence: list[SelectedPolicyEvidence] = Field(default_factory=list)


class PolicySelectionPacket(StrictModel):
    """Inspection-friendly packet emitted by task-aware policy routing."""

    task: str
    domain: str | None = None
    top_k: int
    task_type: TaskType
    explicit_task_type: TaskType | None = None
    profile_signals: list[str] = Field(default_factory=list)
    selected_policies: list[SelectedPolicy] = Field(default_factory=list)
    insufficient_context: bool = False


class RouteResult(StrictModel):
    """Internal route result with packet JSON and generator-ready context."""

    packet: PolicySelectionPacket
    retained_context: list[ScoredChunk] = Field(default_factory=list)


class CompileRequest(StrictModel):
    """Policy-compiler request shared by CLI and services."""

    task: str
    domain: str | None = None
    top_k: TopK = DEFAULT_TOP_K
    task_type: TaskType | None = None

    @field_validator("task", mode="before")
    @classmethod
    def validate_task(cls, value: object) -> str:
        """Reject empty compiler tasks before routing."""
        return _validate_non_empty_string(value, field_name="task")

    @field_validator("domain", mode="before")
    @classmethod
    def validate_domain(cls, value: object) -> str | None:
        """Reject empty domain filters while preserving omitted filters."""
        if value is None:
            return None
        return _validate_non_empty_string(value, field_name="domain")


class GeneratedPolicyConstraint(StrictModel):
    """One untrusted model-generated policy constraint before local grounding."""

    statement: str
    citation_ids: list[str] = Field(default_factory=list)


class GeneratedCompiledPolicyDraft(StrictModel):
    """Untrusted compiler draft returned by the NVIDIA policy compiler."""

    required_steps: list[GeneratedPolicyConstraint] = Field(default_factory=list)
    forbidden_patterns: list[GeneratedPolicyConstraint] = Field(default_factory=list)
    architectural_expectations: list[GeneratedPolicyConstraint] = Field(default_factory=list)
    test_expectations: list[GeneratedPolicyConstraint] = Field(default_factory=list)
    style_constraints: list[GeneratedPolicyConstraint] = Field(default_factory=list)
    insufficient_context: bool = False


class CompiledPolicyConstraint(StrictModel):
    """One locally validated policy constraint with grounded source policy IDs."""

    statement: str = Field(min_length=1)
    citation_ids: list[str] = Field(min_length=1)
    source_policy_ids: list[str] = Field(min_length=1)


class CompiledPolicyPacket(StrictModel):
    """Citation-backed policy constraints for planning and generation."""

    task: str
    domain: str | None = None
    top_k: int
    task_type: TaskType
    explicit_task_type: TaskType | None = None
    profile_signals: list[str] = Field(default_factory=list)
    selected_policies: list[SelectedPolicy] = Field(default_factory=list)
    required_steps: list[CompiledPolicyConstraint] = Field(default_factory=list)
    forbidden_patterns: list[CompiledPolicyConstraint] = Field(default_factory=list)
    architectural_expectations: list[CompiledPolicyConstraint] = Field(default_factory=list)
    test_expectations: list[CompiledPolicyConstraint] = Field(default_factory=list)
    style_constraints: list[CompiledPolicyConstraint] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    insufficient_context: bool = False


class CompileResult(StrictModel):
    """Internal compile result with packet JSON and generator-ready context."""

    packet: CompiledPolicyPacket
    retained_context: list[ScoredChunk] = Field(default_factory=list)


class IngestResult(StrictModel):
    """Summary of one completed ingest run."""

    corpus_path: str
    index_uri: str
    table_name: str
    embedding_model: str
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    embedding_dimension: int = Field(ge=1)


class HealthCheckResult(StrictModel):
    """Hosted runtime health payload for HTTP readiness checks."""

    status: Literal["ok", "error"]
    ready: bool
    table_name: str
    row_count: int = Field(ge=0)
    mcp_url: str | None = None
    reason: str | None = None


BetaAccountStatus = Literal["active", "suspended"]


class BetaAccount(StrictModel):
    """One self-serve hosted beta account."""

    account_id: int = Field(ge=1)
    github_user_id: int = Field(ge=1)
    github_login: str = Field(min_length=1)
    email: str | None = None
    status: BetaAccountStatus
    created_at: datetime
    last_login_at: datetime
    api_key_prefix: str | None = None
    api_key_created_at: datetime | None = None


class BetaUsageSnapshot(StrictModel):
    """Current daily hosted-usage state for one beta account."""

    usage_date: date
    request_count: int = Field(ge=0)
    quota: int = Field(ge=1)
    remaining: int = Field(ge=0)


class BetaIssuedApiKey(StrictModel):
    """One newly issued hosted beta API key."""

    account: BetaAccount
    api_key: str = Field(min_length=1)
    usage: BetaUsageSnapshot


class BetaAuthDecision(StrictModel):
    """Result of authenticating one hosted MCP HTTP request."""

    status: Literal["authorized", "unauthorized", "suspended", "quota_exceeded"]
    source: Literal["api_key", "break_glass"] | None = None
    account: BetaAccount | None = None
    usage: BetaUsageSnapshot | None = None


def _validate_non_empty_string(value: object, *, field_name: str) -> str:
    """Return one trimmed required string and reject empty values."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    return normalized


def _validate_non_empty_path(value: object, *, field_name: str) -> object:
    """Reject empty string path inputs without changing valid Path values."""
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field_name} must not be empty.")
    return value


class PolicyGuidance(StrictModel):
    """One applied policy with rationale and citations."""

    policy_id: str
    title: str
    rationale: str
    citation_ids: list[str] = Field(default_factory=list)


class GeneratedPolicyGuidance(StrictModel):
    """Internal guidance payload returned by the generator before citation mapping."""

    policy_id: str
    title: str
    rationale: str
    citation_ids: list[str] = Field(default_factory=list)


class PreflightRequest(StrictModel):
    """Preflight request shared by CLI and MCP."""

    task: str
    domain: str | None = None
    top_k: TopK = DEFAULT_TOP_K


class GeneratedPreflightDraft(StrictModel):
    """Internal generated payload before citations are materialized."""

    summary: str
    applicable_policies: list[GeneratedPolicyGuidance] = Field(default_factory=list)
    plan_steps: list[str] = Field(default_factory=list)
    implementation_guidance: list[str] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    tests_required: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    insufficient_context: bool = False


class PreflightResult(StrictModel):
    """Preflight response shared by CLI and MCP."""

    task: str
    domain: str | None = None
    summary: str
    applicable_policies: list[PolicyGuidance] = Field(default_factory=list)
    plan_steps: list[str] = Field(default_factory=list)
    implementation_guidance: list[str] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    tests_required: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    insufficient_context: bool = False


EvalBackend = Literal["default", "nemo"]
EvalKind = Literal["search", "preflight"]
EvalExecutionMode = Literal["offline", "live"]


class PolicyConformanceMetric(StrictModel):
    """One policy-conformance metric for a preflight result."""

    name: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    failure_reasons: list[str] = Field(default_factory=list)


class PolicyConformanceTraceStep(StrictModel):
    """One optional intermediate step available for trajectory-aware judging."""

    step_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)


class GeneratedPolicyConformanceDraft(StrictModel):
    """Untrusted conformance judgment returned by an external evaluator."""

    final_adherence_score: float = Field(ge=0.0, le=1.0)
    final_adherence_rationale: str = ""
    trajectory_adherence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    trajectory_adherence_rationale: str | None = None
    constraint_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)


class PolicyConformanceResult(StrictModel):
    """Typed policy-conformance result for one preflight eval case."""

    backend: EvalBackend
    passed: bool
    overall_score: float = Field(ge=0.0, le=1.0)
    metrics: list[PolicyConformanceMetric] = Field(default_factory=list)
    final_adherence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    final_adherence_rationale: str | None = None
    trajectory_adherence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    trajectory_adherence_rationale: str | None = None
    constraint_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)


class PolicyConformanceRequest(StrictModel):
    """Conformance request assembled from a preflight trace."""

    task: str
    result: PreflightResult
    compiled_packet: CompiledPolicyPacket
    trace_steps: list[PolicyConformanceTraceStep] = Field(default_factory=list)


class PreflightTraceResult(StrictModel):
    """Internal preflight result plus trace data for eval scoring."""

    result: PreflightResult
    compiled_packet: CompiledPolicyPacket
    retained_context: list[ScoredChunk] = Field(default_factory=list)
    trace_steps: list[PolicyConformanceTraceStep] = Field(default_factory=list)


PolicyEvidenceTraceConstraintCategory = Literal[
    "required_steps",
    "forbidden_patterns",
    "architectural_expectations",
    "test_expectations",
    "style_constraints",
]
PolicyEvidenceTraceOutputField = Literal[
    "plan_steps",
    "implementation_guidance",
    "review_flags",
    "tests_required",
    "citations",
]


class PolicyEvidenceTraceChunk(StrictModel):
    """One retained policy chunk available to the traced preflight run."""

    chunk_id: str
    policy_id: str
    policy_title: str
    domain: str
    path: str
    section: str
    lines: str
    text: str | None = None
    score: float | None = None


class PolicyEvidenceTracePolicy(StrictModel):
    """One selected policy and the chunk IDs supporting its selection."""

    policy_id: str
    title: str
    reason: str
    supporting_chunk_ids: list[str] = Field(default_factory=list)


class PolicyEvidenceTraceConstraint(StrictModel):
    """One compiled constraint linked to policy and chunk evidence."""

    constraint_id: str
    category: PolicyEvidenceTraceConstraintCategory
    statement: str
    citation_ids: list[str] = Field(default_factory=list)
    source_policy_ids: list[str] = Field(default_factory=list)


class PolicyEvidenceTraceOutputLink(StrictModel):
    """One generated output field linked back to constraints and chunks."""

    field: PolicyEvidenceTraceOutputField
    index: int = Field(ge=0)
    text: str
    constraint_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)


class PolicyEvidenceTraceConformanceCheck(StrictModel):
    """One conformance check linked back to judged constraints and chunks."""

    backend: EvalBackend
    name: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    failure_reasons: list[str] = Field(default_factory=list)
    constraint_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)


class PolicyEvidenceTrace(StrictModel):
    """Replay-free evidence trace for one policy-conditioned preflight run."""

    task: str
    domain: str | None = None
    top_k: int
    task_type: TaskType
    explicit_task_type: TaskType | None = None
    profile_signals: list[str] = Field(default_factory=list)
    insufficient_context: bool = False
    compiled_insufficient_context: bool = False
    chunks: list[PolicyEvidenceTraceChunk] = Field(default_factory=list)
    selected_policies: list[PolicyEvidenceTracePolicy] = Field(default_factory=list)
    constraints: list[PolicyEvidenceTraceConstraint] = Field(default_factory=list)
    output_links: list[PolicyEvidenceTraceOutputLink] = Field(default_factory=list)
    trace_steps: list[PolicyConformanceTraceStep] = Field(default_factory=list)
    conformance_checks: list[PolicyEvidenceTraceConformanceCheck] = Field(default_factory=list)


class PreflightEvidenceTraceResult(StrictModel):
    """CLI result containing public preflight output and evidence trace."""

    result: PreflightResult
    evidence_trace: PolicyEvidenceTrace


class EvalCase(StrictModel):
    """One gold eval case for search or grounded preflight."""

    case_id: str
    kind: EvalKind
    input: str
    domain: str | None = None
    top_k: TopK = DEFAULT_TOP_K
    expected_insufficient_context: bool = False
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_policy_ids: list[str] = Field(default_factory=list)


class EvalSuite(StrictModel):
    """Named bundle of eval cases."""

    name: str
    cases: list[EvalCase] = Field(default_factory=list)


class EvalCaseMetrics(StrictModel):
    """Per-case scoring metrics."""

    expected_chunk_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_policy_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    insufficient_context_correct: bool = False
    conformance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    conformance_passed: bool | None = None


class EvalCaseResult(StrictModel):
    """Scored result for one eval case under a specific rerank mode."""

    case_id: str
    kind: EvalKind
    input: str
    domain: str | None = None
    top_k: int
    rerank_enabled: bool
    passed: bool
    failure_reasons: list[str] = Field(default_factory=list)
    expected_insufficient_context: bool
    actual_insufficient_context: bool
    expected_chunk_ids: list[str] = Field(default_factory=list)
    actual_chunk_ids: list[str] = Field(default_factory=list)
    matched_chunk_ids: list[str] = Field(default_factory=list)
    expected_policy_ids: list[str] = Field(default_factory=list)
    actual_policy_ids: list[str] = Field(default_factory=list)
    matched_policy_ids: list[str] = Field(default_factory=list)
    actual_summary: str | None = None
    conformance_result: PolicyConformanceResult | None = None
    evidence_trace: PolicyEvidenceTrace | None = None
    metrics: EvalCaseMetrics


class EvalAggregateMetrics(StrictModel):
    """Aggregate metrics for one rerank mode run."""

    case_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    search_case_count: int = Field(ge=0)
    search_passed_count: int = Field(ge=0)
    preflight_case_count: int = Field(ge=0)
    preflight_passed_count: int = Field(ge=0)
    overall_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    search_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    preflight_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_chunk_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_policy_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    insufficient_context_accuracy: float = Field(default=0.0, ge=0.0, le=1.0)
    conformance_case_count: int = Field(default=0, ge=0)
    conformance_passed_count: int = Field(default=0, ge=0)
    conformance_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    conformance_score: float = Field(default=0.0, ge=0.0, le=1.0)


class EvalModeRunResult(StrictModel):
    """All eval results for one rerank mode."""

    backend: EvalBackend = "default"
    rerank_enabled: bool
    metrics: EvalAggregateMetrics
    result_json_path: str
    report_html_path: str
    case_results: list[EvalCaseResult] = Field(default_factory=list)


class EvalComparisonDelta(StrictModel):
    """Comparison summary between rerank on and off runs."""

    overall_pass_rate_delta: float = 0.0
    expected_chunk_recall_delta: float = 0.0
    expected_policy_recall_delta: float = 0.0
    insufficient_context_accuracy_delta: float = 0.0
    improved_case_ids: list[str] = Field(default_factory=list)
    regressed_case_ids: list[str] = Field(default_factory=list)
    unchanged_case_ids: list[str] = Field(default_factory=list)


class EvalRunResult(StrictModel):
    """Top-level eval command result."""

    mode: EvalExecutionMode
    backend: EvalBackend = "default"
    suite_name: str
    suite_path: str
    workspace_path: str
    compare_rerank: bool = True
    runs: list[EvalModeRunResult] = Field(default_factory=list)
    comparison: EvalComparisonDelta | None = None
