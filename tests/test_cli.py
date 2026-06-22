"""Tests for the CLI surface."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from click import unstyle
from typer.testing import CliRunner

from policynim.errors import ConfigurationError, MissingIndexError, PolicyNIMError
from policynim.interfaces import cli as cli_module
from policynim.interfaces.cli import app
from policynim.services.runtime_evidence_report import RuntimeEvidenceReportService
from policynim.services.runtime_execution import RuntimeExecutionService
from policynim.settings import Settings, get_settings
from policynim.storage import RuntimeEvidenceStore
from policynim.storage.index_readiness import IndexReadinessReport
from policynim.types import (
    BetaAccount,
    BetaAuditEvent,
    Citation,
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    CompileRequest,
    CompileResult,
    EmbeddedChunk,
    EvalAggregateMetrics,
    EvalBackend,
    EvalCaseMetrics,
    EvalCaseResult,
    EvalComparisonDelta,
    EvalModeRunResult,
    EvalRunResult,
    IngestResult,
    PolicyChunk,
    PolicyConformanceMetric,
    PolicyConformanceResult,
    PolicyConformanceTraceStep,
    PolicyEvidenceTrace,
    PolicyGuidance,
    PolicyMetadata,
    PolicySelectionPacket,
    PreflightEvidenceTraceResult,
    PreflightRegenerationRequest,
    PreflightRegenerationResult,
    PreflightRequest,
    PreflightResult,
    PreflightTraceResult,
    RegenerationAttempt,
    RouteRequest,
    RouteResult,
    RuntimeDecision,
    RuntimeDecisionResult,
    RuntimeEvidenceExecutionSummary,
    RuntimeEvidenceSessionSummary,
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


def write_ready_sqlite_index(path: Path) -> None:
    """Write a minimal populated PolicyNIM sqlite-vec index for doctor tests."""
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    SQLiteVecIndexStore(path=path).replace(
        [
            EmbeddedChunk(
                chunk_id="DOCTOR-READY-1",
                path="policies/backend/doctor-ready.md",
                section="Rules",
                lines="1-3",
                text="Backend services should keep request ids in logs.",
                policy=PolicyMetadata(
                    policy_id="BACKEND-DOCTOR-001",
                    title="Doctor Ready",
                    doc_type="guidance",
                    domain="backend",
                    tags=["setup"],
                    grounded_in=["https://example.com/policy"],
                ),
                vector=[1.0, 0.0],
            )
        ]
    )


def clear_installer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear env that would interfere with standalone installer-style tests."""
    for key in (
        "NVIDIA_API_KEY",
        "POLICYNIM_CONFIG_FILE",
        "POLICYNIM_CORPUS_DIR",
        "POLICYNIM_INDEX_DB_PATH",
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


def configure_checkout_cli_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    """Simulate running the CLI from a source checkout."""
    clear_installer_env(monkeypatch)

    checkout_root = tmp_path / "checkout"
    package_root = checkout_root / "src" / "policynim"
    package_root.mkdir(parents=True)
    (checkout_root / "pyproject.toml").write_text(
        "[project]\nname = 'policynim'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(checkout_root)

    config_root = tmp_path / "user-config"
    data_root = tmp_path / "user-data"
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

    return checkout_root, config_root, data_root


class MockIngestService:
    """Static ingest service for CLI tests."""

    def __init__(self) -> None:
        """Track whether the CLI closed the service."""
        self.closed = False

    def run(self) -> IngestResult:
        return IngestResult(
            corpus_path="policies",
            index_uri="data/index.sqlite3",
            table_name="policy_chunks",
            embedding_model="mock-model",
            document_count=8,
            chunk_count=24,
            embedding_dimension=2,
        )

    def close(self) -> None:
        """Record that the CLI released the service."""
        self.closed = True


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

    def __init__(self) -> None:
        """Track whether the CLI closed the dump service."""
        self.closed = False

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

    def close(self) -> None:
        """Record that the CLI released the dump service."""
        self.closed = True


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


class MockPolicyRegenerationService:
    """Static regeneration service for CLI tests."""

    def __init__(self) -> None:
        self.closed = False
        self.last_request: PreflightRegenerationRequest | None = None

    def regenerate(self, request: PreflightRegenerationRequest) -> PreflightRegenerationResult:
        self.last_request = request
        final_result = PreflightResult(
            task=request.task,
            domain=request.domain,
            summary="Regenerated policy guidance.",
            applicable_policies=[
                PolicyGuidance(
                    policy_id="AUTH-001",
                    title="Auth Reviews",
                    rationale="Cleanup logic must preserve revocation behavior.",
                    citation_ids=["AUTH-1"],
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
        )
        conformance_result = PolicyConformanceResult(
            backend=request.backend,
            passed=True,
            overall_score=1.0,
            metrics=[
                PolicyConformanceMetric(
                    name="plan_completeness",
                    score=1.0,
                    passed=True,
                )
            ],
        )
        evidence_trace = PolicyEvidenceTrace(
            task=request.task,
            domain=request.domain,
            top_k=request.top_k,
            compiled_packet_id="packet-1",
            task_type="feature_work",
        )
        return PreflightRegenerationResult(
            request=request,
            passed=True,
            stop_reason="passed",
            compiled_packet_id="packet-1",
            final_result=final_result,
            final_conformance_result=conformance_result,
            evidence_trace=evidence_trace,
            attempts=[
                RegenerationAttempt(
                    attempt_index=0,
                    compiled_packet_id="packet-1",
                    triggers=[],
                    result=final_result,
                    conformance_result=conformance_result,
                    evidence_trace=evidence_trace,
                )
            ],
        )

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

    def __init__(self, payload: dict[str, object] | RuntimeEvidenceSessionSummary) -> None:
        self._payload = payload
        self.closed = False
        self.last_session_id: str | None = None

    def report_session(self, session_id: str) -> _MockJSONModel | RuntimeEvidenceSessionSummary:
        self.last_session_id = session_id
        if isinstance(self._payload, RuntimeEvidenceSessionSummary):
            return self._payload
        return _MockJSONModel(self._payload)

    def close(self) -> None:
        self.closed = True


class MockEvalService:
    """Static eval service for CLI tests."""

    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed
        self.launch_port: int | None = None
        self.started_ui = False
        self.published_result: EvalRunResult | None = None
        self.last_regenerate: bool | None = None
        self.last_max_regenerations: int | None = None

    def run(
        self,
        *,
        mode,
        backend: EvalBackend = "default",
        compare_rerank,
        regenerate: bool = False,
        max_regenerations: int = 1,
    ) -> EvalRunResult:
        self.last_regenerate = regenerate
        self.last_max_regenerations = max_regenerations
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

    def publish_to_ui(self, result: EvalRunResult, *, port: int | None = None) -> None:
        self.launch_port = port
        self.published_result = result


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

    def list_audit_events(
        self,
        *,
        github_login: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[BetaAuditEvent]:
        """Return static hosted beta audit events for CLI assertions."""
        if github_login == "missing-user":
            raise PolicyNIMError(
                "Hosted beta account with GitHub login 'missing-user' does not exist."
            )
        events = [
            BetaAuditEvent(
                event_id=2,
                account_id=1,
                github_login="octocat",
                account_status="active",
                event_type="api_key_rotated",
                details={"key_prefix": "pnm_current"},
                created_at=datetime(2026, 4, 5, 12, 5, tzinfo=UTC),
            ),
            BetaAuditEvent(
                event_id=1,
                account_id=1,
                github_login="octocat",
                account_status="active",
                event_type="account_signup",
                details={"github_login": "octocat", "email": "octocat@example.com"},
                created_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            ),
        ]
        if github_login is not None:
            events = [event for event in events if event.github_login == github_login]
        if event_type is not None:
            events = [event for event in events if event.event_type == event_type]
        return events[:limit]

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
    """Print ingest output and close the created service on success."""
    service = MockIngestService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_ingest_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 0
    assert "Indexed 24 chunks from 8 documents." in result.stdout
    assert "mock-model" in result.stdout
    assert service.closed is True


def test_ingest_command_surfaces_value_errors(monkeypatch) -> None:
    """Surface ingest construction failures as CLI errors."""
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_ingest_service",
        lambda settings: (_ for _ in ()).throw(ValueError("chunk/vector mismatch")),
    )

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 1
    assert "chunk/vector mismatch" in result.stderr


def test_ingest_command_closes_service_when_run_fails(monkeypatch) -> None:
    """Close the created ingest service when its run fails."""

    class FailingIngestService(MockIngestService):
        def run(self) -> IngestResult:
            """Raise a deterministic ingest failure."""
            raise ValueError("chunk/vector mismatch")

    service = FailingIngestService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_ingest_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 1
    assert "chunk/vector mismatch" in result.stderr
    assert service.closed is True


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
    assert "Phoenix" in runner.invoke(app, ["eval", "--help"]).stdout
    assert "Evidently" not in runner.invoke(app, ["eval", "--help"]).stdout
    assert service.started_ui is False
    assert service.published_result is None


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


def test_eval_command_passes_regeneration_options(monkeypatch) -> None:
    service = MockEvalService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        lambda settings: service,
    )

    result = runner.invoke(
        app,
        [
            "eval",
            "--mode",
            "offline",
            "--backend",
            "nemo",
            "--regenerate",
            "--max-regenerations",
            "2",
            "--headless",
        ],
    )

    assert result.exit_code == 0
    assert service.last_regenerate is True
    assert service.last_max_regenerations == 2


def test_eval_command_rejects_invalid_backend() -> None:
    result = runner.invoke(app, ["eval", "--backend", "not-a-backend", "--headless"])

    assert result.exit_code != 0
    assert "not-a-backend" in result.output


def test_eval_command_starts_ui_by_default(monkeypatch) -> None:
    service = MockEvalService()
    factory_calls: list[bool] = []

    def create_service(settings):
        factory_calls.append(True)
        return service

    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        create_service,
    )

    result = runner.invoke(app, ["eval"])

    assert result.exit_code == 0
    assert service.started_ui is True
    assert service.published_result is not None
    assert len(factory_calls) == 1


def test_eval_command_surfaces_ui_startup_failures(monkeypatch) -> None:
    error_cls = PolicyNIMError

    class FailingEvalService(MockEvalService):
        def start_ui(self, *, port: int | None = None) -> None:
            raise error_cls("Phoenix UI exited before startup completed.")

    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        lambda settings: FailingEvalService(),
    )

    result = runner.invoke(app, ["eval"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Phoenix UI exited before startup completed." in result.stderr


def test_eval_command_surfaces_ui_publishing_failures(monkeypatch) -> None:
    error_cls = PolicyNIMError

    class FailingEvalService(MockEvalService):
        def publish_to_ui(self, result: EvalRunResult, *, port: int | None = None) -> None:
            raise error_cls("Could not publish eval traces to Phoenix.")

    monkeypatch.setattr(
        "policynim.interfaces.cli.create_eval_service",
        lambda settings: FailingEvalService(),
    )

    result = runner.invoke(app, ["eval"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Could not publish eval traces to Phoenix." in result.stderr


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
    assert (
        payload.evidence_trace.chunks[0].text
        == "Retain revocation checks before deleting stale refresh tokens."
    )
    assert payload.evidence_trace.selected_policies[0].supporting_chunk_ids == ["AUTH-1"]
    assert payload.evidence_trace.constraints[0].constraint_id == "required_steps:0"
    assert payload.evidence_trace.output_links[0].chunk_ids == ["AUTH-1"]
    assert payload.evidence_trace.trace_steps[0].step_id == "compile"
    assert service.preflight_calls == 1
    assert service.trace_calls == 1
    assert service.closed is True


def test_preflight_regenerate_command_prints_regeneration_result(monkeypatch) -> None:
    service = MockPolicyRegenerationService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_regeneration_service",
        lambda settings, *, backend: service,
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
            "--regenerate",
            "--max-regenerations",
            "2",
            "--backend",
            "nat",
        ],
    )

    assert result.exit_code == 0
    payload = PreflightRegenerationResult.model_validate(json.loads(result.stdout))
    assert payload.request.task == "refresh token cleanup"
    assert payload.request.domain == "security"
    assert payload.request.top_k == 3
    assert payload.request.backend == "nat"
    assert payload.request.max_regenerations == 2
    assert payload.final_result.summary == "Regenerated policy guidance."
    assert payload.evidence_trace.compiled_packet_id == "packet-1"
    assert service.last_request is not None
    assert service.last_request.backend == "nat"
    assert service.last_request.include_chunk_text is False
    assert service.closed is True


def test_preflight_trace_regenerate_uses_regeneration_output(monkeypatch) -> None:
    service = MockPolicyRegenerationService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_policy_regeneration_service",
        lambda settings, *, backend: service,
    )

    result = runner.invoke(
        app,
        ["preflight", "--task", "refresh token cleanup", "--trace", "--regenerate"],
    )

    assert result.exit_code == 0
    payload = PreflightRegenerationResult.model_validate(json.loads(result.stdout))
    assert payload.evidence_trace.compiled_packet_id == "packet-1"
    assert service.last_request is not None
    assert service.last_request.include_chunk_text is True
    assert service.closed is True


def test_preflight_regenerate_rejects_invalid_max_regenerations() -> None:
    result = runner.invoke(
        app,
        [
            "preflight",
            "--task",
            "refresh token cleanup",
            "--regenerate",
            "--max-regenerations",
            "4",
        ],
    )

    assert result.exit_code != 0
    assert "4" in result.output


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
    """Print dump-index details and close the created service."""
    service = MockIndexDumpService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_index_dump_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["dump-index"])

    assert result.exit_code == 0
    assert "Indexed chunks: 1" in result.stdout
    assert "BACKEND-1" in result.stdout
    assert "Use request ids in backend logs." in result.stdout
    assert service.closed is True


def test_dump_index_count_only_prints_only_count(monkeypatch) -> None:
    """Print only the dump-index count and close the created service."""
    service = MockIndexDumpService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_index_dump_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["dump-index", "--count-only"])

    assert result.exit_code == 0
    assert result.stdout == "Indexed chunks: 1\n"
    assert service.closed is True


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
    assert "quickstart" in help_output
    assert "doctor" in help_output
    assert "mcp-config" in help_output
    assert "mcp-smoke" in help_output
    assert "support-bundle" in help_output
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


def test_doctor_reports_missing_standalone_setup_without_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Diagnose first-run setup before settings or provider construction."""
    _, config_root, _ = configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "action_required"
    assert payload["runtime_mode"] == "standalone"
    assert payload["config"]["expected_init_config_file"] == str(config_root / "config.env")
    assert payload["next_steps"] == [
        f"Run `policynim init` to create {config_root / 'config.env'}."
    ]
    assert "nvapi" not in result.stdout.lower()


def test_doctor_reports_ready_checkout_and_mcp_hints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Report safe local setup state without exposing configured secrets."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    index_path = checkout_root / "data" / "index.sqlite3"
    runtime_rules_path = checkout_root / "data" / "runtime" / "runtime_rules.json"
    write_ready_sqlite_index(index_path)
    runtime_rules_path.parent.mkdir(parents=True)
    runtime_rules_path.write_text("{}", encoding="utf-8")
    write_env_file(
        checkout_root / ".env",
        NVIDIA_API_KEY="nvapi-test-key",
        POLICYNIM_INDEX_DB_PATH="data/index.sqlite3",
        POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH="data/runtime/runtime_rules.json",
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["runtime_mode"] == "source_checkout"
    assert payload["config"]["active_config_file"] == str(checkout_root / ".env")
    assert payload["paths"]["index_db_path"] == str(index_path)
    assert payload["mcp"]["stdio_command"] == "uv run policynim mcp --transport stdio"
    assert payload["mcp"]["smoke_command"] == "uv run policynim mcp-smoke --format json"
    assert payload["mcp"]["local_stdio_config_commands"] == {
        "codex": "uv run policynim mcp-config --target local-stdio --client codex "
        f"--repo-root {checkout_root} --format json",
        "claude-code": (
            "uv run policynim mcp-config --target local-stdio --client claude-code "
            f"--repo-root {checkout_root} --format json"
        ),
    }
    assert payload["mcp"]["streamable_http_url"] == "http://127.0.0.1:8000/mcp"
    assert "nvapi-test-key" not in result.stdout


def test_doctor_source_checkout_recovery_uses_uv_run_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Give source-checkout users recovery commands that run in the uv project."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    write_env_file(checkout_root / ".env", NVIDIA_API_KEY="nvapi-test-key")

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "action_required"
    assert (
        "Run `uv run policynim ingest` to build the local policy index and runtime rules artifact."
    ) in payload["next_steps"]
    assert (
        "Run `policynim ingest` to build the local policy index and runtime rules artifact."
        not in payload["next_steps"]
    )


def test_doctor_flags_invalid_sqlite_index_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not report ready when the configured SQLite file is not a PolicyNIM index."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    index_path = checkout_root / "data" / "index.sqlite3"
    runtime_rules_path = checkout_root / "data" / "runtime" / "runtime_rules.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("placeholder", encoding="utf-8")
    runtime_rules_path.parent.mkdir(parents=True)
    runtime_rules_path.write_text("{}", encoding="utf-8")
    write_env_file(
        checkout_root / ".env",
        NVIDIA_API_KEY="nvapi-test-key",
        POLICYNIM_INDEX_DB_PATH="data/index.sqlite3",
        POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH="data/runtime/runtime_rules.json",
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "action_required"
    local_index_check = next(
        check for check in payload["checks"] if check["name"] == "local_index_path"
    )
    assert local_index_check == {
        "name": "local_index_path",
        "status": "action_required",
        "message": (
            "Configured local SQLite index file is not a populated PolicyNIM sqlite-vec index."
        ),
    }
    assert payload["next_steps"] == [
        (
            "Run `uv run policynim ingest` to build the local policy index "
            "and runtime rules artifact."
        )
    ]


def test_doctor_surfaces_unreadable_sqlite_index_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Report unreadable SQLite indexes as permission/path issues, not missing data."""

    class UnreadableDoctorIndexStore:
        """Doctor-only index-store stub with an unreadable readiness state."""

        def inspect_readiness(self) -> IndexReadinessReport:
            """Return an unreadable readiness report for doctor assertions."""
            return IndexReadinessReport(
                state="unreadable",
                error=PermissionError(13, "permission denied"),
            )

    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    runtime_rules_path = checkout_root / "data" / "runtime" / "runtime_rules.json"
    runtime_rules_path.parent.mkdir(parents=True)
    runtime_rules_path.write_text("{}", encoding="utf-8")
    write_env_file(
        checkout_root / ".env",
        NVIDIA_API_KEY="nvapi-test-key",
        POLICYNIM_INDEX_DB_PATH="data/index.sqlite3",
        POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH="data/runtime/runtime_rules.json",
    )
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_index_store",
        lambda settings: UnreadableDoctorIndexStore(),
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    local_index_check = next(
        check for check in payload["checks"] if check["name"] == "local_index_path"
    )
    assert local_index_check["status"] == "action_required"
    assert "could not be read" in local_index_check["message"]
    assert "PermissionError: permission denied" in local_index_check["message"]
    assert (
        "Fix the local SQLite index file permissions or point "
        "`POLICYNIM_INDEX_DB_PATH` at a readable SQLite file, then run "
        "`uv run policynim ingest`."
    ) in payload["next_steps"]


def test_doctor_flags_legacy_lancedb_directory_index_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Guide sqlite-vec upgrades away from old LanceDB directory paths."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    legacy_index_dir = checkout_root / "data" / "lancedb"
    legacy_index_dir.mkdir(parents=True)
    write_env_file(
        checkout_root / ".env",
        NVIDIA_API_KEY="nvapi-test-key",
        POLICYNIM_LANCEDB_URI="data/lancedb",
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "action_required"
    local_index_check = next(
        check for check in payload["checks"] if check["name"] == "local_index_path"
    )
    assert local_index_check == {
        "name": "local_index_path",
        "status": "action_required",
        "message": (
            "Configured local SQLite index path points to a directory. "
            "Set POLICYNIM_INDEX_DB_PATH to a SQLite file path such as data/index.sqlite3."
        ),
    }
    assert (
        "Replace deprecated `POLICYNIM_LANCEDB_URI` with "
        "`POLICYNIM_INDEX_DB_PATH=data/index.sqlite3`, then run `uv run policynim ingest`."
    ) in payload["next_steps"]
    assert (
        "Run `uv run policynim ingest` to build the local policy index and runtime rules artifact."
        not in payload["next_steps"]
    )


def test_doctor_flags_canonical_directory_index_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Tell users that sqlite-vec needs a file path, not a directory."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    index_dir = checkout_root / "data" / "index-dir"
    index_dir.mkdir(parents=True)
    write_env_file(
        checkout_root / ".env",
        NVIDIA_API_KEY="nvapi-test-key",
        POLICYNIM_INDEX_DB_PATH="data/index-dir",
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "action_required"
    local_index_check = next(
        check for check in payload["checks"] if check["name"] == "local_index_path"
    )
    assert local_index_check["status"] == "action_required"
    assert "points to a directory" in local_index_check["message"]
    assert (
        "Set `POLICYNIM_INDEX_DB_PATH=data/index.sqlite3`, then run `uv run policynim ingest`."
    ) in payload["next_steps"]
    assert "POLICYNIM_LANCEDB_URI" not in " ".join(payload["next_steps"])


def test_doctor_reports_installed_mcp_config_commands_without_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep installed no-clone MCP setup discoverable from doctor output."""
    _, config_root, _ = configure_standalone_cli_environment(monkeypatch, tmp_path)
    write_env_file(config_root / "config.env", NVIDIA_API_KEY="nvapi-test-key")

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["runtime_mode"] == "standalone"
    assert payload["mcp"]["smoke_command"] == "policynim mcp-smoke --format json"
    assert payload["mcp"]["local_stdio_config_commands"] == {
        "codex": "policynim mcp-config --target local-stdio --client codex --format json",
        "claude-code": (
            "policynim mcp-config --target local-stdio --client claude-code --format json"
        ),
    }
    assert "--repo-root" not in json.dumps(payload["mcp"])
    assert "uv run" not in json.dumps(payload["mcp"])
    assert "nvapi-test-key" not in result.stdout


def test_doctor_text_output_is_human_readable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep the default diagnostic useful in a terminal."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "PolicyNIM doctor" in result.stdout
    assert "Status: action_required" in result.stdout
    assert "Next steps:" in result.stdout
    assert "policynim init" in result.stdout


def test_doctor_text_output_prints_mcp_config_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Make local MCP client setup discoverable from the default diagnostic."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    write_env_file(checkout_root / ".env", NVIDIA_API_KEY="nvapi-test-key")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "MCP:" in result.stdout
    assert "policynim mcp-smoke --format json" in result.stdout
    assert (
        "uv run policynim mcp-config --target local-stdio --client codex "
        f"--repo-root {checkout_root} --format json"
    ) in result.stdout
    assert (
        "uv run policynim mcp-config --target local-stdio --client claude-code "
        f"--repo-root {checkout_root} --format json"
    ) in result.stdout


def test_doctor_mcp_config_commands_quote_checkout_paths_with_spaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep doctor's copyable MCP setup commands valid for spaced checkout paths."""
    clear_installer_env(monkeypatch)
    checkout_root = tmp_path / "checkout with spaces"
    package_root = checkout_root / "src" / "policynim"
    package_root.mkdir(parents=True)
    (checkout_root / "pyproject.toml").write_text(
        "[project]\nname = 'policynim'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(checkout_root)
    monkeypatch.setattr(
        "policynim.config_discovery.user_config_path",
        lambda *args, **kwargs: tmp_path / "user-config",
    )
    monkeypatch.setattr(
        "policynim.config_discovery.user_data_path",
        lambda *args, **kwargs: tmp_path / "user-data",
    )
    monkeypatch.setattr(
        "policynim.config_discovery.__file__",
        str(package_root / "config_discovery.py"),
    )
    write_env_file(checkout_root / ".env", NVIDIA_API_KEY="nvapi-test-key")

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    commands = payload["mcp"]["local_stdio_config_commands"]
    assert f"--repo-root '{checkout_root}' --format json" in commands["codex"]
    assert f"--repo-root '{checkout_root}' --format json" in commands["claude-code"]


def test_support_bundle_reports_redacted_first_run_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Collect issue-ready diagnostics without requiring completed setup."""
    _, config_root, _ = configure_standalone_cli_environment(monkeypatch, tmp_path)
    monkeypatch.setattr("policynim.interfaces.cli._resolve_installed_version", lambda: "1.2.3")

    result = runner.invoke(app, ["support-bundle"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["policynim_version"] == "1.2.3"
    assert payload["python"]["version"]
    assert payload["platform"]["system"]
    assert payload["first_run"]["runtime_mode"] == "standalone"
    assert payload["first_run"]["default_target"] == "hosted-mcp"
    assert payload["first_run"]["targets"]["hosted_mcp"]["target"] == "hosted-mcp"
    assert payload["first_run"]["targets"]["hosted_mcp"]["requires_local_setup"] is False
    assert payload["first_run"]["targets"]["hosted_mcp"]["hosted_url"] == (
        "https://<railway-domain>/mcp"
    )
    assert payload["first_run"]["targets"]["hosted_mcp"]["beta_portal_url"] == (
        "https://<railway-domain>/beta"
    )
    assert payload["first_run"]["targets"]["hosted_mcp"]["hosted_url_placeholder"] is True
    assert payload["first_run"]["targets"]["local_cli"]["requires_local_setup"] is True
    assert payload["first_run"]["targets"]["local_mcp"]["local_launch_mode"] == "installed-cli"
    assert payload["first_run"]["targets"]["hosted_mcp"]["quickstart_command"] == (
        "policynim quickstart --target hosted-mcp --format json"
    )
    assert payload["first_run"]["targets"]["hosted_mcp"]["agent_workflows"][0]["tool"] == (
        "policy_preflight"
    )
    assert (
        "policy_search"
        in (payload["first_run"]["targets"]["local_mcp"]["agent_workflows"][1]["prompt"])
    )
    assert (
        "policynim mcp-config --target hosted-http"
        in (payload["first_run"]["targets"]["hosted_mcp"]["commands"][1])
    )
    assert payload["first_run"]["targets"]["hosted_mcp"]["client_commands"] == [
        (
            "codex mcp add policynim --url 'https://<railway-domain>/mcp' "
            "--bearer-token-env-var POLICYNIM_TOKEN"
        ),
        (
            "claude mcp add --transport http policynim 'https://<railway-domain>/mcp' "
            '--header "Authorization: Bearer $POLICYNIM_TOKEN"'
        ),
    ]
    assert payload["doctor"]["status"] == "action_required"
    assert payload["python"]["executable"] == "<python-executable>"
    assert payload["doctor"]["config"]["expected_init_config_file"] == ("<config-dir>/config.env")
    assert payload["redaction"]["local_paths"] == "redacted"
    assert payload["redaction"]["path_markers"] == [
        "<config-dir>",
        "<data-dir>",
        "<home>",
        "<python-executable>",
    ]
    assert payload["mcp_smoke"] == {
        "status": "skipped",
        "reason": "Pass --include-mcp-smoke to verify stdio tool registration.",
    }
    assert str(tmp_path) not in result.stdout
    assert "<repo-root>" not in result.stdout
    assert "nvapi" not in result.stdout.lower()


def test_support_bundle_does_not_label_installed_cwd_as_repo_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Avoid treating an arbitrary installed-runtime CWD as a source checkout."""
    workspace, _, _ = configure_standalone_cli_environment(monkeypatch, tmp_path)
    monkeypatch.setattr("policynim.interfaces.cli._resolve_installed_version", lambda: "1.2.3")

    result = runner.invoke(app, ["support-bundle"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["first_run"]["runtime_mode"] == "standalone"
    assert "<repo-root>" not in payload["redaction"]["path_markers"]
    assert str(workspace) not in result.stdout
    assert "workspace" not in result.stdout


def test_support_bundle_redacts_custom_absolute_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Redact absolute runtime path overrides outside default config roots."""
    _, config_root, _ = configure_standalone_cli_environment(monkeypatch, tmp_path)
    custom_runtime = tmp_path / "external-runtime"
    custom_index = custom_runtime / "index.sqlite3"
    write_env_file(
        config_root / "config.env",
        NVIDIA_API_KEY="nvapi-test-key",
        POLICYNIM_INDEX_DB_PATH=str(custom_index),
    )
    monkeypatch.setattr("policynim.interfaces.cli._resolve_installed_version", lambda: "1.2.3")

    result = runner.invoke(app, ["support-bundle"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["doctor"]["paths"]["index_db_path"] == "<local-path>/index.sqlite3"
    assert "<local-path>" in payload["redaction"]["path_markers"]
    assert str(custom_runtime) not in result.stdout
    assert str(custom_index) not in result.stdout
    assert "nvapi" not in result.stdout.lower()


def test_support_bundle_can_include_local_paths_for_private_triage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Let maintainers opt into exact paths when support happens privately."""
    _, config_root, _ = configure_standalone_cli_environment(monkeypatch, tmp_path)
    monkeypatch.setattr("policynim.interfaces.cli._resolve_installed_version", lambda: "1.2.3")

    result = runner.invoke(app, ["support-bundle", "--include-local-paths"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["python"]["executable"] == sys.executable
    assert payload["doctor"]["config"]["expected_init_config_file"] == str(
        config_root / "config.env"
    )
    assert payload["redaction"]["local_paths"] == "included"
    assert str(config_root) in result.stdout
    assert "nvapi" not in result.stdout.lower()


def test_support_bundle_can_include_mcp_smoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Let issue reporters include local MCP launch evidence on demand."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    write_env_file(checkout_root / ".env", NVIDIA_API_KEY="nvapi-test-key")
    monkeypatch.setattr("policynim.interfaces.cli._resolve_installed_version", lambda: "1.2.3")

    async def fake_smoke(*, timeout_seconds: float) -> dict[str, object]:
        """Return successful MCP smoke evidence for support-bundle output."""
        assert timeout_seconds == 7.0
        return {
            "status": "ok",
            "transport": "stdio",
            "command": [sys.executable, "-m", "policynim.interfaces.cli", "mcp"],
            "tools": ["policy_preflight", "policy_search"],
            "missing_tools": [],
        }

    monkeypatch.setattr("policynim.interfaces.cli._run_mcp_stdio_smoke", fake_smoke)

    result = runner.invoke(
        app,
        ["support-bundle", "--include-mcp-smoke", "--mcp-timeout-seconds", "7"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["doctor"]["runtime_mode"] == "source_checkout"
    assert payload["first_run"]["runtime_mode"] == "source_checkout"
    assert payload["first_run"]["targets"]["hosted_mcp"]["quickstart_command"] == (
        "policynim quickstart --target hosted-mcp --format json"
    )
    assert payload["first_run"]["targets"]["hosted_mcp"]["commands"][1].startswith(
        "policynim mcp-config --target hosted-http"
    )
    assert "uv run" not in payload["first_run"]["targets"]["hosted_mcp"]["commands"][1]
    assert payload["first_run"]["targets"]["local_mcp"]["local_launch_mode"] == ("source-checkout")
    assert "<repo-root>" in " ".join(payload["first_run"]["targets"]["local_mcp"]["commands"])
    assert payload["mcp_smoke"]["status"] == "ok"
    assert payload["mcp_smoke"]["command"][0] == "<python-executable>"
    assert payload["mcp_smoke"]["tools"] == ["policy_preflight", "policy_search"]
    assert str(checkout_root) not in result.stdout
    assert "nvapi-test-key" not in result.stdout


def test_support_bundle_markdown_wraps_json_for_issues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Provide a paste-friendly Markdown form for issue bodies."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)
    monkeypatch.setattr("policynim.interfaces.cli._resolve_installed_version", lambda: "1.2.3")

    result = runner.invoke(app, ["support-bundle", "--format", "markdown"])

    assert result.exit_code == 0
    assert "## PolicyNIM Support Bundle" in result.stdout
    assert "```json" in result.stdout
    assert '"schema_version": "1"' in result.stdout


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
        f"POLICYNIM_INDEX_DB_PATH='{data_root / 'index.sqlite3'}'\n"
        f"POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH='{data_root / 'runtime' / 'runtime_rules.json'}'\n"
        "POLICYNIM_RUNTIME_EVIDENCE_DB_PATH="
        f"'{data_root / 'runtime' / 'runtime_evidence.sqlite3'}'\n"
        f"POLICYNIM_EVAL_WORKSPACE_DIR='{data_root / 'evals' / 'workspace'}'\n"
    )


def test_init_command_writes_checkout_dotenv_without_standalone_path_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Write checkout env config without standalone data-path defaults."""
    checkout_root, config_root, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["init"], input="nvapi-test-key\n\n")

    dotenv_path = checkout_root / ".env"
    assert result.exit_code == 0
    assert str(dotenv_path) in result.stdout
    assert dotenv_path.read_text(encoding="utf-8") == "NVIDIA_API_KEY='nvapi-test-key'\n"
    assert not (config_root / "config.env").exists()


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


def test_quickstart_defaults_to_hosted_mcp_without_requiring_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Show the shortest no-clone path before local config exists."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["quickstart", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["target"] == "hosted-mcp"
    assert payload["client"] == "codex"
    assert payload["requires_local_setup"] is False
    assert payload["calls_external_services"] is False
    assert payload["hosted_url"] == "https://<railway-domain>/mcp"
    assert payload["hosted_url_placeholder"] is True
    assert payload["steps"][0] == (
        "Open https://<railway-domain>/mcp in a browser; it routes to "
        "https://<railway-domain>/beta for token creation."
    )
    assert "POLICYNIM_TOKEN" in " ".join(payload["commands"])
    assert "export POLICYNIM_TOKEN='<generated-beta-token>'" in payload["commands"]
    assert "export POLICYNIM_TOKEN=<generated-beta-token>" not in payload["commands"]
    assert payload["client_commands"] == [
        (
            "codex mcp add policynim --url 'https://<railway-domain>/mcp' "
            "--bearer-token-env-var POLICYNIM_TOKEN"
        )
    ]
    assert "<generated-beta-token>" not in " ".join(payload["client_commands"])
    assert "Replace the hosted URL placeholder" in " ".join(payload["next_steps"])
    assert "policy_preflight" in " ".join(payload["next_steps"])
    agent_workflows = payload["agent_workflows"]
    assert [workflow["title"] for workflow in agent_workflows] == [
        "Preflight before implementation",
        "Retrieve policy evidence while debugging",
        "Verify MCP tool availability",
    ]
    assert agent_workflows[0]["tool"] == "policy_preflight"
    assert "Before editing, call policy_preflight" in agent_workflows[0]["prompt"]
    assert "cited constraints" in agent_workflows[0]["prompt"]
    assert "insufficient_context" in agent_workflows[0]["prompt"]
    assert agent_workflows[1]["tool"] == "policy_search"
    assert (
        "Use policy_search for: release installer checksum verification."
        in (agent_workflows[1]["prompt"])
    )
    assert "cited policy lines" in agent_workflows[1]["prompt"]
    assert "policy_preflight and policy_search" in agent_workflows[2]["prompt"]
    assert "before starting implementation" in agent_workflows[2]["prompt"]


def test_quickstart_text_renders_agent_workflows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Make the human-readable first-run output show copyable agent prompts."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["quickstart"])

    assert result.exit_code == 0
    output = unstyle(result.stdout)
    assert "Agent workflows:" in output
    assert "- Preflight before implementation: Before editing, call policy_preflight" in output
    assert "- Retrieve policy evidence while debugging: Use policy_search for:" in output
    assert "insufficient_context" in output
    assert "cited policy lines" in output
    assert "- Verify MCP tool availability: List the PolicyNIM MCP tools" in output


def test_quickstart_text_renders_hosted_client_setup_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Make hosted first-run output copy-pasteable without opening extra docs."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["quickstart"])

    assert result.exit_code == 0
    output = unstyle(result.stdout)
    assert "Client setup:" in output
    assert (
        "Open https://<railway-domain>/mcp in a browser; it routes to "
        "https://<railway-domain>/beta for token creation."
    ) in output
    assert (
        "codex mcp add policynim --url 'https://<railway-domain>/mcp' "
        "--bearer-token-env-var POLICYNIM_TOKEN"
    ) in output
    assert "<generated-beta-token>" not in output.split("Client setup:", maxsplit=1)[1]


def test_quickstart_text_points_to_alternate_hosted_mcp_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Make default quickstart self-guiding for non-default MCP clients."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["quickstart"])

    assert result.exit_code == 0
    output = unstyle(result.stdout)
    assert (
        "For Claude Code setup commands, rerun "
        "`policynim quickstart --target hosted-mcp --client claude-code`."
    ) in output


def test_quickstart_uses_installed_entrypoint_for_hosted_config_from_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep the hosted path no-clone even when quickstart runs from a checkout."""
    configure_checkout_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["quickstart", "--target", "hosted-mcp", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "policynim mcp-config --target hosted-http --client codex" in payload["commands"][1]
    assert payload["commands"][1].startswith("policynim mcp-config")
    assert "uv run" not in payload["commands"][1]
    assert "uv run" not in " ".join(payload["next_steps"])
    assert "policynim quickstart --target hosted-mcp --client claude-code" in " ".join(
        payload["next_steps"]
    )
    assert payload["requires_local_setup"] is False


def test_quickstart_derives_beta_portal_from_hosted_mcp_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep hosted first-run guidance concrete when operators pass a real /mcp URL."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "quickstart",
            "--target",
            "hosted-mcp",
            "--hosted-url",
            "https://policy.policynim.dev/mcp",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["hosted_url_placeholder"] is False
    assert payload["hosted_url"] == "https://policy.policynim.dev/mcp"
    assert payload["beta_portal_url"] == "https://policy.policynim.dev/beta"
    assert payload["steps"][0] == (
        "Open https://policy.policynim.dev/mcp in a browser; it routes to "
        "https://policy.policynim.dev/beta for token creation."
    )
    assert payload["client_commands"] == [
        (
            "codex mcp add policynim --url https://policy.policynim.dev/mcp "
            "--bearer-token-env-var POLICYNIM_TOKEN"
        )
    ]
    assert "https://<railway-domain>/beta" not in payload["steps"]
    assert "Replace the hosted URL placeholder" not in " ".join(payload["next_steps"])


def test_quickstart_prints_claude_hosted_client_setup_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Honor --client when printing the hosted MCP command to paste."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "quickstart",
            "--target",
            "hosted-mcp",
            "--client",
            "claude-code",
            "--hosted-url",
            "https://policy.policynim.dev/mcp",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["client"] == "claude-code"
    assert payload["client_commands"] == [
        (
            "claude mcp add --transport http policynim https://policy.policynim.dev/mcp "
            '--header "Authorization: Bearer $POLICYNIM_TOKEN"'
        )
    ]
    assert "<generated-beta-token>" not in payload["client_commands"][0]


def test_quickstart_text_points_claude_users_back_to_codex_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep the alternate-client hint symmetric when Claude Code is selected."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["quickstart", "--client", "claude-code"])

    assert result.exit_code == 0
    output = unstyle(result.stdout)
    assert (
        "For Codex setup commands, rerun `policynim quickstart --target hosted-mcp --client codex`."
    ) in output


def test_quickstart_rejects_hosted_url_that_is_not_mcp_endpoint() -> None:
    """Do not print hosted first-run commands that cannot work in MCP clients."""
    result = runner.invoke(
        app,
        ["quickstart", "--target", "hosted-mcp", "--hosted-url", "https://policy.example"],
    )

    assert result.exit_code == 1
    assert "Hosted MCP URL must point to the /mcp endpoint." in result.stderr


def test_quickstart_prints_current_pypi_local_cli_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep installed CLI first-run guidance aligned with public package state."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["quickstart", "--target", "local-cli", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    prerequisites = " ".join(payload["prerequisites"])
    assert payload["target"] == "local-cli"
    assert "Python 3.11 or 3.12 for PyPI package installs." in payload["prerequisites"]
    assert "after publication" not in prerequisites
    assert "trusted-publishing evidence is tracked separately" in prerequisites
    assert "policynim quickstart" in " ".join(payload["next_steps"])
    assert "policynim support-bundle" in " ".join(payload["next_steps"])
    assert "attaching public setup evidence" in " ".join(payload["next_steps"])
    assert "doctor --format json` local" in " ".join(payload["next_steps"])


def test_quickstart_uses_source_checkout_entrypoint_for_local_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep local CLI first-run commands runnable from a source checkout."""
    configure_checkout_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["quickstart", "--target", "local-cli", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["description"] == "Local CLI path for running preflight from a source checkout."
    assert "PolicyNIM source checkout with uv dependencies synced." in payload["prerequisites"]
    assert "PyPI package installs" not in " ".join(payload["prerequisites"])
    assert "Check the source-checkout entrypoint." in payload["steps"]
    assert "installed entrypoint" not in " ".join(payload["steps"])
    commands = payload["commands"]
    assert commands[:4] == [
        "uv run policynim --help",
        "uv run policynim doctor",
        "uv run policynim init",
        "uv run policynim ingest",
    ]
    assert commands[4].startswith("uv run policynim preflight --task")
    assert "Run `uv run policynim quickstart --target hosted-mcp`" in (
        " ".join(payload["next_steps"])
    )
    assert "uv run policynim support-bundle" in " ".join(payload["next_steps"])


def test_quickstart_prints_local_mcp_path_with_source_checkout_hint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Guide local MCP users through doctor, ingest, smoke, and config generation."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "quickstart",
            "--target",
            "local-mcp",
            "--client",
            "claude-code",
            "--repo-root",
            str(checkout_root),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["target"] == "local-mcp"
    assert payload["client"] == "claude-code"
    assert payload["local_launch_mode"] == "source-checkout"
    assert payload["description"] == (
        "Local MCP path for Codex or Claude Code from a source checkout."
    )
    assert "PolicyNIM source checkout with uv dependencies synced." in payload["prerequisites"]
    assert "installed PolicyNIM CLI" not in " ".join(payload["prerequisites"])
    assert payload["requires_local_setup"] is True
    assert "Generate client config from the checkout path." in payload["steps"]
    assert "uv run policynim doctor" in payload["commands"]
    assert "uv run policynim init" in payload["commands"]
    assert "uv run policynim ingest" in payload["commands"]
    assert "uv run policynim mcp-smoke" in payload["commands"]
    assert "uv run policynim mcp-config --target local-stdio --client claude-code" in " ".join(
        payload["commands"]
    )
    assert "policynim mcp-smoke" not in payload["commands"]
    assert f"--repo-root {checkout_root}" in " ".join(payload["commands"])
    assert "NVIDIA_API_KEY" in " ".join(payload["prerequisites"])
    assert "exact local filesystem paths" in " ".join(payload["safety"])
    assert "policynim support-bundle" in " ".join(payload["safety"])


def test_quickstart_prints_installed_local_mcp_path_without_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Guide installed users to local stdio MCP without requiring a source checkout."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "quickstart",
            "--target",
            "local-mcp",
            "--client",
            "codex",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["target"] == "local-mcp"
    assert payload["local_launch_mode"] == "installed-cli"
    assert "installed PolicyNIM CLI" in " ".join(payload["prerequisites"])
    assert "Generate client config from the installed entrypoint." in payload["steps"]
    assert "checkout path" not in " ".join(payload["steps"])
    assert "policynim mcp-config --target local-stdio --client codex" in payload["commands"]
    assert "--repo-root" not in " ".join(payload["commands"])
    assert "policynim support-bundle" in " ".join(payload["safety"])


def test_quickstart_help_mentions_targets_clients_and_json_output() -> None:
    """Keep help discoverable for installed users starting from --help."""
    result = runner.invoke(app, ["quickstart", "--help"])
    help_output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "Print the first-run path" in help_output
    assert "--target" in help_output
    assert "hosted-mcp" in help_output
    assert "local-cli" in help_output
    assert "local-mcp" in help_output
    assert "--client" in help_output
    assert "--format" in help_output


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
        (["mcp", "--transport", "streamable-http"], None),
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


def test_mcp_stdio_launch_does_not_require_completed_standalone_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Let stdio MCP launch for client discovery before API-key setup."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)
    launched: list[str] = []

    def fake_run_server(*, transport: str) -> None:
        """Record the selected MCP transport without starting a server."""
        launched.append(transport)

    monkeypatch.setattr("policynim.interfaces.cli.run_server", fake_run_server)

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 0
    assert launched == ["stdio"]


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
                "Hosted streamable-http startup requires a populated local SQLite index at "
                "/app/data/index.sqlite3 (table: policy_chunks). "
                "Automatic hosted-index rebuild failed: ConfigurationError: "
                "NVIDIA_API_KEY is required for embeddings. "
                "Run `policynim ingest` before serving traffic, or bake that command "
                "during Docker build. Configure the path with `POLICYNIM_INDEX_DB_PATH`; "
                "`POLICYNIM_LANCEDB_URI` is only a deprecated alias for that path."
            )
        ),
    )

    result = runner.invoke(app, ["mcp", "--transport", "streamable-http"])

    assert result.exit_code == 1
    assert "NVIDIA_API_KEY is required for embeddings." in result.stderr
    assert "Configure the path with `POLICYNIM_INDEX_DB_PATH`" in result.stderr


def test_mcp_config_prints_claude_code_json_without_secret_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generate a project-scoped Claude Code config from a verified checkout path."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-secret-value")

    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--client",
            "claude-code",
            "--repo-root",
            str(checkout_root),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    server = payload["config"]["mcpServers"]["policynim"]
    assert payload["client"] == "claude-code"
    assert payload["repo_root"] == str(checkout_root)
    assert server == {
        "type": "stdio",
        "command": "uv",
        "args": [
            "run",
            "--directory",
            str(checkout_root),
            "policynim",
            "mcp",
            "--transport",
            "stdio",
        ],
        "env": {"NVIDIA_API_KEY": "${NVIDIA_API_KEY}"},
    }
    assert "uv run policynim doctor" in payload["next_steps"]
    assert "uv run policynim mcp-smoke" in payload["next_steps"]
    assert "exact local filesystem paths" in " ".join(payload["safety"])
    assert "policynim support-bundle" in " ".join(payload["safety"])
    assert "nvapi-secret-value" not in result.stdout


def test_mcp_config_prints_codex_installed_stdio_json_without_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generate no-clone local MCP config from an installed CLI entrypoint."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--target",
            "local-stdio",
            "--client",
            "codex",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["client"] == "codex"
    assert payload["target"] == "local-stdio"
    assert payload["local_launch_mode"] == "installed-cli"
    assert "repo_root" not in payload
    assert payload["codex_cli_command"] == [
        "codex",
        "mcp",
        "add",
        "policynim",
        "--env",
        "NVIDIA_API_KEY=$NVIDIA_API_KEY",
        "--",
        "policynim",
        "mcp",
        "--transport",
        "stdio",
    ]
    assert payload["codex_app"] == {
        "name": "policynim",
        "transport": "STDIO",
        "command": "policynim",
        "arguments": ["mcp", "--transport", "stdio"],
        "environment_variable_passthrough": ["NVIDIA_API_KEY"],
    }


def test_mcp_config_prints_claude_installed_stdio_json_without_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generate Claude Code local MCP config from an installed CLI entrypoint."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--target",
            "local-stdio",
            "--client",
            "claude-code",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    server = payload["config"]["mcpServers"]["policynim"]
    assert payload["client"] == "claude-code"
    assert payload["local_launch_mode"] == "installed-cli"
    assert "repo_root" not in payload
    assert server == {
        "type": "stdio",
        "command": "policynim",
        "args": ["mcp", "--transport", "stdio"],
        "env": {"NVIDIA_API_KEY": "${NVIDIA_API_KEY}"},
    }


def test_mcp_config_prints_codex_cli_and_app_guidance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generate Codex CLI and app setup guidance from the same stdio contract."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--client",
            "codex",
            "--repo-root",
            str(checkout_root),
        ],
    )

    assert result.exit_code == 0
    assert "PolicyNIM MCP config for Codex" in result.stdout
    assert "codex mcp add policynim" in result.stdout
    assert "--env NVIDIA_API_KEY=$NVIDIA_API_KEY --" in result.stdout
    assert f"uv run --directory {checkout_root} policynim mcp --transport stdio" in result.stdout
    assert "Command to launch: uv" in result.stdout
    assert f"Working directory: {checkout_root}" in result.stdout
    assert "Environment variable passthrough: NVIDIA_API_KEY" in result.stdout
    assert "Safety:" in result.stdout
    assert "exact local filesystem paths" in result.stdout
    assert "policynim support-bundle" in result.stdout


def test_mcp_config_prints_codex_hosted_http_json_without_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generate hosted Codex setup without requiring a local source checkout."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--target",
            "hosted-http",
            "--client",
            "codex",
            "--hosted-url",
            "https://policy.policynim.dev/mcp",
            "--bearer-token-env-var",
            "POLICYNIM_TOKEN",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["target"] == "hosted-http"
    assert payload["client"] == "codex"
    assert payload["hosted_url"] == "https://policy.policynim.dev/mcp"
    assert payload["beta_portal_url"] == "https://policy.policynim.dev/beta"
    assert payload["hosted_url_placeholder"] is False
    assert payload["bearer_token_env_var"] == "POLICYNIM_TOKEN"
    assert "repo_root" not in payload
    assert payload["codex_cli_command"] == [
        "codex",
        "mcp",
        "add",
        "policynim",
        "--url",
        "https://policy.policynim.dev/mcp",
        "--bearer-token-env-var",
        "POLICYNIM_TOKEN",
    ]
    assert payload["codex_cli_shell_command"] == (
        "codex mcp add policynim --url https://policy.policynim.dev/mcp "
        "--bearer-token-env-var POLICYNIM_TOKEN"
    )
    assert "Export POLICYNIM_TOKEN='<generated-beta-token>'" in payload["next_steps"]
    assert "policy_preflight" in " ".join(payload["next_steps"])


def test_mcp_config_marks_placeholder_hosted_url_without_failing() -> None:
    """Keep offline placeholder smokes possible while warning users before setup."""
    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--target",
            "hosted-http",
            "--client",
            "codex",
            "--hosted-url",
            "https://<railway-domain>/mcp",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["hosted_url"] == "https://<railway-domain>/mcp"
    assert payload["beta_portal_url"] == "https://<railway-domain>/beta"
    assert payload["hosted_url_placeholder"] is True
    assert "Replace the hosted URL placeholder" in " ".join(payload["next_steps"])
    assert "policy_preflight" in " ".join(payload["next_steps"])


def test_mcp_config_prints_claude_hosted_http_guidance() -> None:
    """Generate hosted Claude Code setup from the same MCP URL contract."""
    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--target",
            "hosted-http",
            "--client",
            "claude-code",
            "--hosted-url",
            "https://policy.policynim.dev/mcp",
        ],
    )

    assert result.exit_code == 0
    assert "PolicyNIM hosted MCP config for Claude Code" in result.stdout
    assert (
        "claude mcp add --transport http policynim https://policy.policynim.dev/mcp "
        '--header "Authorization: Bearer $POLICYNIM_TOKEN"'
    ) in result.stdout
    assert "Export POLICYNIM_TOKEN='<generated-beta-token>'" in result.stdout
    assert "policy_search" in result.stdout


def test_mcp_config_rejects_hosted_http_without_url() -> None:
    """Hosted config should fail before printing unusable client commands."""
    result = runner.invoke(app, ["mcp-config", "--target", "hosted-http"])

    assert result.exit_code == 1
    assert "Hosted MCP config requires --hosted-url" in result.stderr


def test_mcp_config_rejects_non_http_hosted_url() -> None:
    """Reject hosted config URLs that MCP clients cannot use as HTTP endpoints."""
    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--target",
            "hosted-http",
            "--hosted-url",
            "file:///tmp/mcp",
        ],
    )

    assert result.exit_code == 1
    assert "Hosted MCP URL must start with http:// or https://" in result.stderr


def test_mcp_config_rejects_hosted_url_userinfo() -> None:
    """Reject hosted URLs that could echo embedded credentials into setup output."""
    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--target",
            "hosted-http",
            "--hosted-url",
            "https://user:pass@policy.example/mcp",
        ],
    )

    assert result.exit_code == 1
    assert "Hosted MCP URL must not include embedded credentials." in result.stderr
    assert "user:pass" not in result.stdout


def test_mcp_config_rejects_hosted_http_with_local_only_options(tmp_path: Path) -> None:
    """Avoid silently ignoring local stdio flags for hosted config."""
    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--target",
            "hosted-http",
            "--hosted-url",
            "https://policy.example/mcp",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "--repo-root is only valid with --target local-stdio" in result.stderr


def test_mcp_config_rejects_local_stdio_with_hosted_only_options() -> None:
    """Avoid silently ignoring hosted HTTP flags for local stdio config."""
    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--target",
            "local-stdio",
            "--hosted-url",
            "https://policy.example/mcp",
        ],
    )

    assert result.exit_code == 1
    assert "--hosted-url is only valid with --target hosted-http" in result.stderr


def test_mcp_config_rejects_uv_command_without_source_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Avoid silently ignoring source-checkout launch flags in installed mode."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    result = runner.invoke(app, ["mcp-config", "--uv-command", "/opt/uv"])

    assert result.exit_code == 1
    assert "--uv-command requires a PolicyNIM source checkout" in result.stderr
    assert "Pass --repo-root /ABS/PATH/TO/policyNIM" in result.stderr


def test_mcp_config_help_mentions_hosted_http_options() -> None:
    """Keep CLI help aligned with hosted-first docs."""
    result = runner.invoke(app, ["mcp-config", "--help"])
    help_output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "hosted HTTP or local stdio MCP client config" in help_output
    assert "--target" in help_output
    assert "local-stdio" in help_output
    assert "hosted-http" in help_output
    assert "--hosted-url" in help_output
    assert "--bearer-token-env-var" in help_output


def test_mcp_config_rejects_non_checkout_repo_root(tmp_path: Path) -> None:
    """Avoid emitting client config that points at an arbitrary directory."""
    not_checkout = tmp_path / "not-policyNIM"
    not_checkout.mkdir()

    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--client",
            "codex",
            "--repo-root",
            str(not_checkout),
        ],
    )

    assert result.exit_code == 1
    assert "--repo-root must point to a PolicyNIM source checkout" in result.stderr
    assert "Omit --repo-root to launch the installed CLI" in result.stderr


def test_mcp_smoke_prints_stdio_tool_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Print machine-readable MCP tool discovery evidence."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    write_env_file(checkout_root / ".env", NVIDIA_API_KEY="nvapi-test-key")

    async def fake_smoke(*, timeout_seconds: float) -> dict[str, object]:
        """Return successful MCP smoke evidence for CLI rendering."""
        assert timeout_seconds == 5.0
        return {
            "status": "ok",
            "transport": "stdio",
            "command": [sys.executable, "-m", "policynim.interfaces.cli", "mcp"],
            "tools": ["policy_preflight", "policy_search"],
            "missing_tools": [],
        }

    monkeypatch.setattr("policynim.interfaces.cli._run_mcp_stdio_smoke", fake_smoke)

    result = runner.invoke(app, ["mcp-smoke", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["tools"] == ["policy_preflight", "policy_search"]


def test_mcp_smoke_can_launch_from_generated_codex_config_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Handshake with the same local stdio command emitted by mcp-config."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    config_result = runner.invoke(
        app,
        [
            "mcp-config",
            "--client",
            "codex",
            "--repo-root",
            str(checkout_root),
            "--format",
            "json",
        ],
    )
    config_file = tmp_path / "codex-mcp-config.json"
    config_file.write_text(config_result.stdout, encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_smoke(
        *,
        timeout_seconds: float,
        command: list[str] | None = None,
        cwd: Path | None = None,
    ) -> dict[str, object]:
        """Capture the config-derived stdio launch command."""
        captured["timeout_seconds"] = timeout_seconds
        captured["command"] = command
        captured["cwd"] = cwd
        return {
            "status": "ok",
            "transport": "stdio",
            "command": command,
            "config_source": str(config_file),
            "tools": ["policy_preflight", "policy_search"],
            "missing_tools": [],
        }

    monkeypatch.setattr("policynim.interfaces.cli._run_mcp_stdio_smoke", fake_smoke)

    result = runner.invoke(
        app,
        ["mcp-smoke", "--mcp-config-file", str(config_file), "--format", "json"],
    )

    assert result.exit_code == 0
    assert captured["timeout_seconds"] == 5.0
    assert captured["command"] == [
        "uv",
        "run",
        "--directory",
        str(checkout_root),
        "policynim",
        "mcp",
        "--transport",
        "stdio",
    ]
    assert captured["cwd"] == checkout_root
    payload = json.loads(result.stdout)
    assert payload["config_source"] == str(config_file)


def test_mcp_smoke_rejects_hosted_mcp_config_file(tmp_path: Path) -> None:
    """Do not imply that local stdio smoke proves a hosted HTTP config."""
    config_file = tmp_path / "hosted-mcp-config.json"
    config_file.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "client": "codex",
                "target": "hosted-http",
                "server_name": "policynim",
                "hosted_url": "https://policy.example/mcp",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["mcp-smoke", "--mcp-config-file", str(config_file)])

    assert result.exit_code == 1
    assert "mcp-smoke --mcp-config-file only supports local-stdio configs" in result.stderr


def test_mcp_smoke_help_mentions_generated_config_file() -> None:
    """Keep the generated-config handshake discoverable from CLI help."""
    result = runner.invoke(app, ["mcp-smoke", "--help"])
    help_output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "--mcp-config-file" in help_output
    assert "mcp-config" in help_output
    assert "--format json" in help_output


def test_mcp_smoke_does_not_require_completed_standalone_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Let clean installs prove MCP tool registration before API-key setup."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    async def fake_smoke(*, timeout_seconds: float) -> dict[str, object]:
        """Return successful MCP smoke evidence for a clean install."""
        assert timeout_seconds == 5.0
        return {
            "status": "ok",
            "transport": "stdio",
            "command": [sys.executable, "-m", "policynim.interfaces.cli", "mcp"],
            "tools": ["policy_preflight", "policy_search"],
            "missing_tools": [],
        }

    monkeypatch.setattr("policynim.interfaces.cli._run_mcp_stdio_smoke", fake_smoke)

    result = runner.invoke(app, ["mcp-smoke", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["missing_tools"] == []


def test_mcp_smoke_exits_nonzero_when_tools_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exit nonzero when local stdio smoke misses expected MCP tools."""
    checkout_root, _, _ = configure_checkout_cli_environment(monkeypatch, tmp_path)
    write_env_file(checkout_root / ".env", NVIDIA_API_KEY="nvapi-test-key")

    async def fake_smoke(*, timeout_seconds: float) -> dict[str, object]:
        """Return incomplete MCP smoke evidence for CLI rendering."""
        return {
            "status": "error",
            "transport": "stdio",
            "command": [sys.executable, "-m", "policynim.interfaces.cli", "mcp"],
            "tools": ["policy_search"],
            "missing_tools": ["policy_preflight"],
        }

    monkeypatch.setattr("policynim.interfaces.cli._run_mcp_stdio_smoke", fake_smoke)

    result = runner.invoke(app, ["mcp-smoke"])

    assert result.exit_code == 1
    assert "PolicyNIM MCP smoke" in result.stdout
    assert "missing_tools" in result.stdout
    assert "policy_preflight" in result.stdout


def test_mcp_smoke_failure_text_prints_recovery_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Tell users how to recover when local stdio registration fails."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    async def fake_smoke(*, timeout_seconds: float) -> dict[str, object]:
        """Return failed MCP smoke evidence with recovery steps."""
        assert timeout_seconds == 5.0
        return {
            "status": "error",
            "transport": "stdio",
            "command": [sys.executable, "-m", "policynim.interfaces.cli", "mcp"],
            "tools": [],
            "missing_tools": ["policy_preflight", "policy_search"],
            "message": "MCP stdio smoke failed: RuntimeError: launch failed",
            "next_steps": [
                "Run `policynim doctor` to inspect config, credentials, and local index state.",
                "Run `policynim ingest` before calling policy_preflight or policy_search.",
            ],
        }

    monkeypatch.setattr("policynim.interfaces.cli._run_mcp_stdio_smoke", fake_smoke)

    result = runner.invoke(app, ["mcp-smoke"])

    assert result.exit_code == 1
    assert "Next steps:" in result.stdout
    assert "policynim doctor" in result.stdout
    assert "policynim ingest" in result.stdout


def test_mcp_stdio_smoke_exception_report_includes_recovery_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Include recovery steps in machine-readable stdio launch failures."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)

    @asynccontextmanager
    async def failing_stdio_client(*args: object, **kwargs: object):
        """Raise during stdio client startup to exercise recovery output."""
        raise RuntimeError("spawn failed")
        yield

    monkeypatch.setattr("policynim.interfaces.cli.stdio_client", failing_stdio_client)

    report = asyncio.run(cli_module._run_mcp_stdio_smoke(timeout_seconds=1.0))

    assert report["status"] == "error"
    next_steps = cast(list[str], report["next_steps"])
    assert "policynim doctor" in " ".join(next_steps)
    assert "policynim ingest" in " ".join(next_steps)
    assert "mcp-config --target local-stdio" in " ".join(next_steps)


def test_mcp_stdio_smoke_uses_frozen_executable_for_standalone_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Re-enter PyInstaller bundles directly instead of using Python -m."""
    configure_standalone_cli_environment(monkeypatch, tmp_path)
    binary = tmp_path / "dist" / "policynim" / "policynim"
    monkeypatch.setattr(cli_module.sys, "executable", str(binary))
    monkeypatch.setattr(cli_module.sys, "frozen", True, raising=False)

    @asynccontextmanager
    async def failing_stdio_client(*args: object, **kwargs: object):
        """Raise during frozen stdio client startup to capture the command."""
        raise RuntimeError("spawn failed")
        yield

    monkeypatch.setattr("policynim.interfaces.cli.stdio_client", failing_stdio_client)

    report = asyncio.run(cli_module._run_mcp_stdio_smoke(timeout_seconds=1.0))

    assert report["status"] == "error"
    assert report["command"] == [str(binary), "mcp", "--transport", "stdio"]


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


def test_beta_admin_audit_log_prints_filtered_json(monkeypatch) -> None:
    """Print filtered hosted beta audit events as JSON."""
    service = MockBetaAuthService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_beta_auth_service",
        lambda settings: service,
    )

    result = runner.invoke(
        app,
        [
            "beta-admin",
            "audit-log",
            "--github-login",
            "octocat",
            "--event-type",
            "api_key_rotated",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["event_id"] == 2
    assert payload[0]["github_login"] == "octocat"
    assert payload[0]["event_type"] == "api_key_rotated"
    assert payload[0]["details"] == {"key_prefix": "pnm_current"}


def test_beta_admin_audit_log_rejects_invalid_limit() -> None:
    """Reject non-positive audit-log limits at the Typer boundary."""
    result = runner.invoke(app, ["beta-admin", "audit-log", "--limit", "0"])

    assert result.exit_code == 2
    assert "Invalid value" in result.stderr


def test_beta_admin_audit_log_surfaces_missing_account(monkeypatch) -> None:
    """Surface missing account filters as operator-facing CLI errors."""
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_beta_auth_service",
        lambda settings: MockBetaAuthService(),
    )

    result = runner.invoke(
        app,
        ["beta-admin", "audit-log", "--github-login", "missing-user"],
    )

    assert result.exit_code == 1
    assert "missing-user" in result.stderr


def test_beta_admin_help_mentions_operator_commands() -> None:
    result = runner.invoke(app, ["beta-admin", "--help"])

    assert result.exit_code == 0
    assert "list-accounts" in result.stdout
    assert "audit-log" in result.stdout
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


def _cli_evidence_summary() -> RuntimeEvidenceSessionSummary:
    """Return one typed evidence summary for report-format CLI tests."""
    started_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    completed_at = datetime(2026, 4, 5, 12, 0, 10, tzinfo=UTC)
    return RuntimeEvidenceSessionSummary(
        session_id="session-1",
        started_at=started_at,
        completed_at=completed_at,
        event_count=2,
        execution_count=1,
        allowed_count=1,
        confirmed_count=0,
        blocked_count=0,
        refused_count=0,
        failed_count=0,
        incomplete_count=0,
        executions=[
            RuntimeEvidenceExecutionSummary(
                execution_id="exec-1",
                action_kind="shell_command",
                task="Run release checks.",
                decision="allow",
                summary="Release checks may run.",
                confirmation_outcome="not_required",
                execution_outcome="allowed",
                started_at=started_at,
                completed_at=completed_at,
            )
        ],
    )


def test_evidence_report_command_prints_markdown(monkeypatch) -> None:
    """Render runtime evidence reports as Markdown on stdout."""
    service = MockRuntimeEvidenceReportService(_cli_evidence_summary())
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_evidence_report_service",
        lambda settings: service,
        raising=False,
    )

    result = runner.invoke(
        app,
        ["evidence", "report", "--session-id", "session-1", "--format", "markdown"],
    )

    assert result.exit_code == 0
    assert "# Runtime Evidence Report" in result.stdout
    assert "session-1" in result.stdout
    assert "| exec-1 | shell_command | allow | allowed |" in result.stdout
    assert service.closed is True


def test_evidence_report_command_writes_json_output_atomically(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Write rendered JSON evidence reports to a nested output path."""
    service = MockRuntimeEvidenceReportService(_cli_evidence_summary())
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_evidence_report_service",
        lambda settings: service,
        raising=False,
    )
    monkeypatch.chdir(tmp_path)
    output_path = Path("reports") / "session-1.json"

    result = runner.invoke(
        app,
        ["evidence", "report", "--session-id", "session-1", "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert "Wrote runtime evidence report to" in result.stdout
    payload = json.loads((tmp_path / output_path).read_text(encoding="utf-8"))
    assert payload["session_id"] == "session-1"
    assert service.closed is True


def test_evidence_report_command_rejects_directory_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Reject output paths that already point at directories."""
    service = MockRuntimeEvidenceReportService(_cli_evidence_summary())
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_evidence_report_service",
        lambda settings: service,
        raising=False,
    )

    result = runner.invoke(
        app,
        ["evidence", "report", "--session-id", "session-1", "--output", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "must be a file path" in result.stderr


def test_evidence_report_command_rejects_empty_output(
    monkeypatch,
) -> None:
    """Reject empty output path strings before filesystem writes."""
    service = MockRuntimeEvidenceReportService(_cli_evidence_summary())
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_evidence_report_service",
        lambda settings: service,
        raising=False,
    )

    result = runner.invoke(
        app,
        ["evidence", "report", "--session-id", "session-1", "--output", " "],
    )

    assert result.exit_code == 1
    assert "must not be empty" in result.stderr


def test_evidence_report_command_surfaces_staged_cleanup_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Include staged-file cleanup failures in the returned CLI error."""
    service = MockRuntimeEvidenceReportService(_cli_evidence_summary())
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_runtime_evidence_report_service",
        lambda settings: service,
        raising=False,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "policynim.interfaces.cli.os.replace",
        lambda src, dst: (_ for _ in ()).throw(PermissionError("permission denied")),
    )

    original_unlink = Path.unlink

    def fail_unlink(self: Path, *, missing_ok: bool = False) -> None:
        """Fail staged-file cleanup to verify the surfaced CLI error text."""
        del missing_ok
        if self.suffix == ".tmp":
            raise OSError("cleanup failed")
        original_unlink(self, missing_ok=False)

    monkeypatch.setattr("policynim.interfaces.cli.Path.unlink", fail_unlink)

    result = runner.invoke(
        app,
        [
            "evidence",
            "report",
            "--session-id",
            "session-1",
            "--output",
            str(Path("reports") / "session-1.json"),
        ],
    )

    assert result.exit_code == 1
    assert "Cleanup of staged file" in result.stderr
    assert "cleanup failed" in result.stderr
    assert list((tmp_path / "reports").glob("*.tmp"))


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
