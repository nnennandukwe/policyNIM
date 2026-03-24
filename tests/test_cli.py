"""Tests for the Day 3 CLI surface."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from policynim.errors import ConfigurationError, MissingIndexError, PolicyNIMError
from policynim.interfaces.cli import app
from policynim.types import (
    Citation,
    EvalAggregateMetrics,
    EvalCaseMetrics,
    EvalCaseResult,
    EvalComparisonDelta,
    EvalModeRunResult,
    EvalRunResult,
    IngestResult,
    PolicyChunk,
    PolicyGuidance,
    PolicyMetadata,
    PreflightRequest,
    PreflightResult,
    ScoredChunk,
    SearchRequest,
    SearchResult,
)

runner = CliRunner()


class MockIngestService:
    """Static ingest service for CLI tests."""

    def run(self) -> IngestResult:
        return IngestResult(
            corpus_path="policies",
            index_uri="data/lancedb",
            table_name="policy_chunks",
            embedding_model="mock-model",
            document_count=8,
            chunk_count=24,
            embedding_dimension=2,
        )


class MockSearchService:
    """Static search service for CLI tests."""

    def search(self, request: SearchRequest) -> SearchResult:
        return SearchResult(
            query=request.query,
            domain=request.domain,
            top_k=request.top_k,
            hits=[
                ScoredChunk(
                    chunk_id="BACKEND-1",
                    path="policies/backend/logging.md",
                    section="Logging > Rules",
                    lines="5-8",
                    text="Use request ids in backend logs.",
                    policy=PolicyMetadata(
                        policy_id="BACKEND-LOG-001",
                        title="Logging",
                        doc_type="guidance",
                        domain="backend",
                    ),
                    score=0.99,
                )
            ],
        )


class MockPreflightService:
    """Static preflight service for CLI tests."""

    def __init__(self) -> None:
        self.closed = False

    def preflight(self, request: PreflightRequest) -> PreflightResult:
        return PreflightResult(
            task=request.task,
            domain=request.domain,
            summary="Follow background-job and auth cleanup policies.",
            applicable_policies=[
                PolicyGuidance(
                    policy_id="AUTH-001",
                    title="Auth Reviews",
                    rationale="Cleanup logic must preserve revocation and auditing behavior.",
                    citation_ids=["AUTH-1"],
                )
            ],
            implementation_guidance=[
                "Retain revocation checks before deleting stale refresh tokens."
            ],
            review_flags=["Ensure cleanup jobs redact token values from logs."],
            tests_required=[
                "Add a test that expired tokens are deleted without removing active ones."
            ],
            citations=[
                Citation(
                    policy_id="AUTH-001",
                    title="Auth Reviews",
                    path="policies/security/auth-review.md",
                    section="Cleanup",
                    lines="10-16",
                    chunk_id="AUTH-1",
                )
            ],
            insufficient_context=False,
        )

    def close(self) -> None:
        self.closed = True


class MockIndexDumpService:
    """Static dump service for CLI tests."""

    def list_chunks(self) -> list[PolicyChunk]:
        return [
            PolicyChunk(
                chunk_id="BACKEND-1",
                path="policies/backend/logging.md",
                section="Logging > Rules",
                lines="5-8",
                text="Use request ids in backend logs.",
                policy=PolicyMetadata(
                    policy_id="BACKEND-LOG-001",
                    title="Logging",
                    doc_type="guidance",
                    domain="backend",
                ),
            )
        ]


class MockEvalService:
    """Static eval service for CLI tests."""

    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed
        self.launch_port: int | None = None
        self.started_ui = False

    def run(self, *, mode, compare_rerank) -> EvalRunResult:
        passed_count = 2 if self.passed else 1
        return EvalRunResult(
            mode=mode,
            suite_name="day-6-default",
            suite_path="evals/default_cases.json",
            workspace_path="data/evals/workspace",
            compare_rerank=compare_rerank,
            runs=[
                EvalModeRunResult(
                    rerank_enabled=True,
                    metrics=EvalAggregateMetrics(
                        case_count=2,
                        passed_count=passed_count,
                        search_case_count=1,
                        search_passed_count=1,
                        preflight_case_count=1,
                        preflight_passed_count=passed_count - 1,
                        overall_pass_rate=passed_count / 2,
                        search_pass_rate=1.0,
                        preflight_pass_rate=(passed_count - 1) / 1,
                        expected_chunk_recall=1.0,
                        expected_policy_recall=1.0 if self.passed else 0.5,
                        insufficient_context_accuracy=1.0,
                    ),
                    result_json_path="data/evals/workspace/results/run.json",
                    report_html_path="data/evals/workspace/reports/run.html",
                    case_results=[
                        EvalCaseResult(
                            case_id="search-case",
                            kind="search",
                            input="backend logs",
                            domain=None,
                            top_k=1,
                            rerank_enabled=True,
                            passed=True,
                            failure_reasons=[],
                            expected_insufficient_context=False,
                            actual_insufficient_context=False,
                            expected_chunk_ids=["BACKEND-1"],
                            actual_chunk_ids=["BACKEND-1"],
                            matched_chunk_ids=["BACKEND-1"],
                            expected_policy_ids=[],
                            actual_policy_ids=[],
                            matched_policy_ids=[],
                            metrics=EvalCaseMetrics(
                                expected_chunk_recall=1.0,
                                expected_policy_recall=1.0,
                                insufficient_context_correct=True,
                            ),
                        ),
                        EvalCaseResult(
                            case_id="preflight-case",
                            kind="preflight",
                            input="refresh token cleanup",
                            domain=None,
                            top_k=1,
                            rerank_enabled=True,
                            passed=self.passed,
                            failure_reasons=(
                                [] if self.passed else ["missing expected policy_id values: AUTH-1"]
                            ),
                            expected_insufficient_context=False,
                            actual_insufficient_context=False,
                            expected_chunk_ids=[],
                            actual_chunk_ids=["AUTH-1"],
                            matched_chunk_ids=[],
                            expected_policy_ids=["AUTH-1"],
                            actual_policy_ids=["AUTH-1"] if self.passed else [],
                            matched_policy_ids=["AUTH-1"] if self.passed else [],
                            metrics=EvalCaseMetrics(
                                expected_chunk_recall=1.0,
                                expected_policy_recall=1.0 if self.passed else 0.0,
                                insufficient_context_correct=True,
                            ),
                        ),
                    ],
                )
            ],
            comparison=EvalComparisonDelta(
                overall_pass_rate_delta=0.5,
                expected_chunk_recall_delta=0.5,
                expected_policy_recall_delta=0.5,
                insufficient_context_accuracy_delta=0.0,
                improved_case_ids=["preflight-case"],
                regressed_case_ids=[],
                unchanged_case_ids=["search-case"],
            ),
        )

    def start_ui(self, *, port: int | None = None) -> None:
        self.launch_port = port
        self.started_ui = True


def test_ingest_command_prints_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_ingest_service",
        lambda settings: MockIngestService(),
    )

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 0
    assert "Indexed 24 chunks from 8 documents." in result.stdout
    assert "mock-model" in result.stdout


def test_ingest_command_surfaces_value_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_ingest_service",
        lambda settings: (_ for _ in ()).throw(ValueError("chunk/vector mismatch")),
    )

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 1
    assert "chunk/vector mismatch" in result.stderr


def test_search_command_prints_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_search_service",
        lambda settings: MockSearchService(),
    )

    result = runner.invoke(app, ["search", "--query", "backend logs", "--top-k", "3"])

    assert result.exit_code == 0
    payload = SearchResult.model_validate(json.loads(result.stdout))
    assert payload.query == "backend logs"
    assert payload.top_k == 3
    assert payload.hits[0].chunk_id == "BACKEND-1"


def test_eval_command_prints_json(monkeypatch) -> None:
    service = MockEvalService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["eval", "--mode", "offline", "--headless", "--no-compare-rerank"])

    assert result.exit_code == 0
    payload = EvalRunResult.model_validate(json.loads(result.stdout))
    assert payload.mode == "offline"
    assert payload.runs[0].metrics.case_count == 2
    assert "--cases" not in runner.invoke(app, ["eval", "--help"]).stdout


def test_eval_command_starts_ui_by_default(monkeypatch) -> None:
    service = MockEvalService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["eval"])

    assert result.exit_code == 0
    assert service.started_ui is True


def test_eval_command_surfaces_ui_startup_failures(monkeypatch) -> None:
    error_cls = PolicyNIMError

    class FailingEvalService(MockEvalService):
        def start_ui(self, *, port: int | None = None) -> None:
            raise error_cls("Evidently UI exited before startup completed.")

    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        lambda settings: FailingEvalService(),
    )

    result = runner.invoke(app, ["eval"])

    assert result.exit_code == 1
    assert "Evidently UI exited before startup completed." in result.stderr


def test_eval_command_returns_non_zero_when_cases_fail(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        lambda settings: MockEvalService(passed=False),
    )

    result = runner.invoke(app, ["eval", "--headless"])

    assert result.exit_code == 1
    payload = EvalRunResult.model_validate(json.loads(result.stdout))
    assert payload.runs[0].metrics.passed_count == 1


def test_eval_command_skips_rerank_comparison_when_requested(monkeypatch) -> None:
    service = MockEvalService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["eval", "--headless", "--no-compare-rerank"])

    assert result.exit_code == 0
    payload = EvalRunResult.model_validate(json.loads(result.stdout))
    assert payload.compare_rerank is False


def test_preflight_command_prints_json(monkeypatch) -> None:
    service = MockPreflightService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: service,
    )

    result = runner.invoke(
        app,
        ["preflight", "--task", "refresh token cleanup", "--domain", "security", "--top-k", "3"],
    )

    assert result.exit_code == 0
    payload = PreflightResult.model_validate(json.loads(result.stdout))
    assert payload.task == "refresh token cleanup"
    assert payload.domain == "security"
    assert payload.citations[0].chunk_id == "AUTH-1"
    assert service.closed is True


def test_dump_index_command_prints_chunks(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_index_dump_service",
        lambda settings: MockIndexDumpService(),
    )

    result = runner.invoke(app, ["dump-index"])

    assert result.exit_code == 0
    assert "Indexed chunks: 1" in result.stdout
    assert "BACKEND-1" in result.stdout
    assert "Use request ids in backend logs." in result.stdout


def test_help_includes_dump_index_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "dump-index" in result.stdout


def test_dump_index_help_mentions_less_for_paging() -> None:
    result = runner.invoke(app, ["dump-index", "--help"])

    assert result.exit_code == 0
    assert "add ` | less`" in result.stdout
    assert "paging large output" in result.stdout


def test_preflight_help_mentions_current_top_k_behavior() -> None:
    result = runner.invoke(app, ["preflight", "--help"])

    assert result.exit_code == 0
    assert "Retrieval depth." in result.stdout
    assert "1-20." in result.stdout
    assert "Reserved retrieval depth" not in result.stdout


def test_search_command_surfaces_configuration_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_search_service",
        lambda settings: (_ for _ in ()).throw(ConfigurationError("missing NVIDIA key")),
    )

    result = runner.invoke(app, ["search", "--query", "backend logs"])

    assert result.exit_code == 1
    assert "missing NVIDIA key" in result.stderr


def test_preflight_command_surfaces_missing_index_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: (_ for _ in ()).throw(MissingIndexError("Run `policynim ingest` first.")),
    )

    result = runner.invoke(app, ["preflight", "--task", "refresh token cleanup"])

    assert result.exit_code == 1
    assert "Run `policynim ingest` first." in result.stderr


def test_preflight_command_surfaces_configuration_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: (_ for _ in ()).throw(ConfigurationError("missing NVIDIA key")),
    )

    result = runner.invoke(app, ["preflight", "--task", "refresh token cleanup"])

    assert result.exit_code == 1
    assert "missing NVIDIA key" in result.stderr


def test_preflight_command_closes_service_when_it_errors(monkeypatch) -> None:
    class FailingPreflightService(MockPreflightService):
        def preflight(self, request) -> PreflightResult:
            raise MissingIndexError("Run `policynim ingest` first.")

    service = FailingPreflightService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["preflight", "--task", "refresh token cleanup"])

    assert result.exit_code == 1
    assert service.closed is True


def test_mcp_command_surfaces_streamable_http_port_conflicts(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.run_server",
        lambda transport: (_ for _ in ()).throw(
            ConfigurationError("Could not start streamable-http MCP server on 127.0.0.1:8000.")
        ),
    )

    result = runner.invoke(app, ["mcp", "--transport", "streamable-http"])

    assert result.exit_code == 1
    assert "streamable-http MCP server" in result.stderr
