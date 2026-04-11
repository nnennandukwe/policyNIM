"""Tests for the Day 3 CLI surface."""

from __future__ import annotations

import json
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from click import unstyle
from typer.testing import CliRunner

from policynim.errors import ConfigurationError, MissingIndexError, PolicyNIMError
from policynim.interfaces.cli import app
from policynim.services.runtime_evidence_report import RuntimeEvidenceReportService
from policynim.services.runtime_execution import RuntimeExecutionService
from policynim.settings import Settings, get_settings
from policynim.storage import RuntimeEvidenceStore
from policynim.types import (
    BetaAccount,
    Citation,
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    CompileRequest,
    CompileResult,
    EvalAggregateMetrics,
    EvalBackend,
    EvalCaseMetrics,
    EvalCaseResult,
    EvalComparisonDelta,
    EvalModeRunResult,
    EvalRunResult,
    IngestResult,
    PolicyChunk,
    PolicyConformanceTraceStep,
    PolicyGuidance,
    PolicyMetadata,
    PolicySelectionPacket,
    PreflightEvidenceTraceResult,
    PreflightRequest,
    PreflightResult,
    PreflightTraceResult,
    RouteRequest,
    RouteResult,
    RuntimeDecision,
    RuntimeDecisionResult,
    RuntimeExecutionResult,
    ScoredChunk,
    SearchRequest,
    SearchResult,
    SelectedPolicy,
    SelectedPolicyEvidence,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def clear_cached_settings() -> Generator[None, None, None]:
    """Prevent settings cache from leaking between CLI tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def write_env_file(path: Path, **values: str) -> None:
    """Write a small env-style config file for CLI setup tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clear_installer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear env that would interfere with standalone installer-style tests."""
    for key in (
        "NVIDIA_API_KEY",
        "POLICYNIM_CONFIG_FILE",
        "POLICYNIM_CORPUS_DIR",
        "POLICYNIM_LANCEDB_URI",
        "POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH",
        "POLICYNIM_RUNTIME_EVIDENCE_DB_PATH",
        "POLICYNIM_EVAL_WORKSPACE_DIR",
        "PORT",
    ):
        monkeypatch.delenv(key, raising=False)


def configure_standalone_cli_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    """Simulate an installed standalone runtime outside a contributor checkout."""
    clear_installer_env(monkeypatch)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    config_root = tmp_path / "user-config"
    data_root = tmp_path / "user-data"
    package_root = tmp_path / "site-packages" / "policynim"
    package_root.mkdir(parents=True)

    monkeypatch.setattr(
        "policynim.config_discovery.user_config_path",
        lambda *args, **kwargs: config_root,
    )
    monkeypatch.setattr(
        "policynim.config_discovery.user_data_path",
        lambda *args, **kwargs: data_root,
    )
    monkeypatch.setattr(
        "policynim.config_discovery.__file__",
        str(package_root / "config_discovery.py"),
    )

    return workspace, config_root, data_root


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


class MockRouteService:
    """Static route service for CLI tests."""

    def __init__(self) -> None:
        self.closed = False
        self.last_request: RouteRequest | None = None

    def route(self, request: RouteRequest) -> RouteResult:
        self.last_request = request
        return RouteResult(
            packet=PolicySelectionPacket(
                task=request.task,
                domain=request.domain,
                top_k=request.top_k,
                task_type=request.task_type or "bug_fix",
                explicit_task_type=request.task_type,
                profile_signals=(
                    [f"explicit:{request.task_type}"] if request.task_type is not None else ["fix"]
                ),
                selected_policies=[
                    SelectedPolicy(
                        policy_id="SECURITY-TOKEN-001",
                        title="Token handling",
                        domain="security",
                        reason="Selected for bug fix routing from 1 retained evidence chunk(s).",
                        evidence=[
                            SelectedPolicyEvidence(
                                chunk_id="SECURITY-1",
                                path="policies/security/tokens.md",
                                section="Rules",
                                lines="10-16",
                                text="Never log token values.",
                                score=0.99,
                            )
                        ],
                    )
                ],
                insufficient_context=False,
            ),
            retained_context=[],
        )

    def close(self) -> None:
        self.closed = True


class MockCompileService:
    """Static compile service for CLI tests."""

    def __init__(self) -> None:
        self.closed = False
        self.last_request: CompileRequest | None = None

    def compile(self, request: CompileRequest) -> CompileResult:
        self.last_request = request
        packet = CompiledPolicyPacket(
            task=request.task,
            domain=request.domain,
            top_k=request.top_k,
            task_type=request.task_type or "bug_fix",
            explicit_task_type=request.task_type,
            profile_signals=(
                [f"explicit:{request.task_type}"] if request.task_type is not None else ["fix"]
            ),
            selected_policies=[
                SelectedPolicy(
                    policy_id="SECURITY-TOKEN-001",
                    title="Token handling",
                    domain="security",
                    reason="Selected for bug fix routing from 1 retained evidence chunk(s).",
                    evidence=[
                        SelectedPolicyEvidence(
                            chunk_id="SECURITY-1",
                            path="policies/security/tokens.md",
                            section="Rules",
                            lines="10-16",
                            text="Never log token values.",
                            score=0.99,
                        )
                    ],
                )
            ],
            required_steps=[
                CompiledPolicyConstraint(
                    statement="Preserve token revocation checks.",
                    citation_ids=["SECURITY-1"],
                    source_policy_ids=["SECURITY-TOKEN-001"],
                )
            ],
            forbidden_patterns=[
                CompiledPolicyConstraint(
                    statement="Do not log raw token values.",
                    citation_ids=["SECURITY-1"],
                    source_policy_ids=["SECURITY-TOKEN-001"],
                )
            ],
            citations=[
                Citation(
                    policy_id="SECURITY-TOKEN-001",
                    title="Token handling",
                    path="policies/security/tokens.md",
                    section="Rules",
                    lines="10-16",
                    chunk_id="SECURITY-1",
                )
            ],
            insufficient_context=False,
        )
        return CompileResult(packet=packet, retained_context=[])

    def close(self) -> None:
        self.closed = True


class MockPreflightService:
    """Static preflight service for CLI tests."""

    def __init__(self) -> None:
        self.closed = False
        self.preflight_calls = 0
        self.trace_calls = 0

    def preflight(self, request: PreflightRequest) -> PreflightResult:
        self.preflight_calls += 1
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

    def preflight_with_trace(self, request: PreflightRequest) -> PreflightTraceResult:
        self.trace_calls += 1
        result = self.preflight(request)
        chunk = ScoredChunk(
            chunk_id="AUTH-1",
            path="policies/security/auth-review.md",
            section="Cleanup",
            lines="10-16",
            text="Retain revocation checks before deleting stale refresh tokens.",
            policy=PolicyMetadata(
                policy_id="AUTH-001",
                title="Auth Reviews",
                doc_type="guidance",
                domain="security",
            ),
            score=0.98,
        )
        return PreflightTraceResult(
            result=result,
            compiled_packet=CompiledPolicyPacket(
                task=request.task,
                domain=request.domain,
                top_k=request.top_k,
                task_type="feature_work",
                selected_policies=[
                    SelectedPolicy(
                        policy_id="AUTH-001",
                        title="Auth Reviews",
                        domain="security",
                        reason="Selected for token cleanup guidance.",
                        evidence=[
                            SelectedPolicyEvidence(
                                chunk_id="AUTH-1",
                                path="policies/security/auth-review.md",
                                section="Cleanup",
                                lines="10-16",
                                text=(
                                    "Retain revocation checks before deleting stale refresh tokens."
                                ),
                                score=0.98,
                            )
                        ],
                    )
                ],
                required_steps=[
                    CompiledPolicyConstraint(
                        statement="Retain revocation checks before deleting stale refresh tokens.",
                        citation_ids=["AUTH-1"],
                        source_policy_ids=["AUTH-001"],
                    )
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
            ),
            retained_context=[chunk],
            trace_steps=[
                PolicyConformanceTraceStep(
                    step_id="compile",
                    kind="policy_compilation",
                    summary="Compiled policy packet for generation.",
                    citation_ids=["AUTH-1"],
                )
            ],
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


class MockRuntimeDecisionService:
    """Static runtime decision service for CLI tests."""

    def __init__(
        self,
        *,
        decision: RuntimeDecision = "allow",
        summary: str | None = None,
    ) -> None:
        self.decision: RuntimeDecision = decision
        self.summary: str = summary or "No runtime policy rules matched this action."
        self.closed: bool = False
        self.last_request: object | None = None

    def decide(self, request) -> RuntimeDecisionResult:
        self.last_request = request
        return RuntimeDecisionResult(
            request=request,
            decision=self.decision,
            summary=self.summary,
            matched_rules=[],
            citations=[],
        )

    def close(self) -> None:
        self.closed = True


class StubRuntimeEvidenceStore:
    """Minimal append-only evidence store for CLI runtime execution tests."""

    def __init__(self) -> None:
        self.events = []
        self.closed = False

    def append_event(self, record) -> None:
        self.events.append(record)

    def list_session_events(self, session_id: str):
        return [event for event in self.events if event.session_id == session_id]

    def close(self) -> None:
        self.closed = True


class _MockJSONModel:
    """Tiny JSON-emitting model used by CLI report tests."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def model_dump_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self._payload, indent=indent)


class MockRuntimeEvidenceReportService:
    """Static evidence report service for CLI tests."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.closed = False
        self.last_session_id: str | None = None

    def report_session(self, session_id: str) -> _MockJSONModel:
        self.last_session_id = session_id
        return _MockJSONModel(self._payload)

    def close(self) -> None:
        self.closed = True


class MockEvalService:
    """Static eval service for CLI tests."""

    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed
        self.launch_port: int | None = None
        self.started_ui = False

    def run(
        self,
        *,
        mode,
        backend: EvalBackend = "default",
        compare_rerank,
    ) -> EvalRunResult:
        passed_count = 2 if self.passed else 1
        return EvalRunResult(
            mode=mode,
            backend=backend,
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


class MockBetaAuthService:
    """Static hosted beta auth service for CLI tests."""

    def __init__(self) -> None:
        self._account = BetaAccount(
            account_id=1,
            github_user_id=123,
            github_login="octocat",
            email="octocat@example.com",
            status="active",
            created_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            last_login_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            api_key_prefix="pnm_current",
            api_key_created_at=datetime(2026, 4, 5, 12, 5, tzinfo=UTC),
        )

    def close(self) -> None:
        return None

    def list_accounts(self) -> list[BetaAccount]:
        return [self._account]

    def suspend_account(self, *, github_login: str) -> BetaAccount:
        assert github_login == "octocat"
        self._account = self._account.model_copy(update={"status": "suspended"})
        return self._account

    def resume_account(self, *, github_login: str) -> BetaAccount:
        assert github_login == "octocat"
        self._account = self._account.model_copy(update={"status": "active"})
        return self._account

    def revoke_api_key(self, *, github_login: str) -> BetaAccount:
        assert github_login == "octocat"
        self._account = self._account.model_copy(update={"api_key_prefix": None})
        return self._account


class _RuntimeDecisionStub:
    """Static decision stub for wiring the real execution service in CLI tests."""

    def __init__(self, decision: RuntimeDecision, *, summary: str | None = None) -> None:
        self._decision: RuntimeDecision = decision
        self._summary: str = summary or "Decision summary."
        self.closed: bool = False

    def decide(self, request) -> RuntimeDecisionResult:
        return RuntimeDecisionResult(
            request=request,
            decision=self._decision,
            summary=self._summary,
            matched_rules=[],
            citations=[],
        )

    def close(self) -> None:
        self.closed = True


def make_runtime_execution_service(
    *,
    decision: RuntimeDecision,
    summary: str | None = None,
    confirmer=None,
) -> RuntimeExecutionService:
    """Build the real runtime execution service with test doubles behind it."""
    return RuntimeExecutionService(
        decision_service=cast(Any, _RuntimeDecisionStub(decision, summary=summary)),
        evidence_store=StubRuntimeEvidenceStore(),
        confirmer=confirmer,
    )


def make_sqlite_runtime_execution_service(
    *,
    db_path: Path,
    decision: RuntimeDecision,
    summary: str | None = None,
    confirmer=None,
) -> RuntimeExecutionService:
    """Build the real runtime execution service backed by SQLite evidence."""
    return RuntimeExecutionService(
        decision_service=cast(Any, _RuntimeDecisionStub(decision, summary=summary)),
        evidence_store=RuntimeEvidenceStore(path=db_path),
        confirmer=confirmer,
    )


def make_stderr_prompt_confirmer():
    """Read confirmation from stdin while keeping prompt text off stdout."""

    def confirm(decision_result: RuntimeDecisionResult) -> bool:
        sys.stderr.write(f"{decision_result.summary} Continue with runtime execution? [y/N]: ")
        sys.stderr.flush()
        response = sys.stdin.readline().strip().lower()
        sys.stderr.write("\n")
        return response in {"y", "yes"}

    return confirm


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


def test_route_command_prints_policy_selection_packet(monkeypatch) -> None:
    service = MockRouteService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_router_service",
        lambda settings: service,
    )

    result = runner.invoke(
        app,
        [
            "route",
            "--task",
            "fix token logging bug",
            "--domain",
            "security",
            "--top-k",
            "2",
            "--task-type",
            "bug_fix",
        ],
    )

    assert result.exit_code == 0
    payload = PolicySelectionPacket.model_validate(json.loads(result.stdout))
    assert payload.task == "fix token logging bug"
    assert payload.domain == "security"
    assert payload.top_k == 2
    assert payload.task_type == "bug_fix"
    assert payload.explicit_task_type == "bug_fix"
    assert payload.selected_policies[0].evidence[0].chunk_id == "SECURITY-1"
    assert service.last_request is not None
    assert service.last_request.task_type == "bug_fix"
    assert service.closed is True


def test_route_command_rejects_invalid_task_type() -> None:
    result = runner.invoke(
        app,
        ["route", "--task", "fix token logging bug", "--task-type", "not-a-task-type"],
    )

    assert result.exit_code != 0
    assert "not-a-task-type" in result.output


def test_route_command_surfaces_missing_index_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_router_service",
        lambda settings: (_ for _ in ()).throw(
            MissingIndexError("Run `policynim ingest` before routing policy selection.")
        ),
    )

    result = runner.invoke(app, ["route", "--task", "fix token logging bug"])

    assert result.exit_code == 1
    assert "Run `policynim ingest` before routing policy selection." in result.stderr


def test_route_command_surfaces_configuration_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_router_service",
        lambda settings: (_ for _ in ()).throw(ConfigurationError("missing NVIDIA key")),
    )

    result = runner.invoke(app, ["route", "--task", "fix token logging bug"])

    assert result.exit_code == 1
    assert "missing NVIDIA key" in result.stderr


def test_route_command_closes_service_when_it_errors(monkeypatch) -> None:
    class FailingRouteService(MockRouteService):
        def route(self, request: RouteRequest) -> RouteResult:
            raise MissingIndexError("Run `policynim ingest` before routing policy selection.")

    service = FailingRouteService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_router_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["route", "--task", "fix token logging bug"])

    assert result.exit_code == 1
    assert service.closed is True


def test_compile_command_prints_compiled_policy_packet(monkeypatch) -> None:
    service = MockCompileService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_compiler_service",
        lambda settings: service,
    )

    result = runner.invoke(
        app,
        [
            "compile",
            "--task",
            "fix token logging bug",
            "--domain",
            "security",
            "--top-k",
            "2",
            "--task-type",
            "bug_fix",
        ],
    )

    assert result.exit_code == 0
    payload = CompiledPolicyPacket.model_validate(json.loads(result.stdout))
    assert payload.task == "fix token logging bug"
    assert payload.domain == "security"
    assert payload.top_k == 2
    assert payload.task_type == "bug_fix"
    assert payload.required_steps[0].statement == "Preserve token revocation checks."
    assert payload.citations[0].chunk_id == "SECURITY-1"
    assert service.last_request is not None
    assert service.last_request.task_type == "bug_fix"
    assert service.closed is True


def test_compile_command_rejects_invalid_task_type() -> None:
    result = runner.invoke(
        app,
        ["compile", "--task", "fix token logging bug", "--task-type", "not-a-task-type"],
    )

    assert result.exit_code != 0
    assert "not-a-task-type" in result.output


def test_compile_command_formats_request_validation_errors() -> None:
    result = runner.invoke(app, ["compile", "--task", "   "])

    assert result.exit_code == 1
    assert "Compile request is invalid at task" in result.stderr
    assert "task must not be empty" in result.stderr
    assert "Traceback" not in result.stderr


def test_compile_command_surfaces_missing_index_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_compiler_service",
        lambda settings: (_ for _ in ()).throw(
            MissingIndexError("Run `policynim ingest` before compiling policy constraints.")
        ),
    )

    result = runner.invoke(app, ["compile", "--task", "fix token logging bug"])

    assert result.exit_code == 1
    assert "Run `policynim ingest` before compiling policy constraints." in result.stderr


def test_compile_command_surfaces_configuration_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_compiler_service",
        lambda settings: (_ for _ in ()).throw(ConfigurationError("missing NVIDIA key")),
    )

    result = runner.invoke(app, ["compile", "--task", "fix token logging bug"])

    assert result.exit_code == 1
    assert "missing NVIDIA key" in result.stderr


def test_compile_command_closes_service_when_it_errors(monkeypatch) -> None:
    class FailingCompileService(MockCompileService):
        def compile(self, request: CompileRequest) -> CompileResult:
            raise MissingIndexError("Run `policynim ingest` before compiling policy constraints.")

    service = FailingCompileService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_compiler_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["compile", "--task", "fix token logging bug"])

    assert result.exit_code == 1
    assert service.closed is True


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


def test_eval_command_accepts_nemo_backend(monkeypatch) -> None:
    service = MockEvalService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        lambda settings: service,
    )

    result = runner.invoke(
        app,
        ["eval", "--mode", "offline", "--backend", "nemo", "--headless"],
    )

    assert result.exit_code == 0
    payload = EvalRunResult.model_validate(json.loads(result.stdout))
    assert payload.backend == "nemo"


def test_eval_command_rejects_invalid_backend() -> None:
    result = runner.invoke(app, ["eval", "--backend", "not-a-backend", "--headless"])

    assert result.exit_code != 0
    assert "not-a-backend" in result.output


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
    assert service.preflight_calls == 1
    assert service.trace_calls == 0
    assert service.closed is True


def test_preflight_trace_command_prints_trace_wrapper(monkeypatch) -> None:
    service = MockPreflightService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: service,
    )

    result = runner.invoke(
        app,
        [
            "preflight",
            "--task",
            "refresh token cleanup",
            "--domain",
            "security",
            "--top-k",
            "3",
            "--trace",
        ],
    )

    assert result.exit_code == 0
    payload = PreflightEvidenceTraceResult.model_validate(json.loads(result.stdout))
    assert payload.result.task == "refresh token cleanup"
    assert payload.evidence_trace.task == "refresh token cleanup"
    assert payload.evidence_trace.chunks[0].chunk_id == "AUTH-1"
    assert payload.evidence_trace.selected_policies[0].supporting_chunk_ids == ["AUTH-1"]
    assert payload.evidence_trace.constraints[0].constraint_id == "required_steps:0"
    assert payload.evidence_trace.output_links[0].chunk_ids == ["AUTH-1"]
    assert payload.evidence_trace.trace_steps[0].step_id == "compile"
    assert service.preflight_calls == 1
    assert service.trace_calls == 1
    assert service.closed is True


@pytest.mark.parametrize(
    ("args", "field", "message"),
    [
        (["preflight", "--task", "   "], "task", "task must not be empty"),
        (
            ["preflight", "--task", "refresh token cleanup", "--domain", "   "],
            "domain",
            "domain must not be empty",
        ),
    ],
)
def test_preflight_command_formats_route_validation_errors(
    args: list[str],
    field: str,
    message: str,
    monkeypatch,
) -> None:
    class FailingPreflightService(MockPreflightService):
        def preflight(self, request: PreflightRequest) -> PreflightResult:
            RouteRequest(task=request.task, domain=request.domain, top_k=request.top_k)
            raise AssertionError("expected RouteRequest validation to fail")

    service = FailingPreflightService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: service,
    )

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert f"Preflight request is invalid at {field}" in result.stderr
    assert message in result.stderr
    assert "Traceback" not in result.stderr
    assert "RouteRequest" not in result.stderr
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


def test_dump_index_count_only_prints_only_count(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_index_dump_service",
        lambda settings: MockIndexDumpService(),
    )

    result = runner.invoke(app, ["dump-index", "--count-only"])

    assert result.exit_code == 0
    assert result.stdout == "Indexed chunks: 1\n"


def test_help_includes_dump_index_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "dump-index" in result.stdout


def test_help_includes_runtime_and_evidence_commands() -> None:
    result = runner.invoke(app, ["--help"])
    help_output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "--version" in help_output
    assert "init" in help_output
    assert "runtime" in help_output
    assert "evidence" in help_output
    assert "route" in help_output
    assert "compile" in help_output


def test_version_flag_prints_installed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli._resolve_installed_version",
        lambda: "1.2.3",
        raising=False,
    )

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "1.2.3\n"
    assert result.stderr == ""


def test_version_flag_surfaces_metadata_errors_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_version_lookup() -> str:
        raise PolicyNIMError("Installed package metadata for PolicyNIM is unavailable.")

    monkeypatch.setattr(
        "policynim.interfaces.cli._resolve_installed_version",
        fail_version_lookup,
        raising=False,
    )

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 1
    assert "Installed package metadata for PolicyNIM is unavailable." in result.stderr
    assert "Traceback" not in result.stderr


def test_init_help_documents_interactive_setup_flow() -> None:
    result = runner.invoke(app, ["init", "--help"])

    assert result.exit_code == 0
    assert "interactive" in result.stdout.lower()
    assert "NVIDIA_API_KEY" in result.stdout
    assert "--non-interactive" not in result.stdout


def test_init_command_writes_config_and_prints_next_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, config_root, data_root = configure_standalone_cli_environment(monkeypatch, tmp_path)
    custom_corpus = tmp_path / "custom-corpus"
    custom_corpus.mkdir()

    result = runner.invoke(
        app,
        ["init"],
        input=f"nvapi-test-key\n{custom_corpus}\n",
    )

    config_path = config_root / "config.env"
    assert result.exit_code == 0
    assert str(config_path) in result.stdout
    assert str(custom_corpus) in result.stdout
    assert "policynim ingest" in result.stdout
    assert config_path.read_text(encoding="utf-8") == (
        "NVIDIA_API_KEY='nvapi-test-key'\n"
        f"POLICYNIM_CORPUS_DIR='{custom_corpus}'\n"
        f"POLICYNIM_LANCEDB_URI='{data_root / 'lancedb'}'\n"
        f"POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH='{data_root / 'runtime' / 'runtime_rules.json'}'\n"
        "POLICYNIM_RUNTIME_EVIDENCE_DB_PATH="
        f"'{data_root / 'runtime' / 'runtime_evidence.sqlite3'}'\n"
        f"POLICYNIM_EVAL_WORKSPACE_DIR='{data_root / 'evals' / 'workspace'}'\n"
    )


def test_init_command_rejects_blank_required_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, config_root, _ = configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["init"], input="\n")

    assert result.exit_code == 1
    assert "NVIDIA_API_KEY is required." in result.stderr
    assert not (config_root / "config.env").exists()


def test_init_command_surfaces_unwritable_config_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_standalone_cli_environment(monkeypatch, tmp_path)
    target_config = tmp_path / "blocked" / "config.env"
    monkeypatch.setenv("POLICYNIM_CONFIG_FILE", str(target_config))

    def fail_replace(src: str, dst: str) -> None:
        raise PermissionError("permission denied")

    monkeypatch.setattr("policynim.config_discovery.os.replace", fail_replace)

    result = runner.invoke(app, ["init"], input="nvapi-test-key\n\n")

    assert result.exit_code == 1
    assert str(target_config) in result.stderr
    assert "permission denied" in result.stderr
    assert not target_config.exists()
    assert list(target_config.parent.glob("*.tmp")) == []


def test_route_help_mentions_task_type_override() -> None:
    result = runner.invoke(app, ["route", "--help"])
    help_output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "--task-type" in help_output
    assert "Selected evidence depth." in help_output


def test_compile_help_mentions_task_type_override() -> None:
    result = runner.invoke(app, ["compile", "--help"])
    help_output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "--task-type" in help_output
    assert "Selected evidence depth." in help_output


def test_runtime_help_mentions_decide_and_execute_commands() -> None:
    result = runner.invoke(app, ["runtime", "--help"])

    assert result.exit_code == 0
    assert "decide" in result.stdout
    assert "execute" in result.stdout


def test_evidence_help_mentions_report_command() -> None:
    result = runner.invoke(app, ["evidence", "--help"])

    assert result.exit_code == 0
    assert "report" in result.stdout


def test_dump_index_help_mentions_less_for_paging() -> None:
    result = runner.invoke(app, ["dump-index", "--help"])

    assert result.exit_code == 0
    assert "add ` | less`" in result.stdout
    assert "paging large output" in result.stdout


def test_preflight_help_mentions_current_top_k_behavior() -> None:
    result = runner.invoke(app, ["preflight", "--help"])
    help_text = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "Retrieval depth." in help_text
    assert "1-20." in help_text
    assert "--trace" in help_text
    assert "Reserved retrieval depth" not in help_text


def test_search_command_surfaces_configuration_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_search_service",
        lambda settings: (_ for _ in ()).throw(ConfigurationError("missing NVIDIA key")),
    )

    result = runner.invoke(app, ["search", "--query", "backend logs"])

    assert result.exit_code == 1
    assert "missing NVIDIA key" in result.stderr


def test_search_command_points_to_init_when_standalone_setup_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, config_root, _ = configure_standalone_cli_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_search_service",
        lambda settings: (_ for _ in ()).throw(
            ConfigurationError("NVIDIA_API_KEY is required for embeddings.")
        ),
    )

    result = runner.invoke(app, ["search", "--query", "backend logs"])

    assert result.exit_code == 1
    assert "PolicyNIM is not set up yet." in result.stderr
    assert "policynim init" in result.stderr
    assert str(config_root / "config.env") in result.stderr
    assert "policynim ingest" not in result.stderr


def test_search_command_points_to_ingest_when_config_exists_but_index_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, config_root, _ = configure_standalone_cli_environment(monkeypatch, tmp_path)
    write_env_file(config_root / "config.env", NVIDIA_API_KEY="nvapi-test-key")
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_search_service",
        lambda settings: (_ for _ in ()).throw(MissingIndexError("Local index is missing.")),
    )

    result = runner.invoke(app, ["search", "--query", "backend logs"])

    assert result.exit_code == 1
    assert "policynim ingest" in result.stderr
    assert "policynim init" not in result.stderr


@pytest.mark.parametrize(
    ("argv", "stdin_text"),
    [
        (["ingest"], None),
        (["search", "--query", "backend logs"], None),
        (["preflight", "--task", "refresh token cleanup"], None),
        (["compile", "--task", "refresh token cleanup"], None),
        (["dump-index"], None),
        (["eval", "--headless"], None),
        (
            ["runtime", "decide", "--input", "-"],
            json.dumps(
                {
                    "kind": "shell_command",
                    "task": "Run tests.",
                    "cwd": "/tmp",
                    "command": ["make", "test"],
                }
            ),
        ),
        (
            ["runtime", "execute", "--input", "-"],
            json.dumps(
                {
                    "kind": "shell_command",
                    "task": "Run tests.",
                    "cwd": "/tmp",
                    "command": ["make", "test"],
                }
            ),
        ),
        (["evidence", "report", "--session-id", "session-123"], None),
        (["mcp"], None),
    ],
)
def test_setup_dependent_commands_point_to_init_when_redirected_config_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    stdin_text: str | None,
) -> None:
    configure_standalone_cli_environment(monkeypatch, tmp_path)
    redirected_config = tmp_path / "redirected" / "config.env"
    monkeypatch.setenv("POLICYNIM_CONFIG_FILE", str(redirected_config))
    monkeypatch.setattr(
        "policynim.interfaces.cli.run_server",
        lambda transport: (_ for _ in ()).throw(AssertionError("run_server should not be called")),
    )

    result = runner.invoke(app, argv, input=stdin_text)

    assert result.exit_code == 1
    assert "PolicyNIM is not set up yet." in result.stderr
    assert "policynim init" in result.stderr
    assert str(redirected_config) in result.stderr


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


def test_preflight_trace_command_closes_service_when_it_errors(monkeypatch) -> None:
    class FailingTracePreflightService(MockPreflightService):
        def preflight_with_trace(self, request: PreflightRequest) -> PreflightTraceResult:
            raise MissingIndexError("Run `policynim ingest` first.")

    service = FailingTracePreflightService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["preflight", "--task", "refresh token cleanup", "--trace"])

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


def test_mcp_command_surfaces_hosted_startup_index_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.run_server",
        lambda transport: (_ for _ in ()).throw(
            ConfigurationError("Hosted streamable-http startup requires a populated local index.")
        ),
    )

    result = runner.invoke(app, ["mcp", "--transport", "streamable-http"])

    assert result.exit_code == 1
    assert "populated local index" in result.stderr


def test_mcp_command_surfaces_hosted_rebuild_key_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.run_server",
        lambda transport: (_ for _ in ()).throw(
            ConfigurationError(
                "Hosted streamable-http startup requires a populated local index at "
                "/app/data/lancedb-baked (table: policy_chunks). "
                "Automatic hosted-index rebuild failed: ConfigurationError: "
                "NVIDIA_API_KEY is required for embeddings. "
                "Rebuild the image so `policynim ingest` runs during Docker build "
                "or set `POLICYNIM_LANCEDB_URI` to a populated directory."
            )
        ),
    )

    result = runner.invoke(app, ["mcp", "--transport", "streamable-http"])

    assert result.exit_code == 1
    assert "NVIDIA_API_KEY is required for embeddings." in result.stderr
    assert "Rebuild the image so `policynim ingest` runs during Docker build" in result.stderr


def test_beta_admin_list_accounts_prints_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_beta_auth_service",
        lambda settings: MockBetaAuthService(),
    )

    result = runner.invoke(app, ["beta-admin", "list-accounts"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["github_login"] == "octocat"


def test_beta_admin_suspend_and_resume_print_json(monkeypatch) -> None:
    service = MockBetaAuthService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_beta_auth_service",
        lambda settings: service,
    )

    suspended = runner.invoke(app, ["beta-admin", "suspend", "--github-login", "octocat"])
    resumed = runner.invoke(app, ["beta-admin", "resume", "--github-login", "octocat"])

    assert suspended.exit_code == 0
    assert json.loads(suspended.stdout)["status"] == "suspended"
    assert resumed.exit_code == 0
    assert json.loads(resumed.stdout)["status"] == "active"


def test_beta_admin_revoke_key_prints_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_beta_auth_service",
        lambda settings: MockBetaAuthService(),
    )

    result = runner.invoke(app, ["beta-admin", "revoke-key", "--github-login", "octocat"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["api_key_prefix"] is None


def test_beta_admin_help_mentions_operator_commands() -> None:
    result = runner.invoke(app, ["beta-admin", "--help"])

    assert result.exit_code == 0
    assert "list-accounts" in result.stdout
    assert "revoke-key" in result.stdout


def test_runtime_decide_command_reads_request_from_file_and_prints_json(
    monkeypatch,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "kind": "shell_command",
                "task": "Run tests.",
                "cwd": str(tmp_path),
                "command": ["make", "test"],
            }
        ),
        encoding="utf-8",
    )
    service = MockRuntimeDecisionService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_decision_service",
        lambda settings: service,
        raising=False,
    )

    result = runner.invoke(app, ["runtime", "decide", "--input", str(request_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "allow"
    assert payload["request"]["command"] == ["make", "test"]
    assert service.closed is True


def test_runtime_decide_command_reads_request_from_stdin(monkeypatch, tmp_path: Path) -> None:
    service = MockRuntimeDecisionService(decision="block", summary="Protect deploy commands.")
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_decision_service",
        lambda settings: service,
        raising=False,
    )

    result = runner.invoke(
        app,
        ["runtime", "decide", "--input", "-"],
        input=json.dumps(
            {
                "kind": "shell_command",
                "task": "Run deploy.",
                "cwd": str(tmp_path),
                "command": ["make", "deploy"],
            }
        ),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert payload["summary"] == "Protect deploy commands."


def test_runtime_decide_command_rejects_invalid_json_input_file(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text("{not-json", encoding="utf-8")

    result = runner.invoke(app, ["runtime", "decide", "--input", str(request_path)])

    assert result.exit_code == 1
    assert str(request_path) in result.stderr
    assert "JSON" in result.stderr


def test_runtime_execute_command_rejects_whitespace_only_stdin() -> None:
    result = runner.invoke(app, ["runtime", "execute", "--input", "-"], input="   \n")

    assert result.exit_code == 1
    assert "must not be empty" in result.stderr


def test_runtime_execute_command_rejects_malformed_runtime_request(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["runtime", "execute", "--input", "-"],
        input=json.dumps(
            {
                "task": "Run tests.",
                "cwd": str(tmp_path),
                "command": ["make", "test"],
            }
        ),
    )

    assert result.exit_code == 1
    assert "kind" in result.stderr


def test_runtime_execute_command_reads_request_from_file(monkeypatch, tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "kind": "shell_command",
                "task": "Run a passing shell command.",
                "cwd": str(tmp_path),
                "command": [sys.executable, "-c", "raise SystemExit(0)"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_runtime_execution_service(
            decision="allow",
            confirmer=confirmer,
        ),
        raising=False,
    )

    result = runner.invoke(app, ["runtime", "execute", "--input", str(request_path)])

    assert result.exit_code == 0
    payload = RuntimeExecutionResult.model_validate(json.loads(result.stdout))
    assert payload.execution_outcome == "allowed"
    assert payload.session_id


def test_runtime_execute_command_rejects_non_object_json(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text('["not-an-object"]', encoding="utf-8")

    result = runner.invoke(app, ["runtime", "execute", "--input", str(request_path)])

    assert result.exit_code == 1
    assert "JSON object" in result.stderr


def test_runtime_execute_command_reads_request_from_stdin_and_echoes_resolved_session_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_runtime_execution_service(
            decision="allow",
            confirmer=confirmer,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["runtime", "execute", "--input", "-"],
        input=json.dumps(
            {
                "kind": "shell_command",
                "task": "Run a passing shell command.",
                "cwd": str(tmp_path),
                "command": [sys.executable, "-c", "raise SystemExit(0)"],
            }
        ),
    )

    assert result.exit_code == 0
    payload = RuntimeExecutionResult.model_validate(json.loads(result.stdout))
    assert payload.execution_outcome == "allowed"
    assert payload.session_id
    assert payload.request.session_id == payload.session_id


def test_runtime_execute_command_preserves_caller_provided_session_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_runtime_execution_service(
            decision="allow",
            confirmer=confirmer,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["runtime", "execute", "--input", "-"],
        input=json.dumps(
            {
                "kind": "shell_command",
                "task": "Run a passing shell command.",
                "cwd": str(tmp_path),
                "session_id": "session-from-request",
                "command": [sys.executable, "-c", "raise SystemExit(0)"],
            }
        ),
    )

    assert result.exit_code == 0
    payload = RuntimeExecutionResult.model_validate(json.loads(result.stdout))
    assert payload.session_id == "session-from-request"


def test_runtime_execute_command_returns_non_zero_for_blocked_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_runtime_execution_service(
            decision="block",
            summary="Protect this file.",
            confirmer=confirmer,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["runtime", "execute", "--input", "-"],
        input=json.dumps(
            {
                "kind": "file_write",
                "task": "Write a blocked file.",
                "cwd": str(tmp_path),
                "path": "blocked.txt",
                "content": "payload",
            }
        ),
    )

    assert result.exit_code == 1
    payload = RuntimeExecutionResult.model_validate(json.loads(result.stdout))
    assert payload.execution_outcome == "blocked"


def test_runtime_execute_command_returns_non_zero_for_refused_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_runtime_execution_service(
            decision="confirm",
            summary="Review this write.",
            confirmer=lambda _: False,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["runtime", "execute", "--input", "-"],
        input=json.dumps(
            {
                "kind": "file_write",
                "task": "Write a guarded file.",
                "cwd": str(tmp_path),
                "path": "guarded.txt",
                "content": "payload",
            }
        ),
    )

    assert result.exit_code == 1
    payload = RuntimeExecutionResult.model_validate(json.loads(result.stdout))
    assert payload.execution_outcome == "refused"


def test_runtime_execute_command_returns_non_zero_for_failed_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_runtime_execution_service(
            decision="allow",
            confirmer=confirmer,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["runtime", "execute", "--input", "-"],
        input=json.dumps(
            {
                "kind": "shell_command",
                "task": "Run a failing shell command.",
                "cwd": str(tmp_path),
                "command": [sys.executable, "-c", "raise SystemExit(7)"],
            }
        ),
    )

    assert result.exit_code == 1
    payload = RuntimeExecutionResult.model_validate(json.loads(result.stdout))
    assert payload.execution_outcome == "failed"
    assert payload.failure_class == "non_zero_exit"


def test_runtime_execute_command_fails_closed_when_confirmation_is_non_interactive(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_runtime_execution_service(
            decision="confirm",
            summary="Review this write.",
            confirmer=confirmer,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["runtime", "execute", "--input", "-"],
        input=json.dumps(
            {
                "kind": "file_write",
                "task": "Write a confirmed file.",
                "cwd": str(tmp_path),
                "path": "guarded.txt",
                "content": "payload",
            }
        ),
    )

    assert result.exit_code == 1
    payload = RuntimeExecutionResult.model_validate(json.loads(result.stdout))
    assert payload.execution_outcome == "failed"
    assert payload.failure_class == "confirmation_unavailable"


def test_runtime_execute_command_accepts_interactive_confirmation_without_stdout_noise(
    monkeypatch,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "kind": "file_write",
                "task": "Write a confirmed file.",
                "cwd": str(tmp_path),
                "path": "guarded.txt",
                "content": "payload",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "policynim.interfaces.cli._build_cli_confirmer",
        make_stderr_prompt_confirmer,
        raising=False,
    )
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_runtime_execution_service(
            decision="confirm",
            summary="Review this write.",
            confirmer=confirmer,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["runtime", "execute", "--input", str(request_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    payload = RuntimeExecutionResult.model_validate(json.loads(result.stdout))
    assert payload.execution_outcome == "confirmed"
    assert "Continue with runtime execution?" not in result.stdout
    assert "Continue with runtime execution?" in result.stderr


def test_runtime_execute_command_rejects_interactive_confirmation_without_stdout_noise(
    monkeypatch,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "kind": "file_write",
                "task": "Write a confirmed file.",
                "cwd": str(tmp_path),
                "path": "guarded.txt",
                "content": "payload",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "policynim.interfaces.cli._build_cli_confirmer",
        make_stderr_prompt_confirmer,
        raising=False,
    )
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_runtime_execution_service(
            decision="confirm",
            summary="Review this write.",
            confirmer=confirmer,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["runtime", "execute", "--input", str(request_path)],
        input="n\n",
    )

    assert result.exit_code == 1
    payload = RuntimeExecutionResult.model_validate(json.loads(result.stdout))
    assert payload.execution_outcome == "refused"
    assert "Continue with runtime execution?" not in result.stdout
    assert "Continue with runtime execution?" in result.stderr


def test_runtime_execute_and_evidence_report_share_real_sqlite_session_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "kind": "file_write",
                "task": "Write a file with durable evidence.",
                "cwd": str(tmp_path),
                "path": "notes.txt",
                "content": "payload",
            }
        ),
        encoding="utf-8",
    )
    runtime_db_path = tmp_path / "runtime" / "runtime_evidence.sqlite3"
    runtime_settings = Settings(runtime_evidence_db_path=runtime_db_path)

    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_execution_service",
        lambda settings, confirmer=None: make_sqlite_runtime_execution_service(
            db_path=runtime_db_path,
            decision="allow",
            confirmer=confirmer,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_evidence_report_service",
        lambda settings: RuntimeEvidenceReportService(
            evidence_store=RuntimeEvidenceStore(path=runtime_db_path)
        ),
        raising=False,
    )
    monkeypatch.setattr("policynim.interfaces.cli.get_settings", lambda: runtime_settings)

    execution = runner.invoke(app, ["runtime", "execute", "--input", str(request_path)])

    assert execution.exit_code == 0
    execution_payload = RuntimeExecutionResult.model_validate(json.loads(execution.stdout))
    assert execution_payload.execution_outcome == "allowed"

    report = runner.invoke(
        app,
        ["evidence", "report", "--session-id", execution_payload.session_id],
    )

    assert report.exit_code == 0
    payload = json.loads(report.stdout)
    assert payload["session_id"] == execution_payload.session_id
    assert payload["allowed_count"] == 1
    assert payload["execution_count"] == 1


def test_evidence_report_command_prints_session_summary_json(monkeypatch) -> None:
    service = MockRuntimeEvidenceReportService(
        {
            "session_id": "session-1",
            "started_at": "2026-04-05T12:00:00+00:00",
            "completed_at": "2026-04-05T12:00:10+00:00",
            "event_count": 2,
            "execution_count": 1,
            "allowed_count": 1,
            "confirmed_count": 0,
            "blocked_count": 0,
            "refused_count": 0,
            "failed_count": 0,
            "incomplete_count": 0,
            "executions": [],
        }
    )
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_evidence_report_service",
        lambda settings: service,
        raising=False,
    )

    result = runner.invoke(app, ["evidence", "report", "--session-id", "session-1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["session_id"] == "session-1"
    assert payload["execution_count"] == 1
    assert service.closed is True


def test_evidence_report_command_surfaces_missing_session_errors(monkeypatch) -> None:
    class MissingSessionReportService:
        def report_session(self, session_id: str):
            raise PolicyNIMError(f"No runtime evidence found for session {session_id}.")

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_evidence_report_service",
        lambda settings: MissingSessionReportService(),
        raising=False,
    )

    result = runner.invoke(app, ["evidence", "report", "--session-id", "missing-session"])

    assert result.exit_code == 1
    assert "missing-session" in result.stderr
