"""Evaluation service for PolicyNIM search and grounded preflight."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Any

import pandas as pd

from policynim.contracts import Generator, IndexStore, Reranker
from policynim.runtime_paths import resolve_eval_suite_path, resolve_runtime_path
from policynim.services.ingest import create_ingest_service
from policynim.services.preflight import PreflightService
from policynim.services.search import SearchService
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import (
    EvalAggregateMetrics,
    EvalCase,
    EvalCaseMetrics,
    EvalCaseResult,
    EvalComparisonDelta,
    EvalExecutionMode,
    EvalModeRunResult,
    EvalRunResult,
    EvalSuite,
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    PolicyChunk,
    PolicyMetadata,
    PreflightRequest,
    PreflightResult,
    ScoredChunk,
    SearchRequest,
    SearchResult,
)

_PROJECT_NAME = "PolicyNIM Eval"
_PROJECT_ID_FILENAME = ".policynim-eval-project-id"


class EvalService:
    """Run PolicyNIM eval suites and persist comparable reports."""

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._workspace_path = resolve_runtime_path(settings.eval_workspace_dir)

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
        cases_path: Path | None = None,
        compare_rerank: bool = True,
    ) -> EvalRunResult:
        """Run the requested eval suite and persist the resulting reports."""
        suite_path = resolve_eval_suite_path(cases_path)
        suite = _load_eval_suite(suite_path)
        rerank_modes = [True, False] if compare_rerank else [True]

        mode_results: list[EvalModeRunResult] = []
        for rerank_enabled in rerank_modes:
            case_results = self._run_suite_cases(
                suite,
                mode=mode,
                rerank_enabled=rerank_enabled,
            )
            mode_results.append(
                self._persist_mode_run(
                    suite=suite,
                    suite_path=suite_path,
                    mode=mode,
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
            suite_name=suite.name,
            suite_path=suite_path.as_posix(),
            workspace_path=self._workspace_path.as_posix(),
            compare_rerank=compare_rerank,
            runs=mode_results,
            comparison=comparison,
        )

    def start_ui(self, *, port: int | None = None) -> None:
        """Start the Evidently local UI in the background."""
        self._run_ui(port=port, detach=True)

    def _run_ui(self, *, port: int | None, detach: bool) -> None:
        """Run or start the Evidently local UI against the PolicyNIM workspace."""
        resolved_port = port if port is not None else self._settings.eval_ui_port
        self._workspace_path.mkdir(parents=True, exist_ok=True)
        command = [
            "evidently",
            "ui",
            "--workspace",
            self._workspace_path.as_posix(),
            "--port",
            str(resolved_port),
        ]
        if detach:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return None
        subprocess.run(command, check=True)

    def _run_suite_cases(
        self,
        suite: EvalSuite,
        *,
        mode: EvalExecutionMode,
        rerank_enabled: bool,
    ) -> list[EvalCaseResult]:
        if mode == "offline":
            return self._run_offline_suite(suite, rerank_enabled=rerank_enabled)
        return self._run_live_suite(suite, rerank_enabled=rerank_enabled)

    def _run_offline_suite(
        self,
        suite: EvalSuite,
        *,
        rerank_enabled: bool,
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
        )
        try:
            return _score_suite_cases(
                suite.cases,
                search_service=search_service,
                preflight_service=preflight_service,
                rerank_enabled=rerank_enabled,
            )
        finally:
            search_service.close()
            preflight_service.close()

    def _run_live_suite(
        self,
        suite: EvalSuite,
        *,
        rerank_enabled: bool,
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
            try:
                return _score_suite_cases(
                    suite.cases,
                    search_service=search_service,
                    preflight_service=preflight_service,
                    rerank_enabled=rerank_enabled,
                )
            finally:
                search_service.close()
                preflight_service.close()

    def _persist_mode_run(
        self,
        *,
        suite: EvalSuite,
        suite_path: Path,
        mode: EvalExecutionMode,
        rerank_enabled: bool,
        case_results: list[EvalCaseResult],
    ) -> EvalModeRunResult:
        metrics = _aggregate_metrics(case_results)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        mode_slug = "rerank-on" if rerank_enabled else "rerank-off"

        results_dir = self._workspace_path / "results"
        reports_dir = self._workspace_path / "reports"
        results_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        result_payload = EvalModeRunResult(
            rerank_enabled=rerank_enabled,
            metrics=metrics,
            result_json_path="",
            report_html_path="",
            case_results=case_results,
        )
        result_json_path = results_dir / f"{timestamp}-{suite.name}-{mode_slug}.json"
        report_html_path = reports_dir / f"{timestamp}-{suite.name}-{mode_slug}.html"
        result_json_path.write_text(
            result_payload.model_copy(
                update={
                    "result_json_path": result_json_path.as_posix(),
                    "report_html_path": report_html_path.as_posix(),
                }
            ).model_dump_json(indent=2),
            encoding="utf-8",
        )

        report = _build_evidently_report(
            suite=suite,
            suite_path=suite_path,
            mode=mode,
            rerank_enabled=rerank_enabled,
            case_results=case_results,
            metrics=metrics,
        )
        report.save_html(report_html_path.as_posix())
        _add_report_to_workspace(
            self._workspace_path,
            report,
            run_name=f"{suite.name} | {mode} | {mode_slug}",
        )

        return EvalModeRunResult(
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
    rerank_enabled: bool,
) -> list[EvalCaseResult]:
    results: list[EvalCaseResult] = []
    for case in cases:
        if case.kind == "search":
            result = search_service.search(
                SearchRequest(query=case.input, domain=case.domain, top_k=case.top_k)
            )
            results.append(_score_search_case(case, result=result, rerank_enabled=rerank_enabled))
            continue

        result = preflight_service.preflight(
            PreflightRequest(task=case.input, domain=case.domain, top_k=case.top_k)
        )
        results.append(_score_preflight_case(case, result=result, rerank_enabled=rerank_enabled))
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


def _score_preflight_case(
    case: EvalCase,
    *,
    result: PreflightResult,
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
        metrics=EvalCaseMetrics(
            expected_chunk_recall=_recall(len(matched_chunk_ids), len(case.expected_chunk_ids)),
            expected_policy_recall=_recall(len(matched_policy_ids), len(case.expected_policy_ids)),
            insufficient_context_correct=(
                case.expected_insufficient_context == result.insufficient_context
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
    total_results = list(case_results)
    passed_count = sum(result.passed for result in total_results)
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


def _build_evidently_report(
    *,
    suite: EvalSuite,
    suite_path: Path,
    mode: EvalExecutionMode,
    rerank_enabled: bool,
    case_results: Sequence[EvalCaseResult],
    metrics: EvalAggregateMetrics,
):
    from evidently import DataDefinition, Dataset, Report
    from evidently.presets import DataSummaryPreset

    rows = [
        {
            "suite_name": suite.name,
            "suite_path": suite_path.as_posix(),
            "mode": mode,
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
            "expected_policy_ids": ",".join(result.expected_policy_ids),
            "actual_policy_ids": ",".join(result.actual_policy_ids),
            "failure_reasons": " | ".join(result.failure_reasons),
            "expected_chunk_recall": result.metrics.expected_chunk_recall,
            "expected_policy_recall": result.metrics.expected_policy_recall,
            "insufficient_context_correct": result.metrics.insufficient_context_correct,
            "actual_summary": result.actual_summary or "",
            "overall_pass_rate": metrics.overall_pass_rate,
            "expected_policy_recall_run": metrics.expected_policy_recall,
            "expected_chunk_recall_run": metrics.expected_chunk_recall,
        }
        for result in case_results
    ]
    frame = pd.DataFrame(rows)
    dataset = Dataset.from_pandas(frame, data_definition=DataDefinition())
    report = Report([DataSummaryPreset()])
    return report.run(dataset, None)


def _add_report_to_workspace(workspace_path: Path, report: Any, *, run_name: str) -> None:
    from evidently.ui.workspace import Workspace

    workspace_path.mkdir(parents=True, exist_ok=True)
    workspace = _create_workspace(workspace_path, workspace_class=Workspace)
    project = _get_or_create_project(workspace_path, workspace)
    workspace.add_run(project.id, report, include_data=True, name=run_name)


def _create_workspace(workspace_path: Path, *, workspace_class: Any) -> Any:
    return workspace_class.create(workspace_path.as_posix())


def _get_or_create_project(workspace_path: Path, workspace: Any) -> Any:
    project_id_path = workspace_path / _PROJECT_ID_FILENAME
    if project_id_path.is_file():
        project_id = project_id_path.read_text(encoding="utf-8").strip()
        if project_id:
            project = _workspace_get_project(workspace, project_id)
            if project is not None:
                return project

    project = _workspace_create_project(workspace, _PROJECT_NAME)
    project_id_path.write_text(str(project.id), encoding="utf-8")
    return project


def _workspace_get_project(workspace: Any, project_id: str) -> Any | None:
    getter = getattr(workspace, "get_project", None)
    if getter is None:
        return None

    try:
        return getter(project_id)
    except TypeError:
        try:
            return getter(project_id=project_id)
        except Exception:
            return None
    except Exception:
        return None


def _workspace_create_project(workspace: Any, project_name: str) -> Any:
    creator = getattr(workspace, "create_project")
    try:
        return creator(project_name)
    except TypeError:
        return creator(name=project_name)


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
    from policynim.providers import NVIDIAEmbedder, NVIDIAGenerator, NVIDIAReranker

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
    )


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
        order = _OFFLINE_RERANK_ORDERS.get(query, [])
        positions = {chunk_id: index for index, chunk_id in enumerate(order)}
        ranked = sorted(
            list(candidates),
            key=lambda chunk: positions.get(chunk.chunk_id, len(positions)),
        )
        return ranked[:top_k]

    def close(self) -> None:
        return None


class _OfflineGenerator(Generator):
    """Produce deterministic grounded drafts from retained offline context."""

    def generate_preflight(
        self,
        request: PreflightRequest,
        context: Sequence[ScoredChunk],
    ) -> GeneratedPreflightDraft:
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
