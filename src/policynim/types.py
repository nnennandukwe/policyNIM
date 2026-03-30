"""Shared typed models for PolicyNIM."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MIN_TOP_K = 1
MAX_TOP_K = 20
DEFAULT_TOP_K = 5
TopK = Annotated[int, Field(ge=MIN_TOP_K, le=MAX_TOP_K)]


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


class ParsedDocument(StrictModel):
    """Normalized source document returned by an ingest parser."""

    source_path: str
    format: str = "markdown"
    metadata: PolicyMetadata
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
    implementation_guidance: list[str] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    tests_required: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    insufficient_context: bool = False


EvalKind = Literal["search", "preflight"]
EvalExecutionMode = Literal["offline", "live"]


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


class EvalModeRunResult(StrictModel):
    """All eval results for one rerank mode."""

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
    suite_name: str
    suite_path: str
    workspace_path: str
    compare_rerank: bool = True
    runs: list[EvalModeRunResult] = Field(default_factory=list)
    comparison: EvalComparisonDelta | None = None
