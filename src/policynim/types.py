"""Shared typed models for PolicyNIM."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MIN_TOP_K = 1
MAX_TOP_K = 20


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
    top_k: int = Field(default=5, ge=MIN_TOP_K, le=MAX_TOP_K)


class SearchResult(StrictModel):
    """Search response shared by CLI and MCP."""

    query: str
    domain: str | None = None
    top_k: int
    hits: list[ScoredChunk] = Field(default_factory=list)
    insufficient_context: bool = False


class PolicyGuidance(StrictModel):
    """One applied policy with rationale and citations."""

    policy_id: str
    title: str
    rationale: str
    citation_ids: list[str] = Field(default_factory=list)


class PreflightRequest(StrictModel):
    """Preflight request shared by CLI and MCP."""

    task: str
    domain: str | None = None
    top_k: int = Field(default=5, ge=MIN_TOP_K, le=MAX_TOP_K)


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
