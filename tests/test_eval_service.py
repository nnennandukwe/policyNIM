"""Tests for the Day 6 eval service."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from policynim.errors import PolicyNIMError
from policynim.services.eval import EvalService
from policynim.settings import Settings
from policynim.types import (
    CompiledPolicyPacket,
    EvalModeRunResult,
    PolicyMetadata,
    PreflightResult,
    PreflightTraceResult,
    ScoredChunk,
    SearchResult,
)


class FakeIngestService:
    """Capture the isolated live eval index path."""

    def __init__(self, settings: Settings, seen_paths: list[Path]) -> None:
        self._settings = settings
        self._seen_paths = seen_paths

    def run(self):
        self._seen_paths.append(self._settings.lancedb_uri)
        return None


class FakeSearchService:
    """Static search service used for live-eval isolation tests."""

    def search(self, request) -> SearchResult:
        return SearchResult(
            query=request.query,
            domain=request.domain,
            top_k=request.top_k,
            hits=[
                ScoredChunk(
                    chunk_id="BACKEND-LOG-1",
                    path="policies/backend/backend-logging-standard.md",
                    section="Rules",
                    lines="1-4",
                    text="Use request ids in backend logs for write paths.",
                    policy=PolicyMetadata(
                        policy_id="BACKEND-LOG-001",
                        title="Backend Logging Standard",
                        doc_type="guidance",
                        domain="backend",
                    ),
                    score=0.99,
                )
            ],
            insufficient_context=False,
        )

    def close(self) -> None:
        return None


class FakePreflightService:
    """Static preflight service used for live-eval isolation tests."""

    def preflight(self, request) -> PreflightResult:
        return PreflightResult(
            task=request.task,
            domain=request.domain,
            summary="Use explicit request ids in backend logs.",
            applicable_policies=[],
            implementation_guidance=[],
            review_flags=[],
            tests_required=[],
            citations=[],
            insufficient_context=True,
        )

    def preflight_with_trace(self, request) -> PreflightTraceResult:
        result = self.preflight(request)
        return PreflightTraceResult(
            result=result,
            compiled_packet=CompiledPolicyPacket(
                task=request.task,
                domain=request.domain,
                top_k=request.top_k,
                task_type="unknown",
                insufficient_context=True,
            ),
            retained_context=[],
            trace_steps=[],
        )

    def close(self) -> None:
        return None


class FakeProcess:
    """Subprocess stub with controllable lifecycle."""

    def __init__(self, returncodes: list[int | None] | None = None) -> None:
        self._returncodes = returncodes or [None]
        self.terminate_called = False

    def poll(self) -> int | None:
        if len(self._returncodes) > 1:
            return self._returncodes.pop(0)
        return self._returncodes[0]

    def terminate(self) -> None:
        self.terminate_called = True


def test_eval_service_offline_run_persists_two_rerank_modes(tmp_path: Path) -> None:
    settings = Settings(eval_workspace_dir=tmp_path / "workspace")

    result = EvalService(settings=settings).run(mode="offline")

    assert result.mode == "offline"
    assert result.backend == "default"
    assert len(result.runs) == 2
    assert all(
        case_result.conformance_result is None
        for run in result.runs
        for case_result in run.case_results
    )
    assert all(
        case_result.evidence_trace is None
        for run in result.runs
        for case_result in run.case_results
        if case_result.kind == "search"
    )
    assert any(
        case_result.evidence_trace is not None
        for run in result.runs
        for case_result in run.case_results
        if case_result.kind == "preflight"
    )
    assert result.comparison is not None
    assert "preflight-refresh-token-cleanup" in result.comparison.improved_case_ids
    assert "search-refresh-token-cleanup" in result.comparison.improved_case_ids
    assert all(Path(run.result_json_path).is_file() for run in result.runs)
    assert all(Path(run.report_html_path).is_file() for run in result.runs)
    persisted = EvalModeRunResult.model_validate(
        json.loads(Path(result.runs[0].result_json_path).read_text(encoding="utf-8"))
    )
    assert any(
        case.evidence_trace is not None
        for case in persisted.case_results
        if case.kind == "preflight"
    )
    persisted_preflight_trace = next(
        case.evidence_trace
        for case in persisted.case_results
        if case.kind == "preflight" and case.evidence_trace is not None
    )
    assert persisted_preflight_trace.chunks
    assert persisted_preflight_trace.chunks[0].text is None
    assert all(
        "PolicyNIM Eval Report" in Path(run.report_html_path).read_text(encoding="utf-8")
        for run in result.runs
    )


def test_eval_service_uses_original_suite_name_and_safe_artifact_slug(
    monkeypatch, tmp_path: Path
) -> None:
    suite_path = tmp_path / "default_cases.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "../alt suite\\2026?",
                "cases": [
                    {
                        "case_id": "search-no-answer",
                        "kind": "search",
                        "input": "graphql federation schema ownership",
                        "top_k": 2,
                        "expected_insufficient_context": True,
                        "expected_chunk_ids": [],
                        "expected_policy_ids": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("policynim.services.eval.resolve_eval_suite_path", lambda: suite_path)

    result = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace")).run(
        mode="offline",
        compare_rerank=False,
    )

    assert result.suite_name == "../alt suite\\2026?"
    assert len(result.runs) == 1
    assert result.runs[0].metrics.case_count == 1
    report_text = Path(result.runs[0].report_html_path).read_text(encoding="utf-8")
    assert "../alt suite\\2026?" in report_text
    assert ".." not in Path(result.runs[0].result_json_path).name
    assert "/" not in Path(result.runs[0].result_json_path).name
    assert "\\" not in Path(result.runs[0].result_json_path).name


def test_eval_service_nemo_backend_adds_preflight_conformance_results(tmp_path: Path) -> None:
    settings = Settings(eval_workspace_dir=tmp_path / "workspace")

    result = EvalService(settings=settings).run(
        mode="offline",
        backend="nemo",
        compare_rerank=False,
    )

    assert result.backend == "nemo"
    assert len(result.runs) == 1
    run = result.runs[0]
    search_cases = [case for case in run.case_results if case.kind == "search"]
    preflight_cases = [case for case in run.case_results if case.kind == "preflight"]
    assert all(case.conformance_result is None for case in search_cases)
    assert all(case.evidence_trace is None for case in search_cases)
    assert any(case.conformance_result is not None for case in preflight_cases)
    assert all(case.evidence_trace is not None for case in preflight_cases)
    conformance_trace = next(
        case.evidence_trace
        for case in preflight_cases
        if case.evidence_trace is not None and case.evidence_trace.conformance_checks
    )
    assert conformance_trace.conformance_checks[-1].constraint_ids
    assert conformance_trace.conformance_checks[-1].chunk_ids
    assert run.metrics.conformance_case_count == 2
    assert run.metrics.conformance_passed_count == 2
    assert run.metrics.conformance_score == 1.0


def test_eval_service_regeneration_runs_for_preflight_cases_only(tmp_path: Path) -> None:
    settings = Settings(eval_workspace_dir=tmp_path / "workspace")

    result = EvalService(settings=settings).run(
        mode="offline",
        backend="nemo",
        regenerate=True,
        max_regenerations=1,
        compare_rerank=False,
    )

    run = result.runs[0]
    search_cases = [case for case in run.case_results if case.kind == "search"]
    preflight_cases = [case for case in run.case_results if case.kind == "preflight"]
    assert all(case.regeneration_result is None for case in search_cases)
    assert all(case.conformance_result is None for case in search_cases)
    assert all(case.regeneration_result is not None for case in preflight_cases)
    assert all(
        case.conformance_result is not None or case.actual_insufficient_context
        for case in preflight_cases
    )
    assert all(
        case.regeneration_result is not None
        and case.regeneration_result.compiled_packet_id
        == case.regeneration_result.evidence_trace.compiled_packet_id
        for case in preflight_cases
    )


def test_eval_service_live_mode_uses_isolated_temp_index(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        lancedb_uri=tmp_path / "caller-index",
        eval_workspace_dir=tmp_path / "workspace",
    )
    seen_paths: list[Path] = []

    monkeypatch.setattr(
        "policynim.services.eval.create_ingest_service",
        lambda active_settings: FakeIngestService(active_settings, seen_paths),
    )
    monkeypatch.setattr(
        "policynim.services.eval._create_live_search_service",
        lambda active_settings, rerank_enabled: FakeSearchService(),
    )
    monkeypatch.setattr(
        "policynim.services.eval._create_live_preflight_service",
        lambda active_settings, rerank_enabled: FakePreflightService(),
    )

    result = EvalService(settings=settings).run(
        mode="live",
        compare_rerank=False,
    )

    assert result.mode == "live"
    assert seen_paths
    assert seen_paths[0] != settings.lancedb_uri
    assert settings.lancedb_uri == tmp_path / "caller-index"


def test_eval_service_live_nemo_backend_uses_isolated_conformance_service(
    monkeypatch, tmp_path: Path
) -> None:
    settings = Settings(
        lancedb_uri=tmp_path / "caller-index",
        eval_workspace_dir=tmp_path / "workspace",
    )
    seen_paths: list[Path] = []
    closed: list[bool] = []

    class FakeConformanceService:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        "policynim.services.eval.create_ingest_service",
        lambda active_settings: FakeIngestService(active_settings, seen_paths),
    )
    monkeypatch.setattr(
        "policynim.services.eval._create_live_search_service",
        lambda active_settings, rerank_enabled: FakeSearchService(),
    )
    monkeypatch.setattr(
        "policynim.services.eval._create_live_preflight_service",
        lambda active_settings, rerank_enabled: FakePreflightService(),
    )
    monkeypatch.setattr(
        "policynim.services.eval._create_live_conformance_service",
        lambda active_settings, *, backend: FakeConformanceService(),
    )

    result = EvalService(settings=settings).run(
        mode="live",
        backend="nemo",
        compare_rerank=False,
    )

    assert result.backend == "nemo"
    assert seen_paths
    assert seen_paths[0] != settings.lancedb_uri
    assert closed == [True]


def test_eval_service_start_ui_fails_when_process_exits_early(monkeypatch, tmp_path: Path) -> None:
    service = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace"))
    process = FakeProcess(returncodes=[1])
    monkeypatch.setattr(
        "policynim.services.eval.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    with pytest.raises(PolicyNIMError, match="exited before startup completed"):
        service.start_ui(port=8015)

    assert (tmp_path / "workspace" / "ui" / "phoenix.log").is_file()


def test_eval_service_start_ui_succeeds_when_port_becomes_reachable(
    monkeypatch, tmp_path: Path
) -> None:
    service = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace"))
    process = FakeProcess(returncodes=[None, None])
    port_checks = iter([False, True])
    popen_call: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        popen_call["command"] = command
        popen_call["env"] = kwargs["env"]
        return process

    monkeypatch.setattr("policynim.services.eval.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "policynim.services.eval._is_local_port_reachable",
        lambda port: next(port_checks),
    )
    monkeypatch.setattr("policynim.services.eval.time.sleep", lambda seconds: None)

    service.start_ui(port=8016)

    assert process.terminate_called is False
    assert popen_call["command"] == ["phoenix", "serve"]
    env = popen_call["env"]
    assert isinstance(env, dict)
    assert env["PHOENIX_WORKING_DIR"] == (tmp_path / "workspace" / "phoenix").as_posix()
    assert env["PHOENIX_HOST"] == "127.0.0.1"
    assert env["PHOENIX_PORT"] == "8016"
    assert env["PHOENIX_PROJECT_NAME"] == "PolicyNIM Eval"


def test_eval_service_start_ui_terminates_when_port_never_becomes_reachable(
    monkeypatch, tmp_path: Path
) -> None:
    service = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace"))
    process = FakeProcess(returncodes=[None, None, None])
    monotonic_values = iter([0.0, 1.0, 2.0, 6.0])
    monkeypatch.setattr(
        "policynim.services.eval.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr("policynim.services.eval._is_local_port_reachable", lambda port: False)
    monkeypatch.setattr("policynim.services.eval.time.sleep", lambda seconds: None)
    monkeypatch.setattr("policynim.services.eval.time.monotonic", lambda: next(monotonic_values))

    with pytest.raises(PolicyNIMError, match="did not become reachable in time"):
        service.start_ui(port=8017)

    assert process.terminate_called is True


def test_eval_service_publish_to_ui_logs_deterministic_spans_and_annotations(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace"))
    result = service.run(mode="offline", compare_rerank=False)

    class FakePhoenixClient:
        def __init__(self, *, base_url: str) -> None:
            self.base_url = base_url
            self.projects = FakePhoenixProjects()
            self.spans = FakePhoenixSpans()
            clients.append(self)

    class FakePhoenixProjects:
        def get(self, *, project_name: str) -> None:
            raise RuntimeError(f"missing project {project_name}")

        def create(self, *, name: str) -> dict[str, str]:
            return {"name": name}

    class FakePhoenixSpans:
        def __init__(self) -> None:
            self.logged_spans: list[dict[str, object]] = []
            self.logged_annotations: list[dict[str, object]] = []
            self.sync: bool | None = None

        def log_spans(self, *, project_identifier: str, spans) -> None:
            assert project_identifier == "PolicyNIM Eval"
            self.logged_spans.extend(spans)

        def log_span_annotations(self, *, span_annotations, sync: bool) -> None:
            self.sync = sync
            self.logged_annotations.extend(span_annotations)

    clients: list[FakePhoenixClient] = []
    fake_client_module = types.ModuleType("phoenix.client")
    setattr(fake_client_module, "Client", FakePhoenixClient)
    monkeypatch.setitem(sys.modules, "phoenix.client", fake_client_module)

    service.publish_to_ui(result, port=8123)

    assert clients[0].base_url == "http://127.0.0.1:8123"
    spans = clients[0].spans.logged_spans
    annotations = clients[0].spans.logged_annotations
    assert len(spans) == len(result.runs[0].case_results)
    assert clients[0].spans.sync is True
    assert {span["span_kind"] for span in spans} == {"CHAIN"}
    assert {annotation["name"] for annotation in annotations} >= {
        "case_passed",
        "expected_chunk_recall",
        "expected_policy_recall",
        "insufficient_context_correct",
    }
    first_contexts = [span["context"] for span in spans]

    service.publish_to_ui(result, port=8123)

    assert [span["context"] for span in clients[1].spans.logged_spans] == first_contexts


def test_eval_service_publish_to_ui_surfaces_phoenix_endpoint_and_log_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace"))
    result = service.run(mode="offline", compare_rerank=False)

    def fail_publish(result, *, endpoint: str) -> None:
        raise RuntimeError(endpoint)

    monkeypatch.setattr("policynim.services.eval._publish_eval_result_to_phoenix", fail_publish)

    with pytest.raises(PolicyNIMError, match="http://127.0.0.1:8124") as exc_info:
        service.publish_to_ui(result, port=8124)

    assert "phoenix.log" in str(exc_info.value)
