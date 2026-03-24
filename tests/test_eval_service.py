"""Tests for the Day 6 eval service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policynim.errors import PolicyNIMError
from policynim.services.eval import EvalService
from policynim.settings import Settings
from policynim.types import (
    PolicyMetadata,
    PreflightResult,
    ScoredChunk,
    SearchResult,
)


class FakeReport:
    """Minimal Evidently-like report stub."""

    def __init__(self) -> None:
        self.saved_paths: list[str] = []

    def save_html(self, path: str) -> None:
        Path(path).write_text("<html>report</html>", encoding="utf-8")
        self.saved_paths.append(path)


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


def test_eval_service_offline_run_persists_two_rerank_modes(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(eval_workspace_dir=tmp_path / "workspace")
    run_names: list[str] = []

    monkeypatch.setattr(
        "policynim.services.eval._build_evidently_report",
        lambda **kwargs: FakeReport(),
    )
    monkeypatch.setattr(
        "policynim.services.eval._add_report_to_workspace",
        lambda workspace_path, report, run_name: run_names.append(run_name),
    )

    result = EvalService(settings=settings).run(mode="offline")

    assert result.mode == "offline"
    assert len(result.runs) == 2
    assert result.comparison is not None
    assert "preflight-refresh-token-cleanup" in result.comparison.improved_case_ids
    assert "search-refresh-token-cleanup" in result.comparison.improved_case_ids
    assert all(Path(run.result_json_path).is_file() for run in result.runs)
    assert all(Path(run.report_html_path).is_file() for run in result.runs)
    assert len(run_names) == 2


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
    run_names: list[str] = []
    monkeypatch.setattr(
        "policynim.services.eval._build_evidently_report",
        lambda **kwargs: FakeReport(),
    )
    monkeypatch.setattr(
        "policynim.services.eval._add_report_to_workspace",
        lambda workspace_path, report, run_name: run_names.append(run_name),
    )

    result = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace")).run(
        mode="offline",
        compare_rerank=False,
    )

    assert result.suite_name == "../alt suite\\2026?"
    assert len(result.runs) == 1
    assert result.runs[0].metrics.case_count == 1
    assert "../alt suite\\2026?" in run_names[0]
    assert ".." not in Path(result.runs[0].result_json_path).name
    assert "/" not in Path(result.runs[0].result_json_path).name
    assert "\\" not in Path(result.runs[0].result_json_path).name


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
    monkeypatch.setattr(
        "policynim.services.eval._build_evidently_report",
        lambda **kwargs: FakeReport(),
    )
    monkeypatch.setattr(
        "policynim.services.eval._add_report_to_workspace",
        lambda workspace_path, report, run_name: None,
    )

    result = EvalService(settings=settings).run(
        mode="live",
        compare_rerank=False,
    )

    assert result.mode == "live"
    assert seen_paths
    assert seen_paths[0] != settings.lancedb_uri
    assert settings.lancedb_uri == tmp_path / "caller-index"


def test_eval_service_start_ui_fails_when_process_exits_early(monkeypatch, tmp_path: Path) -> None:
    service = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace"))
    process = FakeProcess(returncodes=[1])
    monkeypatch.setattr(
        "policynim.services.eval.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    with pytest.raises(PolicyNIMError, match="exited before startup completed"):
        service.start_ui(port=8015)

    assert (tmp_path / "workspace" / "ui" / "evidently.log").is_file()


def test_eval_service_start_ui_succeeds_when_port_becomes_reachable(
    monkeypatch, tmp_path: Path
) -> None:
    service = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace"))
    process = FakeProcess(returncodes=[None, None])
    port_checks = iter([False, True])
    monkeypatch.setattr(
        "policynim.services.eval.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        "policynim.services.eval._is_local_port_reachable",
        lambda port: next(port_checks),
    )
    monkeypatch.setattr("policynim.services.eval.time.sleep", lambda seconds: None)

    service.start_ui(port=8016)

    assert process.terminate_called is False


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
