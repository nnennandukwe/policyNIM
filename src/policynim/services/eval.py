"""Evaluation service for PolicyNIM search and grounded preflight."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import socket
import subprocess
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Any, cast

from policynim.contracts import Generator, IndexStore, Reranker
from policynim.errors import PolicyNIMError
from policynim.runtime_paths import resolve_eval_suite_path, resolve_runtime_path
from policynim.services.compiler import PolicyCompilerService
from policynim.services.conformance import PolicyConformanceService
from policynim.services.evidence_trace import create_policy_evidence_trace_service
from policynim.services.ingest import create_ingest_service
from policynim.services.preflight import PreflightService
from policynim.services.regeneration import PolicyRegenerationService
from policynim.services.search import SearchService
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import (
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    CompileRequest,
    EvalAggregateMetrics,
    EvalBackend,
    EvalCase,
    EvalCaseMetrics,
    EvalCaseResult,
    EvalComparisonDelta,
    EvalExecutionMode,
    EvalModeRunResult,
    EvalRunResult,
    EvalSuite,
    GeneratedCompiledPolicyDraft,
    GeneratedPolicyConformanceDraft,
    GeneratedPolicyConstraint,
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    PolicyChunk,
    PolicyConformanceRequest,
    PolicyConformanceResult,
    PolicyEvidenceTrace,
    PolicyMetadata,
    PolicySelectionPacket,
    PreflightRegenerationRequest,
    PreflightRegenerationResult,
    PreflightRequest,
    PreflightResult,
    RegenerationBackend,
    RegenerationContext,
    ScoredChunk,
    SearchRequest,
    SearchResult,
)

_PROJECT_NAME = "PolicyNIM Eval"
_SAFE_SUITE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEFAULT_UI_START_TIMEOUT_SECONDS = 5.0
_UI_START_POLL_INTERVAL_SECONDS = 0.1


class EvalService:
    """Run PolicyNIM eval suites and persist comparable reports."""

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._workspace_path = resolve_runtime_path(settings.eval_workspace_dir)
        self._ui_port: int | None = None

    def __enter__(self) -> EvalService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    @property
    def workspace_path(self) -> Path:
        """Return the resolved eval workspace path."""
        return self._workspace_path

    def run(
        self,
        *,
        mode: EvalExecutionMode = "offline",
        backend: EvalBackend = "default",
        compare_rerank: bool = True,
        regenerate: bool = False,
        max_regenerations: int = 1,
    ) -> EvalRunResult:
        """Run the requested eval suite and persist the resulting reports."""
        if regenerate and backend == "default":
            raise ValueError("eval --regenerate requires backend nemo, nemo_evaluator, or nat.")
        if max_regenerations < 1 or max_regenerations > 3:
            raise ValueError("max_regenerations must be between 1 and 3.")
        suite_path = resolve_eval_suite_path()
        suite = _load_eval_suite(suite_path)
        rerank_modes = [True, False] if compare_rerank else [True]

        mode_results: list[EvalModeRunResult] = []
        for rerank_enabled in rerank_modes:
            case_results = self._run_suite_cases(
                suite,
                mode=mode,
                backend=backend,
                rerank_enabled=rerank_enabled,
                regenerate=regenerate,
                max_regenerations=max_regenerations,
            )
            mode_results.append(
                self._persist_mode_run(
                    suite=suite,
                    suite_path=suite_path,
                    mode=mode,
                    backend=backend,
                    rerank_enabled=rerank_enabled,
                    case_results=case_results,
                )
            )

        comparison = (
            _compare_mode_results(mode_results[0], mode_results[1])
            if compare_rerank and len(mode_results) == 2
            else None
        )
        return EvalRunResult(
            mode=mode,
            backend=backend,
            suite_name=suite.name,
            suite_path=suite_path.as_posix(),
            workspace_path=self._workspace_path.as_posix(),
            compare_rerank=compare_rerank,
            runs=mode_results,
            comparison=comparison,
        )

    def start_ui(self, *, port: int | None = None) -> None:
        """Start the Phoenix local UI in the background and verify startup."""
        self._run_ui(port=port)

    def publish_to_ui(self, result: EvalRunResult, *, port: int | None = None) -> None:
        """Publish a completed eval result to the local Phoenix UI."""
        resolved_port = port if port is not None else self._ui_port or self._settings.eval_ui_port
        endpoint = f"http://127.0.0.1:{resolved_port}"
        log_path = _phoenix_log_path(self._workspace_path)
        try:
            _publish_eval_result_to_phoenix(result, endpoint=endpoint)
        except Exception as exc:
            raise PolicyNIMError(
                "Could not publish eval traces to Phoenix at "
                f"{endpoint}. See {log_path.as_posix()} for Phoenix logs."
            ) from exc

    def _run_ui(self, *, port: int | None) -> None:
        """Start the Phoenix local UI against the PolicyNIM workspace."""
        resolved_port = port if port is not None else self._settings.eval_ui_port
        self._workspace_path.mkdir(parents=True, exist_ok=True)
        ui_dir = self._workspace_path / "ui"
        phoenix_dir = self._workspace_path / "phoenix"
        ui_dir.mkdir(parents=True, exist_ok=True)
        phoenix_dir.mkdir(parents=True, exist_ok=True)
        log_path = _phoenix_log_path(self._workspace_path)
        command = ["phoenix", "serve"]
        env = os.environ.copy()
        env.update(
            {
                "PHOENIX_WORKING_DIR": phoenix_dir.as_posix(),
                "PHOENIX_HOST": "127.0.0.1",
                "PHOENIX_PORT": str(resolved_port),
                "PHOENIX_PROJECT_NAME": _PROJECT_NAME,
            }
        )
        try:
            with log_path.open("ab") as log_file:
                process = subprocess.Popen(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=env,
                )
        except OSError as exc:
            raise PolicyNIMError(
                f"Could not start Phoenix UI. See {log_path.as_posix()} for details."
            ) from exc

        try:
            _wait_for_ui_start(
                process,
                port=resolved_port,
                log_path=log_path,
                timeout_seconds=_DEFAULT_UI_START_TIMEOUT_SECONDS,
            )
        except Exception:
            if process.poll() is None:
                process.terminate()
            raise
        self._ui_port = resolved_port

    def _run_suite_cases(
        self,
        suite: EvalSuite,
        *,
        mode: EvalExecutionMode,
        backend: EvalBackend,
        rerank_enabled: bool,
        regenerate: bool,
        max_regenerations: int,
    ) -> list[EvalCaseResult]:
        if mode == "offline":
            return self._run_offline_suite(
                suite,
                backend=backend,
                rerank_enabled=rerank_enabled,
                regenerate=regenerate,
                max_regenerations=max_regenerations,
            )
        return self._run_live_suite(
            suite,
            backend=backend,
            rerank_enabled=rerank_enabled,
            regenerate=regenerate,
            max_regenerations=max_regenerations,
        )

    def _run_offline_suite(
        self,
        suite: EvalSuite,
        *,
        backend: EvalBackend,
        rerank_enabled: bool,
        regenerate: bool,
        max_regenerations: int,
    ) -> list[EvalCaseResult]:
        store = _OfflineIndexStore(_OFFLINE_QUERY_CANDIDATES)
        embedder = _OfflineEmbedder()
        search_service = SearchService(
            embedder=embedder,
            index_store=store,
            reranker=_OfflineReranker() if rerank_enabled else None,
        )
        preflight_service = PreflightService(
            embedder=embedder,
            index_store=store,
            reranker=_OfflineReranker() if rerank_enabled else _PassThroughReranker(),
            generator=_OfflineGenerator(),
            compiler=_OfflinePolicyCompiler(),
        )
        conformance_service = (
            PolicyConformanceService(evaluator=_OfflinePolicyConformanceEvaluator())
            if backend != "default" and not regenerate
            else None
        )
        regeneration_service = (
            PolicyRegenerationService(
                compiler_service=PolicyCompilerService(
                    embedder=embedder,
                    index_store=store,
                    reranker=_OfflineReranker() if rerank_enabled else _PassThroughReranker(),
                    compiler=_OfflinePolicyCompiler(),
                ),
                generator=_OfflineGenerator(),
                conformance_service=PolicyConformanceService(
                    evaluator=_OfflinePolicyConformanceEvaluator()
                ),
            )
            if regenerate
            else None
        )
        try:
            return _score_suite_cases(
                suite.cases,
                search_service=search_service,
                preflight_service=preflight_service,
                conformance_service=conformance_service,
                regeneration_service=regeneration_service,
                backend=backend,
                rerank_enabled=rerank_enabled,
                max_regenerations=max_regenerations,
            )
        finally:
            search_service.close()
            preflight_service.close()
            _close_component(regeneration_service)
            _close_component(conformance_service)

    def _run_live_suite(
        self,
        suite: EvalSuite,
        *,
        backend: EvalBackend,
        rerank_enabled: bool,
        regenerate: bool,
        max_regenerations: int,
    ) -> list[EvalCaseResult]:
        with TemporaryDirectory(prefix="policynim-eval-") as temp_dir:
            temp_settings = self._settings.model_copy(
                update={"lancedb_uri": Path(temp_dir) / "lancedb"}
            )
            ingest_service = create_ingest_service(temp_settings)
            ingest_service.run()

            search_service = _create_live_search_service(
                temp_settings,
                rerank_enabled=rerank_enabled,
            )
            preflight_service = _create_live_preflight_service(
                temp_settings,
                rerank_enabled=rerank_enabled,
            )
            conformance_service = (
                _create_live_conformance_service(temp_settings, backend=backend)
                if backend != "default" and not regenerate
                else None
            )
            regeneration_service = (
                _create_live_regeneration_service(
                    temp_settings,
                    backend=backend,
                    rerank_enabled=rerank_enabled,
                )
                if regenerate
                else None
            )
            try:
                return _score_suite_cases(
                    suite.cases,
                    search_service=search_service,
                    preflight_service=preflight_service,
                    conformance_service=conformance_service,
                    regeneration_service=regeneration_service,
                    backend=backend,
                    rerank_enabled=rerank_enabled,
                    max_regenerations=max_regenerations,
                )
            finally:
                search_service.close()
                preflight_service.close()
                _close_component(conformance_service)
                _close_component(regeneration_service)

    def _persist_mode_run(
        self,
        *,
        suite: EvalSuite,
        suite_path: Path,
        mode: EvalExecutionMode,
        backend: EvalBackend,
        rerank_enabled: bool,
        case_results: list[EvalCaseResult],
    ) -> EvalModeRunResult:
        metrics = _aggregate_metrics(case_results)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        mode_slug = "rerank-on" if rerank_enabled else "rerank-off"
        suite_slug = _suite_name_slug(suite.name)

        results_dir = self._workspace_path / "results"
        reports_dir = self._workspace_path / "reports"
        results_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        result_payload = EvalModeRunResult(
            backend=backend,
            rerank_enabled=rerank_enabled,
            metrics=metrics,
            result_json_path="",
            report_html_path="",
            case_results=case_results,
        )
        backend_slug = f"{mode_slug}-{backend}"
        result_json_path = results_dir / f"{timestamp}-{suite_slug}-{backend_slug}.json"
        report_html_path = reports_dir / f"{timestamp}-{suite_slug}-{backend_slug}.html"
        result_json_path.write_text(
            result_payload.model_copy(
                update={
                    "result_json_path": result_json_path.as_posix(),
                    "report_html_path": report_html_path.as_posix(),
                }
            ).model_dump_json(indent=2),
            encoding="utf-8",
        )

        report_rows = _build_eval_report_rows(
            suite=suite,
            suite_path=suite_path,
            mode=mode,
            backend=backend,
            rerank_enabled=rerank_enabled,
            case_results=case_results,
            metrics=metrics,
        )
        _write_eval_html_report(
            report_html_path,
            suite=suite,
            suite_path=suite_path,
            mode=mode,
            backend=backend,
            rerank_enabled=rerank_enabled,
            metrics=metrics,
            rows=report_rows,
        )

        return EvalModeRunResult(
            backend=backend,
            rerank_enabled=rerank_enabled,
            metrics=metrics,
            result_json_path=result_json_path.as_posix(),
            report_html_path=report_html_path.as_posix(),
            case_results=case_results,
        )


def create_eval_service(settings: Settings | None = None) -> EvalService:
    """Build the default eval service from application settings."""
    return EvalService(settings=settings or get_settings())


def _score_suite_cases(
    cases: Sequence[EvalCase],
    *,
    search_service: SearchService,
    preflight_service: PreflightService,
    conformance_service: PolicyConformanceService | None,
    regeneration_service: PolicyRegenerationService | None,
    backend: EvalBackend,
    rerank_enabled: bool,
    max_regenerations: int,
) -> list[EvalCaseResult]:
    results: list[EvalCaseResult] = []
    trace_service = create_policy_evidence_trace_service()
    for case in cases:
        if case.kind == "search":
            result = search_service.search(
                SearchRequest(query=case.input, domain=case.domain, top_k=case.top_k)
            )
            results.append(_score_search_case(case, result=result, rerank_enabled=rerank_enabled))
            continue

        if regeneration_service is not None:
            regeneration_result = regeneration_service.regenerate(
                PreflightRegenerationRequest(
                    task=case.input,
                    domain=case.domain,
                    top_k=case.top_k,
                    backend=_regeneration_backend(backend),
                    max_regenerations=max_regenerations,
                )
            )
            results.append(
                _score_preflight_case(
                    case,
                    result=regeneration_result.final_result,
                    conformance_result=regeneration_result.final_conformance_result,
                    evidence_trace=regeneration_result.evidence_trace,
                    regeneration_result=regeneration_result,
                    rerank_enabled=rerank_enabled,
                )
            )
            continue

        request = PreflightRequest(task=case.input, domain=case.domain, top_k=case.top_k)
        conformance_result: PolicyConformanceResult | None = None
        trace_result = preflight_service.preflight_with_trace(request)
        result = trace_result.result
        if conformance_service is not None and not result.insufficient_context:
            conformance_result = conformance_service.evaluate(
                PolicyConformanceRequest(
                    task=request.task,
                    result=result,
                    compiled_packet=trace_result.compiled_packet,
                    trace_steps=trace_result.trace_steps,
                ),
                backend=backend,
            )
        evidence_trace = trace_service.build(
            trace_result,
            conformance_result=conformance_result,
            include_chunk_text=False,
        )
        results.append(
            _score_preflight_case(
                case,
                result=result,
                conformance_result=conformance_result,
                evidence_trace=evidence_trace,
                regeneration_result=None,
                rerank_enabled=rerank_enabled,
            )
        )
    return results


def _score_search_case(
    case: EvalCase,
    *,
    result: SearchResult,
    rerank_enabled: bool,
) -> EvalCaseResult:
    actual_chunk_ids = [hit.chunk_id for hit in result.hits]
    matched_chunk_ids = [
        chunk_id for chunk_id in case.expected_chunk_ids if chunk_id in set(actual_chunk_ids)
    ]
    chunk_recall = _recall(len(matched_chunk_ids), len(case.expected_chunk_ids))
    failure_reasons = _build_failure_reasons(
        expected_insufficient_context=case.expected_insufficient_context,
        actual_insufficient_context=result.insufficient_context,
        expected_ids=case.expected_chunk_ids,
        matched_ids=matched_chunk_ids,
        label="chunk_id",
    )
    return EvalCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        input=case.input,
        domain=case.domain,
        top_k=case.top_k,
        rerank_enabled=rerank_enabled,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
        expected_insufficient_context=case.expected_insufficient_context,
        actual_insufficient_context=result.insufficient_context,
        expected_chunk_ids=case.expected_chunk_ids,
        actual_chunk_ids=actual_chunk_ids,
        matched_chunk_ids=matched_chunk_ids,
        expected_policy_ids=[],
        actual_policy_ids=[],
        matched_policy_ids=[],
        metrics=EvalCaseMetrics(
            expected_chunk_recall=chunk_recall,
            expected_policy_recall=1.0,
            insufficient_context_correct=(
                case.expected_insufficient_context == result.insufficient_context
            ),
        ),
    )


def _regeneration_backend(backend: EvalBackend) -> RegenerationBackend:
    if backend == "default":
        raise ValueError("regeneration requires a conformance-capable backend.")
    return backend


def _score_preflight_case(
    case: EvalCase,
    *,
    result: PreflightResult,
    conformance_result: PolicyConformanceResult | None = None,
    evidence_trace: PolicyEvidenceTrace | None = None,
    regeneration_result: PreflightRegenerationResult | None = None,
    rerank_enabled: bool,
) -> EvalCaseResult:
    actual_policy_ids = [policy.policy_id for policy in result.applicable_policies]
    matched_policy_ids = [
        policy_id for policy_id in case.expected_policy_ids if policy_id in set(actual_policy_ids)
    ]
    actual_chunk_ids = [citation.chunk_id for citation in result.citations]
    matched_chunk_ids = [
        chunk_id for chunk_id in case.expected_chunk_ids if chunk_id in set(actual_chunk_ids)
    ]
    failure_reasons = _build_failure_reasons(
        expected_insufficient_context=case.expected_insufficient_context,
        actual_insufficient_context=result.insufficient_context,
        expected_ids=case.expected_policy_ids,
        matched_ids=matched_policy_ids,
        label="policy_id",
    )
    if conformance_result is not None and not conformance_result.passed:
        failure_reasons.append(
            "policy conformance failed: "
            + (
                "; ".join(conformance_result.failure_reasons)
                if conformance_result.failure_reasons
                else "score below threshold"
            )
        )
    return EvalCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        input=case.input,
        domain=case.domain,
        top_k=case.top_k,
        rerank_enabled=rerank_enabled,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
        expected_insufficient_context=case.expected_insufficient_context,
        actual_insufficient_context=result.insufficient_context,
        expected_chunk_ids=case.expected_chunk_ids,
        actual_chunk_ids=actual_chunk_ids,
        matched_chunk_ids=matched_chunk_ids,
        expected_policy_ids=case.expected_policy_ids,
        actual_policy_ids=actual_policy_ids,
        matched_policy_ids=matched_policy_ids,
        actual_summary=result.summary,
        conformance_result=conformance_result,
        evidence_trace=evidence_trace,
        regeneration_result=regeneration_result,
        metrics=EvalCaseMetrics(
            expected_chunk_recall=_recall(len(matched_chunk_ids), len(case.expected_chunk_ids)),
            expected_policy_recall=_recall(len(matched_policy_ids), len(case.expected_policy_ids)),
            insufficient_context_correct=(
                case.expected_insufficient_context == result.insufficient_context
            ),
            conformance_score=(
                conformance_result.overall_score if conformance_result is not None else None
            ),
            conformance_passed=(
                conformance_result.passed if conformance_result is not None else None
            ),
        ),
    )


def _build_failure_reasons(
    *,
    expected_insufficient_context: bool,
    actual_insufficient_context: bool,
    expected_ids: Sequence[str],
    matched_ids: Sequence[str],
    label: str,
) -> list[str]:
    failure_reasons: list[str] = []
    if expected_insufficient_context != actual_insufficient_context:
        failure_reasons.append(
            "insufficient_context mismatch: "
            f"expected {expected_insufficient_context}, got {actual_insufficient_context}"
        )
    missing_ids = [value for value in expected_ids if value not in set(matched_ids)]
    if missing_ids:
        failure_reasons.append(f"missing expected {label} values: {', '.join(missing_ids)}")
    return failure_reasons


def _aggregate_metrics(case_results: Sequence[EvalCaseResult]) -> EvalAggregateMetrics:
    search_results = [result for result in case_results if result.kind == "search"]
    preflight_results = [result for result in case_results if result.kind == "preflight"]
    conformance_results = [
        result for result in preflight_results if result.conformance_result is not None
    ]
    total_results = list(case_results)
    passed_count = sum(result.passed for result in total_results)
    conformance_passed_count = sum(
        result.conformance_result.passed
        for result in conformance_results
        if result.conformance_result is not None
    )
    return EvalAggregateMetrics(
        case_count=len(total_results),
        passed_count=passed_count,
        search_case_count=len(search_results),
        search_passed_count=sum(result.passed for result in search_results),
        preflight_case_count=len(preflight_results),
        preflight_passed_count=sum(result.passed for result in preflight_results),
        overall_pass_rate=_ratio(passed_count, len(total_results)),
        search_pass_rate=_ratio(
            sum(result.passed for result in search_results),
            len(search_results),
        ),
        preflight_pass_rate=_ratio(
            sum(result.passed for result in preflight_results),
            len(preflight_results),
        ),
        expected_chunk_recall=_average(
            [result.metrics.expected_chunk_recall for result in search_results]
        ),
        expected_policy_recall=_average(
            [result.metrics.expected_policy_recall for result in preflight_results]
        ),
        insufficient_context_accuracy=_average(
            [
                1.0 if result.metrics.insufficient_context_correct else 0.0
                for result in total_results
            ]
        ),
        conformance_case_count=len(conformance_results),
        conformance_passed_count=conformance_passed_count,
        conformance_pass_rate=_ratio(conformance_passed_count, len(conformance_results)),
        conformance_score=_average(
            [
                result.conformance_result.overall_score
                for result in conformance_results
                if result.conformance_result is not None
            ]
        ),
    )


def _compare_mode_results(
    rerank_on: EvalModeRunResult,
    rerank_off: EvalModeRunResult,
) -> EvalComparisonDelta:
    on_by_case = {result.case_id: result for result in rerank_on.case_results}
    off_by_case = {result.case_id: result for result in rerank_off.case_results}
    improved: list[str] = []
    regressed: list[str] = []
    unchanged: list[str] = []
    for case_id in sorted(on_by_case):
        on_passed = on_by_case[case_id].passed
        off_passed = off_by_case[case_id].passed
        if on_passed and not off_passed:
            improved.append(case_id)
        elif off_passed and not on_passed:
            regressed.append(case_id)
        else:
            unchanged.append(case_id)

    return EvalComparisonDelta(
        overall_pass_rate_delta=(
            rerank_on.metrics.overall_pass_rate - rerank_off.metrics.overall_pass_rate
        ),
        expected_chunk_recall_delta=(
            rerank_on.metrics.expected_chunk_recall - rerank_off.metrics.expected_chunk_recall
        ),
        expected_policy_recall_delta=(
            rerank_on.metrics.expected_policy_recall - rerank_off.metrics.expected_policy_recall
        ),
        insufficient_context_accuracy_delta=(
            rerank_on.metrics.insufficient_context_accuracy
            - rerank_off.metrics.insufficient_context_accuracy
        ),
        improved_case_ids=improved,
        regressed_case_ids=regressed,
        unchanged_case_ids=unchanged,
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _average(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _recall(matched_count: int, expected_count: int) -> float:
    if expected_count == 0:
        return 1.0
    return matched_count / expected_count


def _load_eval_suite(path: Path) -> EvalSuite:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return EvalSuite(name=path.stem, cases=[EvalCase.model_validate(item) for item in payload])
    return EvalSuite.model_validate(payload)


def _suite_name_slug(name: str) -> str:
    slug = _SAFE_SUITE_NAME_RE.sub("_", name.replace("/", "_").replace("\\", "_"))
    slug = slug.strip("._-")
    return slug or "eval-suite"


def _wait_for_ui_start(
    process: subprocess.Popen[bytes],
    *,
    port: int,
    log_path: Path,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PolicyNIMError(
                "Phoenix UI exited before startup completed. "
                f"See {log_path.as_posix()} for details."
            )
        if _is_local_port_reachable(port):
            return None
        time.sleep(_UI_START_POLL_INTERVAL_SECONDS)

    if process.poll() is None:
        raise PolicyNIMError(
            f"Phoenix UI did not become reachable in time. See {log_path.as_posix()} for details."
        )
    raise PolicyNIMError(
        f"Phoenix UI exited before startup completed. See {log_path.as_posix()} for details."
    )


def _phoenix_log_path(workspace_path: Path) -> Path:
    return workspace_path / "ui" / "phoenix.log"


def _is_local_port_reachable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(_UI_START_POLL_INTERVAL_SECONDS)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _build_eval_report_rows(
    *,
    suite: EvalSuite,
    suite_path: Path,
    mode: EvalExecutionMode,
    backend: EvalBackend,
    rerank_enabled: bool,
    case_results: Sequence[EvalCaseResult],
    metrics: EvalAggregateMetrics,
) -> list[dict[str, object]]:
    return [
        {
            "suite_name": suite.name,
            "suite_path": suite_path.as_posix(),
            "mode": mode,
            "backend": backend,
            "rerank_enabled": rerank_enabled,
            "case_id": result.case_id,
            "kind": result.kind,
            "input": result.input,
            "domain": result.domain or "",
            "top_k": result.top_k,
            "passed": result.passed,
            "expected_insufficient_context": result.expected_insufficient_context,
            "actual_insufficient_context": result.actual_insufficient_context,
            "expected_chunk_ids": ",".join(result.expected_chunk_ids),
            "actual_chunk_ids": ",".join(result.actual_chunk_ids),
            "matched_chunk_ids": ",".join(result.matched_chunk_ids),
            "expected_policy_ids": ",".join(result.expected_policy_ids),
            "actual_policy_ids": ",".join(result.actual_policy_ids),
            "matched_policy_ids": ",".join(result.matched_policy_ids),
            "failure_reasons": " | ".join(result.failure_reasons),
            "expected_chunk_recall": result.metrics.expected_chunk_recall,
            "expected_policy_recall": result.metrics.expected_policy_recall,
            "insufficient_context_correct": result.metrics.insufficient_context_correct,
            "conformance_passed": (
                result.conformance_result.passed if result.conformance_result is not None else None
            ),
            "conformance_score": (
                result.conformance_result.overall_score
                if result.conformance_result is not None
                else None
            ),
            "conformance_failure_reasons": (
                " | ".join(result.conformance_result.failure_reasons)
                if result.conformance_result is not None
                else ""
            ),
            "evidence_trace_chunk_count": (
                len(result.evidence_trace.chunks) if result.evidence_trace is not None else 0
            ),
            "evidence_trace_constraint_count": (
                len(result.evidence_trace.constraints) if result.evidence_trace is not None else 0
            ),
            "evidence_trace_conformance_check_count": (
                len(result.evidence_trace.conformance_checks)
                if result.evidence_trace is not None
                else 0
            ),
            "regeneration_attempt_count": (
                len(result.regeneration_result.attempts)
                if result.regeneration_result is not None
                else 0
            ),
            "regeneration_stop_reason": (
                result.regeneration_result.stop_reason
                if result.regeneration_result is not None
                else ""
            ),
            "regeneration_passed": (
                result.regeneration_result.passed
                if result.regeneration_result is not None
                else None
            ),
            "actual_summary": result.actual_summary or "",
            "overall_pass_rate": metrics.overall_pass_rate,
            "case_count": metrics.case_count,
            "passed_count": metrics.passed_count,
            "search_case_count": metrics.search_case_count,
            "search_passed_count": metrics.search_passed_count,
            "preflight_case_count": metrics.preflight_case_count,
            "preflight_passed_count": metrics.preflight_passed_count,
            "expected_policy_recall_run": metrics.expected_policy_recall,
            "expected_chunk_recall_run": metrics.expected_chunk_recall,
            "insufficient_context_accuracy_run": metrics.insufficient_context_accuracy,
            "conformance_case_count": metrics.conformance_case_count,
            "conformance_passed_count": metrics.conformance_passed_count,
            "conformance_pass_rate_run": metrics.conformance_pass_rate,
            "conformance_score_run": metrics.conformance_score,
        }
        for result in case_results
    ]


def _write_eval_html_report(
    path: Path,
    *,
    suite: EvalSuite,
    suite_path: Path,
    mode: EvalExecutionMode,
    backend: EvalBackend,
    rerank_enabled: bool,
    metrics: EvalAggregateMetrics,
    rows: Sequence[dict[str, object]],
) -> None:
    summary_rows: list[tuple[str, object]] = [
        ("Suite", suite.name),
        ("Suite path", suite_path.as_posix()),
        ("Mode", mode),
        ("Backend", backend),
        ("Rerank mode", "rerank-on" if rerank_enabled else "rerank-off"),
        ("Case count", metrics.case_count),
        ("Passed count", metrics.passed_count),
        ("Overall pass rate", _format_metric(metrics.overall_pass_rate)),
        ("Search pass rate", _format_metric(metrics.search_pass_rate)),
        ("Preflight pass rate", _format_metric(metrics.preflight_pass_rate)),
        ("Expected chunk recall", _format_metric(metrics.expected_chunk_recall)),
        ("Expected policy recall", _format_metric(metrics.expected_policy_recall)),
        (
            "Insufficient context accuracy",
            _format_metric(metrics.insufficient_context_accuracy),
        ),
        ("Conformance case count", metrics.conformance_case_count),
        ("Conformance passed count", metrics.conformance_passed_count),
        ("Conformance pass rate", _format_metric(metrics.conformance_pass_rate)),
        ("Conformance score", _format_metric(metrics.conformance_score)),
    ]
    case_columns = [
        "case_id",
        "kind",
        "domain",
        "top_k",
        "passed",
        "expected_insufficient_context",
        "actual_insufficient_context",
        "expected_chunk_ids",
        "actual_chunk_ids",
        "matched_chunk_ids",
        "expected_policy_ids",
        "actual_policy_ids",
        "matched_policy_ids",
        "failure_reasons",
        "expected_chunk_recall",
        "expected_policy_recall",
        "insufficient_context_correct",
        "conformance_passed",
        "conformance_score",
        "regeneration_attempt_count",
        "regeneration_stop_reason",
        "regeneration_passed",
    ]
    document = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>PolicyNIM Eval Report</title>",
            "<style>",
            "body{font-family:system-ui,sans-serif;margin:2rem;line-height:1.45;}",
            "table{border-collapse:collapse;width:100%;margin:1rem 0;}",
            "th,td{border:1px solid #d0d7de;padding:0.4rem;text-align:left;vertical-align:top;}",
            "th{background:#f6f8fa;}",
            "code{white-space:pre-wrap;}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>PolicyNIM Eval Report</h1>",
            "<h2>Run Summary</h2>",
            _html_key_value_table(summary_rows),
            "<h2>Eval Cases</h2>",
            _html_row_table(case_columns, rows),
            "</body>",
            "</html>",
        ]
    )
    path.write_text(document, encoding="utf-8")


def _html_key_value_table(rows: Sequence[tuple[str, object]]) -> str:
    body = "\n".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(_format_report_value(value))}</td></tr>"
        for label, value in rows
    )
    return f"<table><tbody>{body}</tbody></table>"


def _html_row_table(columns: Sequence[str], rows: Sequence[dict[str, object]]) -> str:
    header = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = "\n".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(_format_report_value(row.get(column, '')))}</td>"
            for column in columns
        )
        + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def _format_report_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return _format_metric(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _format_metric(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}"


def _publish_eval_result_to_phoenix(result: EvalRunResult, *, endpoint: str) -> None:
    from phoenix.client import Client

    client = Client(base_url=endpoint)
    _ensure_phoenix_project(client, _PROJECT_NAME)
    for run in result.runs:
        spans = cast(Any, _build_phoenix_spans(result, run))
        if spans:
            client.spans.log_spans(project_identifier=_PROJECT_NAME, spans=spans)
        annotations = cast(Any, _build_phoenix_span_annotations(result, run))
        if annotations:
            client.spans.log_span_annotations(span_annotations=annotations, sync=True)


def _ensure_phoenix_project(client: Any, project_name: str) -> None:
    try:
        client.projects.get(project_name=project_name)
    except Exception:
        client.projects.create(name=project_name)


def _build_phoenix_spans(
    result: EvalRunResult,
    run: EvalModeRunResult,
) -> list[dict[str, Any]]:
    timestamp = _phoenix_timestamp()
    run_identity = _phoenix_run_identity(result, run)
    return [
        {
            "name": f"policynim eval case {case.case_id}",
            "context": _phoenix_span_context(run_identity, case.case_id),
            "span_kind": "CHAIN",
            "start_time": timestamp,
            "end_time": timestamp,
            "status_code": "OK" if case.passed else "ERROR",
            "status_message": "" if case.passed else " | ".join(case.failure_reasons),
            "attributes": _phoenix_span_attributes(result, run, case),
        }
        for case in run.case_results
    ]


def _build_phoenix_span_annotations(
    result: EvalRunResult,
    run: EvalModeRunResult,
) -> list[dict[str, Any]]:
    run_identity = _phoenix_run_identity(result, run)
    annotations: list[dict[str, Any]] = []
    for case in run.case_results:
        span_id = _phoenix_span_context(run_identity, case.case_id)["span_id"]
        annotations.extend(
            [
                _phoenix_span_annotation(
                    span_id=span_id,
                    name="case_passed",
                    label="passed" if case.passed else "failed",
                    score=1.0 if case.passed else 0.0,
                    explanation=" | ".join(case.failure_reasons),
                ),
                _phoenix_span_annotation(
                    span_id=span_id,
                    name="expected_chunk_recall",
                    score=case.metrics.expected_chunk_recall,
                ),
                _phoenix_span_annotation(
                    span_id=span_id,
                    name="expected_policy_recall",
                    score=case.metrics.expected_policy_recall,
                ),
                _phoenix_span_annotation(
                    span_id=span_id,
                    name="insufficient_context_correct",
                    label=("correct" if case.metrics.insufficient_context_correct else "incorrect"),
                    score=1.0 if case.metrics.insufficient_context_correct else 0.0,
                ),
            ]
        )
        if case.metrics.conformance_score is not None:
            annotations.append(
                _phoenix_span_annotation(
                    span_id=span_id,
                    name="conformance_score",
                    score=case.metrics.conformance_score,
                    label=(
                        "passed"
                        if case.metrics.conformance_passed
                        else "failed"
                        if case.metrics.conformance_passed is False
                        else None
                    ),
                )
            )
        if case.regeneration_result is not None:
            annotations.append(
                _phoenix_span_annotation(
                    span_id=span_id,
                    name="regeneration_passed",
                    label="passed" if case.regeneration_result.passed else "failed",
                    score=1.0 if case.regeneration_result.passed else 0.0,
                    explanation=case.regeneration_result.stop_reason,
                )
            )
    return annotations


def _phoenix_span_annotation(
    *,
    span_id: str,
    name: str,
    label: str | None = None,
    score: float | None = None,
    explanation: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if label is not None:
        result["label"] = label
    if score is not None:
        result["score"] = score
    if explanation:
        result["explanation"] = explanation
    return {
        "name": name,
        "annotator_kind": "CODE",
        "span_id": span_id,
        "result": result,
    }


def _phoenix_span_attributes(
    result: EvalRunResult,
    run: EvalModeRunResult,
    case: EvalCaseResult,
) -> dict[str, object]:
    conformance_result = case.conformance_result
    regeneration_result = case.regeneration_result
    evidence_trace = case.evidence_trace
    attributes: dict[str, object | None] = {
        "policynim.eval.suite_name": result.suite_name,
        "policynim.eval.suite_path": result.suite_path,
        "policynim.eval.workspace_path": result.workspace_path,
        "policynim.eval.mode": result.mode,
        "policynim.eval.backend": result.backend,
        "policynim.eval.rerank_enabled": run.rerank_enabled,
        "policynim.eval.rerank_mode": "rerank-on" if run.rerank_enabled else "rerank-off",
        "policynim.eval.result_json_path": run.result_json_path,
        "policynim.eval.report_html_path": run.report_html_path,
        "policynim.eval.case_id": case.case_id,
        "policynim.eval.case_kind": case.kind,
        "policynim.eval.domain": case.domain or "",
        "policynim.eval.top_k": case.top_k,
        "policynim.eval.passed": case.passed,
        "policynim.eval.failure_reasons": list(case.failure_reasons),
        "policynim.eval.expected_insufficient_context": case.expected_insufficient_context,
        "policynim.eval.actual_insufficient_context": case.actual_insufficient_context,
        "policynim.eval.expected_chunk_ids": list(case.expected_chunk_ids),
        "policynim.eval.actual_chunk_ids": list(case.actual_chunk_ids),
        "policynim.eval.matched_chunk_ids": list(case.matched_chunk_ids),
        "policynim.eval.expected_policy_ids": list(case.expected_policy_ids),
        "policynim.eval.actual_policy_ids": list(case.actual_policy_ids),
        "policynim.eval.matched_policy_ids": list(case.matched_policy_ids),
        "policynim.eval.expected_chunk_recall": case.metrics.expected_chunk_recall,
        "policynim.eval.expected_policy_recall": case.metrics.expected_policy_recall,
        "policynim.eval.insufficient_context_correct": (case.metrics.insufficient_context_correct),
        "policynim.eval.evidence_trace_chunk_count": (
            len(evidence_trace.chunks) if evidence_trace is not None else 0
        ),
        "policynim.eval.evidence_trace_constraint_count": (
            len(evidence_trace.constraints) if evidence_trace is not None else 0
        ),
        "policynim.eval.evidence_trace_conformance_check_count": (
            len(evidence_trace.conformance_checks) if evidence_trace is not None else 0
        ),
        "policynim.eval.conformance_passed": (
            conformance_result.passed if conformance_result is not None else None
        ),
        "policynim.eval.conformance_score": (
            conformance_result.overall_score if conformance_result is not None else None
        ),
        "policynim.eval.conformance_failure_reasons": (
            list(conformance_result.failure_reasons) if conformance_result is not None else []
        ),
        "policynim.eval.regeneration_attempt_count": (
            len(regeneration_result.attempts) if regeneration_result is not None else 0
        ),
        "policynim.eval.regeneration_stop_reason": (
            regeneration_result.stop_reason if regeneration_result is not None else ""
        ),
        "policynim.eval.regeneration_passed": (
            regeneration_result.passed if regeneration_result is not None else None
        ),
    }
    return {key: value for key, value in attributes.items() if value is not None}


def _phoenix_run_identity(result: EvalRunResult, run: EvalModeRunResult) -> str:
    rerank_mode = "rerank-on" if run.rerank_enabled else "rerank-off"
    result_filename = Path(run.result_json_path).name
    return "|".join(
        [
            result.suite_name,
            result.mode,
            result.backend,
            rerank_mode,
            result_filename,
        ]
    )


def _phoenix_span_context(run_identity: str, case_id: str) -> dict[str, str]:
    digest = hashlib.sha256(f"{run_identity}|{case_id}".encode()).hexdigest()
    return {
        "trace_id": _nonzero_otel_id(digest[:32]),
        "span_id": _nonzero_otel_id(digest[32:48]),
    }


def _nonzero_otel_id(value: str) -> str:
    if set(value) == {"0"}:
        return "1" + value[1:]
    return value


def _phoenix_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _create_live_search_service(settings: Settings, *, rerank_enabled: bool) -> SearchService:
    from policynim.providers import NVIDIAEmbedder, NVIDIAReranker

    return SearchService(
        embedder=NVIDIAEmbedder.from_settings(settings),
        index_store=LanceDBIndexStore(
            uri=resolve_runtime_path(settings.lancedb_uri),
            table_name=settings.lancedb_table,
        ),
        reranker=NVIDIAReranker.from_settings(settings) if rerank_enabled else None,
    )


def _create_live_preflight_service(
    settings: Settings,
    *,
    rerank_enabled: bool,
) -> PreflightService:
    from policynim.providers import (
        NVIDIAEmbedder,
        NVIDIAGenerator,
        NVIDIAPolicyCompiler,
        NVIDIAReranker,
    )

    return PreflightService(
        embedder=NVIDIAEmbedder.from_settings(settings),
        index_store=LanceDBIndexStore(
            uri=resolve_runtime_path(settings.lancedb_uri),
            table_name=settings.lancedb_table,
        ),
        reranker=(
            NVIDIAReranker.from_settings(settings) if rerank_enabled else _PassThroughReranker()
        ),
        generator=NVIDIAGenerator.from_settings(settings),
        compiler=NVIDIAPolicyCompiler.from_settings(settings),
    )


def _create_live_conformance_service(
    settings: Settings,
    *,
    backend: EvalBackend,
) -> PolicyConformanceService:
    from policynim.providers import (
        NeMoAgentToolkitPolicyConformanceEvaluator,
        NeMoEvaluatorPolicyConformanceEvaluator,
        NVIDIAPolicyConformanceEvaluator,
    )

    if backend == "nemo":
        evaluator = NVIDIAPolicyConformanceEvaluator.from_settings(settings)
    elif backend == "nemo_evaluator":
        evaluator = NeMoEvaluatorPolicyConformanceEvaluator.from_settings(settings)
    elif backend == "nat":
        evaluator = NeMoAgentToolkitPolicyConformanceEvaluator.from_settings(settings)
    else:
        raise ValueError("default backend does not create a conformance service.")
    return PolicyConformanceService(
        evaluator=evaluator,
    )


def _create_live_regeneration_service(
    settings: Settings,
    *,
    backend: EvalBackend,
    rerank_enabled: bool,
) -> PolicyRegenerationService:
    from policynim.providers import (
        NVIDIAEmbedder,
        NVIDIAGenerator,
        NVIDIAPolicyCompiler,
        NVIDIAReranker,
    )

    compiler_service: PolicyCompilerService | None = None
    generator: Generator | None = None
    conformance_service: PolicyConformanceService | None = None
    try:
        compiler_service = PolicyCompilerService(
            embedder=NVIDIAEmbedder.from_settings(settings),
            index_store=LanceDBIndexStore(
                uri=resolve_runtime_path(settings.lancedb_uri),
                table_name=settings.lancedb_table,
            ),
            reranker=(
                NVIDIAReranker.from_settings(settings) if rerank_enabled else _PassThroughReranker()
            ),
            compiler=NVIDIAPolicyCompiler.from_settings(settings),
        )
        generator = NVIDIAGenerator.from_settings(settings)
        conformance_service = _create_live_conformance_service(
            settings,
            backend=backend,
        )
        return PolicyRegenerationService(
            compiler_service=compiler_service,
            generator=generator,
            conformance_service=conformance_service,
        )
    except Exception:
        _close_component(conformance_service)
        _close_component(generator)
        _close_component(compiler_service)
        raise


def _close_component(component: object | None) -> None:
    close = getattr(component, "close", None)
    if callable(close):
        close()


class _PassThroughReranker(Reranker):
    """Keep dense candidate order unchanged for rerank-off comparisons."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[ScoredChunk],
        *,
        top_k: int,
    ) -> list[ScoredChunk]:
        return list(candidates[:top_k])

    def close(self) -> None:
        return None


class _OfflineEmbedder:
    """Return deterministic embeddings for eval fixture queries."""

    def __init__(self) -> None:
        self._vectors = {
            case_input: [float(index)]
            for index, case_input in enumerate(_OFFLINE_QUERY_CANDIDATES, start=1)
        }

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        if text not in self._vectors:
            return [-999.0]
        return self._vectors[text]


class _OfflineIndexStore(IndexStore):
    """Serve deterministic dense candidates for offline evals."""

    def __init__(self, candidates_by_query: dict[str, list[ScoredChunk]]) -> None:
        self._candidates_by_query = candidates_by_query
        self._vector_to_query = {
            tuple([float(index)]): query for index, query in enumerate(candidates_by_query, start=1)
        }

    def replace(self, chunks: Sequence[Any]) -> None:
        return None

    def exists(self) -> bool:
        return True

    def count(self) -> int:
        return sum(len(chunks) for chunks in self._candidates_by_query.values())

    def list_chunks(self) -> list[Any]:
        unique_chunks: dict[str, ScoredChunk] = {}
        for chunks in self._candidates_by_query.values():
            for chunk in chunks:
                unique_chunks.setdefault(chunk.chunk_id, chunk)
        return [
            PolicyChunk(**chunk.model_dump(exclude={"score"})) for chunk in unique_chunks.values()
        ]

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        domain: str | None = None,
    ) -> list[ScoredChunk]:
        query = self._vector_to_query.get(tuple(float(value) for value in query_embedding), "")
        candidates = list(self._candidates_by_query.get(query, []))
        if domain is not None:
            candidates = [chunk for chunk in candidates if chunk.policy.domain == domain]
        return candidates[:top_k]


class _OfflineReranker(Reranker):
    """Reorder offline dense candidates to make rerank deltas observable."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[ScoredChunk],
        *,
        top_k: int,
    ) -> list[ScoredChunk]:
        order = _OFFLINE_RERANK_ORDERS.get(_offline_rerank_key(query), [])
        positions = {chunk_id: index for index, chunk_id in enumerate(order)}
        ranked = sorted(
            list(candidates),
            key=lambda chunk: positions.get(chunk.chunk_id, len(positions)),
        )
        return ranked[:top_k]

    def close(self) -> None:
        return None


def _offline_rerank_key(query: str) -> str:
    return query.split(" Task type:", 1)[0]


class _OfflineGenerator(Generator):
    """Produce deterministic grounded drafts from retained offline context."""

    def generate_preflight(
        self,
        request: PreflightRequest,
        context: Sequence[ScoredChunk],
        *,
        compiled_packet: CompiledPolicyPacket | None = None,
        regeneration_context: RegenerationContext | None = None,
    ) -> GeneratedPreflightDraft:
        del compiled_packet
        del regeneration_context
        context_by_id = {chunk.chunk_id: chunk for chunk in context}
        if request.task == "Implement a refresh-token cleanup background job":
            return _cleanup_job_draft(context_by_id)
        if request.task == "Add request ids to backend logs":
            if "BACKEND-LOG-1" not in context_by_id:
                return _insufficient_draft()
            return GeneratedPreflightDraft(
                summary="Use explicit request ids in backend logs.",
                applicable_policies=[
                    GeneratedPolicyGuidance(
                        policy_id="BACKEND-LOG-001",
                        title="Backend Logging Standard",
                        rationale="Request identifiers keep backend logs traceable.",
                        citation_ids=["BACKEND-LOG-1"],
                    )
                ],
                implementation_guidance=["Thread request ids through log context."],
                review_flags=["Do not emit unstructured log lines."],
                tests_required=["Add a regression test for request-id logging."],
                citation_ids=["BACKEND-LOG-1"],
            )
        return _insufficient_draft()

    def close(self) -> None:
        return None


class _OfflinePolicyCompiler:
    """Produce deterministic compiled constraints from retained offline context."""

    def compile_policy_packet(
        self,
        request: CompileRequest,
        selection_packet: PolicySelectionPacket,
        context: Sequence[ScoredChunk],
    ) -> GeneratedCompiledPolicyDraft:
        del request, selection_packet
        context_by_id = {chunk.chunk_id: chunk for chunk in context}
        required_steps: list[GeneratedPolicyConstraint] = []
        test_expectations: list[GeneratedPolicyConstraint] = []
        forbidden_patterns: list[GeneratedPolicyConstraint] = []

        if "BACKGROUND-JOB-1" in context_by_id:
            required_steps.append(
                GeneratedPolicyConstraint(
                    statement="Make the cleanup job idempotent and observable.",
                    citation_ids=["BACKGROUND-JOB-1"],
                )
            )
            test_expectations.append(
                GeneratedPolicyConstraint(
                    statement="Add coverage for repeated cleanup runs.",
                    citation_ids=["BACKGROUND-JOB-1"],
                )
            )
        if "SECURITY-TOKEN-1" in context_by_id:
            forbidden_patterns.append(
                GeneratedPolicyConstraint(
                    statement="Do not log raw refresh-token values.",
                    citation_ids=["SECURITY-TOKEN-1"],
                )
            )
            test_expectations.append(
                GeneratedPolicyConstraint(
                    statement="Add a test that active tokens are preserved.",
                    citation_ids=["SECURITY-TOKEN-1"],
                )
            )
        if "BACKEND-LOG-1" in context_by_id:
            required_steps.append(
                GeneratedPolicyConstraint(
                    statement="Thread request ids through log context.",
                    citation_ids=["BACKEND-LOG-1"],
                )
            )

        return GeneratedCompiledPolicyDraft(
            required_steps=required_steps,
            forbidden_patterns=forbidden_patterns,
            test_expectations=test_expectations,
            insufficient_context=not (required_steps or forbidden_patterns or test_expectations),
        )

    def close(self) -> None:
        return None


class _OfflinePolicyConformanceEvaluator:
    """Produce deterministic conformance judgments for offline evals."""

    def evaluate_policy_conformance(
        self,
        request: PolicyConformanceRequest,
    ) -> GeneratedPolicyConformanceDraft:
        trajectory_score = 1.0 if request.trace_steps else None
        return GeneratedPolicyConformanceDraft(
            final_adherence_score=1.0,
            final_adherence_rationale="Offline preflight output follows compiled policy evidence.",
            trajectory_adherence_score=trajectory_score,
            trajectory_adherence_rationale=(
                "Offline trace steps preserve compile and generation flow."
                if trajectory_score is not None
                else None
            ),
            constraint_ids=_offline_policy_conformance_constraint_ids(request.compiled_packet),
            chunk_ids=[citation.chunk_id for citation in request.result.citations],
            failure_reasons=[],
        )

    def close(self) -> None:
        return None


def _offline_policy_conformance_constraint_ids(
    compiled_packet: CompiledPolicyPacket,
) -> list[str]:
    categories: tuple[tuple[str, Sequence[CompiledPolicyConstraint]], ...] = (
        ("required_steps", compiled_packet.required_steps),
        ("forbidden_patterns", compiled_packet.forbidden_patterns),
        ("architectural_expectations", compiled_packet.architectural_expectations),
        ("test_expectations", compiled_packet.test_expectations),
        ("style_constraints", compiled_packet.style_constraints),
    )
    return [
        f"{category_name}:{index}"
        for category_name, constraints in categories
        for index, _constraint in enumerate(constraints)
    ]


def _cleanup_job_draft(context_by_id: dict[str, ScoredChunk]) -> GeneratedPreflightDraft:
    applicable_policies: list[GeneratedPolicyGuidance] = []
    citation_ids: list[str] = []
    implementation_guidance: list[str] = []
    review_flags: list[str] = []
    tests_required: list[str] = []

    if "BACKGROUND-JOB-1" in context_by_id:
        applicable_policies.append(
            GeneratedPolicyGuidance(
                policy_id="BACKGROUND-JOB-001",
                title="Background Job Design Rules",
                rationale="Cleanup jobs must remain idempotent and observable.",
                citation_ids=["BACKGROUND-JOB-1"],
            )
        )
        citation_ids.append("BACKGROUND-JOB-1")
        implementation_guidance.append("Make the cleanup job idempotent and observable.")
        tests_required.append("Add coverage for repeated cleanup runs.")

    if "SECURITY-TOKEN-1" in context_by_id:
        applicable_policies.append(
            GeneratedPolicyGuidance(
                policy_id="SECURITY-TOKEN-001",
                title="Session Lifetime And Token Boundaries",
                rationale="Token cleanup must preserve revocation and avoid leakage.",
                citation_ids=["SECURITY-TOKEN-1"],
            )
        )
        citation_ids.append("SECURITY-TOKEN-1")
        review_flags.append("Do not log raw refresh-token values.")
        tests_required.append("Add a test that active tokens are preserved.")

    if not applicable_policies:
        return _insufficient_draft()

    return GeneratedPreflightDraft(
        summary="Use background-job safeguards and token-handling rules for cleanup.",
        applicable_policies=applicable_policies,
        implementation_guidance=implementation_guidance,
        review_flags=review_flags,
        tests_required=tests_required,
        citation_ids=citation_ids,
    )


def _insufficient_draft() -> GeneratedPreflightDraft:
    return GeneratedPreflightDraft(
        summary="PolicyNIM could not find enough grounded policy evidence for this task.",
        applicable_policies=[],
        implementation_guidance=[],
        review_flags=[],
        tests_required=[],
        citation_ids=[],
        insufficient_context=True,
    )


def _chunk(
    *,
    chunk_id: str,
    policy_id: str,
    title: str,
    domain: str,
    path: str,
    text: str,
    score: float,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        path=path,
        section="Rules",
        lines="1-4",
        text=text,
        policy=PolicyMetadata(
            policy_id=policy_id,
            title=title,
            doc_type="guidance",
            domain=domain,
        ),
        score=score,
    )


_BACKEND_LOG_CHUNK = _chunk(
    chunk_id="BACKEND-LOG-1",
    policy_id="BACKEND-LOG-001",
    title="Backend Logging Standard",
    domain="backend",
    path="policies/backend/backend-logging-standard.md",
    text="Use request ids in backend logs for write paths.",
    score=0.62,
)
_BACKGROUND_JOB_CHUNK = _chunk(
    chunk_id="BACKGROUND-JOB-1",
    policy_id="BACKGROUND-JOB-001",
    title="Background Job Design Rules",
    domain="backend",
    path="policies/architecture/background-job-design-rules.md",
    text="Background cleanup jobs must be idempotent and observable.",
    score=0.55,
)
_SECURITY_TOKEN_CHUNK = _chunk(
    chunk_id="SECURITY-TOKEN-1",
    policy_id="SECURITY-TOKEN-001",
    title="Session Lifetime And Token Boundaries",
    domain="security",
    path="policies/security/session-lifetime-and-token-boundaries.md",
    text="Do not log raw token values and preserve revocation semantics.",
    score=0.51,
)

_OFFLINE_QUERY_CANDIDATES: dict[str, list[ScoredChunk]] = {
    "request ids in backend logs": [
        _BACKGROUND_JOB_CHUNK,
        _BACKEND_LOG_CHUNK,
    ],
    "refresh token cleanup background job": [
        _BACKEND_LOG_CHUNK,
        _BACKGROUND_JOB_CHUNK,
        _SECURITY_TOKEN_CHUNK,
    ],
    "graphql federation schema ownership": [],
    "Implement a refresh-token cleanup background job": [
        _BACKEND_LOG_CHUNK,
        _BACKGROUND_JOB_CHUNK,
        _SECURITY_TOKEN_CHUNK,
    ],
    "Add request ids to backend logs": [
        _BACKGROUND_JOB_CHUNK,
        _BACKEND_LOG_CHUNK,
    ],
    "Implement video transcoding billing exporter": [],
}

_OFFLINE_RERANK_ORDERS: dict[str, list[str]] = {
    "request ids in backend logs": ["BACKEND-LOG-1", "BACKGROUND-JOB-1"],
    "refresh token cleanup background job": [
        "BACKGROUND-JOB-1",
        "SECURITY-TOKEN-1",
        "BACKEND-LOG-1",
    ],
    "Implement a refresh-token cleanup background job": [
        "BACKGROUND-JOB-1",
        "SECURITY-TOKEN-1",
        "BACKEND-LOG-1",
    ],
    "Add request ids to backend logs": ["BACKEND-LOG-1", "BACKGROUND-JOB-1"],
}
