"""Tests for the Day 6 eval service."""

from __future__ import annotations

import json
from pathlib import Path

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


def test_eval_service_offline_run_persists_two_rerank_modes(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(eval_workspace_dir=tmp_path / "workspace")
    report_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "policynim.services.eval._build_evidently_report",
        lambda **kwargs: FakeReport(),
    )
    monkeypatch.setattr(
        "policynim.services.eval._add_report_to_workspace",
        lambda workspace_path, report, run_name: report_calls.append(
            {"workspace_path": workspace_path, "run_name": run_name}
        ),
    )

    result = EvalService(settings=settings).run(mode="offline")

    assert result.mode == "offline"
    assert len(result.runs) == 2
    assert result.comparison is not None
    assert "preflight-refresh-token-cleanup" in result.comparison.improved_case_ids
    assert "search-refresh-token-cleanup" in result.comparison.improved_case_ids
    assert all(Path(run.result_json_path).is_file() for run in result.runs)
    assert all(Path(run.report_html_path).is_file() for run in result.runs)
    assert len(report_calls) == 2


def test_eval_service_loads_alternate_suite_path(monkeypatch, tmp_path: Path) -> None:
    suite_path = tmp_path / "alt-suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "alt-suite",
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
    monkeypatch.setattr(
        "policynim.services.eval._build_evidently_report",
        lambda **kwargs: FakeReport(),
    )
    monkeypatch.setattr(
        "policynim.services.eval._add_report_to_workspace",
        lambda workspace_path, report, run_name: None,
    )

    result = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace")).run(
        mode="offline",
        cases_path=suite_path,
        compare_rerank=False,
    )

    assert result.suite_name == "alt-suite"
    assert len(result.runs) == 1
    assert result.runs[0].metrics.case_count == 1


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
        cases_path=Path("evals/default_cases.json"),
        compare_rerank=False,
    )

    assert result.mode == "live"
    assert seen_paths
    assert seen_paths[0] != settings.lancedb_uri
    assert settings.lancedb_uri == tmp_path / "caller-index"
