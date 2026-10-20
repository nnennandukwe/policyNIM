"""Tests for the eval service."""

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


class MockSearchService:
    """Static search service used for live-eval isolation tests."""

    def __init__(self, generation_id: str | None = None) -> None:
        """Retain the generation ID for deterministic result evidence."""
        self.generation_id = generation_id

    def search(self, request) -> SearchResult:
        """Return offline search evidence bound to the selected generation."""
        return SearchResult(
            generation_id=self.generation_id,
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


class MockPreflightService:
    """Static preflight service used for live-eval isolation tests."""

    def __init__(self, generation_id: str | None = None) -> None:
        """Retain the generation ID for deterministic result evidence."""
        self.generation_id = generation_id

    def preflight(self, request) -> PreflightResult:
        """Return an offline preflight result for the selected generation."""
        return PreflightResult(
            generation_id=self.generation_id,
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
        """Retain matching compiled-packet and result evidence for offline tests."""
        result = self.preflight(request)
        return PreflightTraceResult(
            result=result,
            compiled_packet=CompiledPolicyPacket(
                generation_id=self.generation_id,
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


class MockProcess:
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
    assert all(
        chunk.text is None
        for case in preflight_cases
        if case.regeneration_result is not None
        for chunk in case.regeneration_result.evidence_trace.chunks
    )
    assert all(
        chunk.text is None
        for case in preflight_cases
        if case.regeneration_result is not None
        for attempt in case.regeneration_result.attempts
        for chunk in attempt.evidence_trace.chunks
    )


def test_live_eval_pins_one_generation_across_modes_and_publication(
    monkeypatch, tmp_path: Path
) -> None:
    """Use the real lease/retention boundary with offline query/generation doubles."""
    from test_ingest_service import MockEmbedder
    from test_policy_generations import seed_legacy

    import policynim.services.ingest as ingest_module
    from policynim.services.generations import GenerationStore

    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(
        update={
            "generations_dir": tmp_path / "generations",
            "eval_workspace_dir": tmp_path / "workspace",
        }
    )
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    original = store.migrate(settings, index_path=index, rules_path=rules).generation_id
    with store.pin() as snapshot:
        suite_path = tmp_path / "reviewed-suite.json"
        suite_path.write_text(
            json.dumps(
                {
                    "name": "reader-pinning",
                    "expected_catalog_digest": snapshot.catalog.digest,
                    "cases": [{"case_id": "read", "kind": "search", "input": "backend logs"}],
                }
            )
        )
    before = index.read_bytes(), rules.read_bytes()
    seen: list[str | None] = []

    def forbid_embedder(settings):
        """Evaluation must never construct an ingestion provider."""
        raise AssertionError("Implicit embedding ingestion")

    monkeypatch.setattr(ingest_module, "_create_default_embedder", forbid_embedder)

    def search_factory(active_settings, *, rerank_enabled):
        """Publish enough new states to put the original beyond normal retention."""
        seen.append(active_settings.generation_id)
        if len(seen) == 1:
            source = settings.corpus_dir
            assert source is not None
            member = next(source.rglob("*.md"))
            for number in range(4):
                member.write_text(member.read_text() + f"\nPublished revision {number}.\n")
                store.build(settings, embedder=MockEmbedder())
            store.prune(keep_recent=0)
            assert (store.root / "objects" / original).is_dir()
        return MockSearchService(active_settings.generation_id)

    monkeypatch.setattr("policynim.services.eval._create_live_search_service", search_factory)
    monkeypatch.setattr(
        "policynim.services.eval._create_live_preflight_service",
        lambda active_settings, rerank_enabled: MockPreflightService(active_settings.generation_id),
    )
    result = EvalService(settings=settings).run(
        mode="live", compare_rerank=True, suite_path=suite_path
    )
    assert seen == [original, original]
    assert settings.generation_id is None
    assert result.generation_id == original
    assert all(run.generation_id == original for run in result.runs)
    assert all(case.generation_id == original for run in result.runs for case in run.case_results)
    for run in result.runs:
        assert json.loads(Path(run.result_json_path).read_text())["generation_id"] == original
        assert original in Path(run.report_html_path).read_text()
    assert (index.read_bytes(), rules.read_bytes()) == before
    store.prune(keep_recent=0)
    assert not (store.root / "objects" / original).exists()


@pytest.mark.parametrize("existing_rules", [False, True])
def test_live_eval_requires_generation_before_provider_construction(
    monkeypatch, tmp_path: Path, existing_rules: bool
) -> None:
    """Standalone artifacts aren't silently rebuilt or accepted as a live job snapshot."""
    from policynim.services.generations import GenerationError

    index, rules = tmp_path / "legacy.sqlite3", tmp_path / "rules.json"
    index.write_bytes(b"preserved index")
    if existing_rules:
        rules.write_bytes(b"preserved rules")
    settings = Settings(
        index_db_path=index,
        runtime_rules_artifact_path=rules,
        generations_dir=tmp_path / "generations",
        eval_workspace_dir=tmp_path / "workspace",
    )

    def forbid_provider(*args, **kwargs):
        """Missing generation rejection must happen before graph construction."""
        raise AssertionError("Provider constructed before generation validation")

    monkeypatch.setattr("policynim.services.eval._create_live_search_service", forbid_provider)
    with pytest.raises(GenerationError):
        EvalService(settings=settings).run(mode="live")
    assert index.read_bytes() == b"preserved index"
    assert rules.read_bytes() == b"preserved rules" if existing_rules else not rules.exists()
    assert not settings.eval_workspace_dir.exists()


def test_offline_eval_doesnt_claim_configured_live_generation(tmp_path: Path) -> None:
    """Synthetic fixtures stay unassociated with an explicit live ID."""
    result = EvalService(
        settings=Settings(
            generation_id="a" * 64,
            generations_dir=tmp_path / "missing",
            eval_workspace_dir=tmp_path / "workspace",
        )
    ).run()
    assert result.generation_id is None
    assert all(run.generation_id is None for run in result.runs)
    assert all(case.generation_id is None for run in result.runs for case in run.case_results)


def test_eval_service_start_ui_fails_when_process_exits_early(monkeypatch, tmp_path: Path) -> None:
    service = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace"))
    process = MockProcess(returncodes=[1])
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
    process = MockProcess(returncodes=[None, None])
    port_checks = iter([False, True])
    popen_call: dict[str, object] = {}

    def mock_popen(command, **kwargs):
        popen_call["command"] = command
        popen_call["env"] = kwargs["env"]
        return process

    monkeypatch.setattr("policynim.services.eval.subprocess.Popen", mock_popen)
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
    process = MockProcess(returncodes=[None, None, None])
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
    """Check deterministic Phoenix publication creates a project only after confirmed absence."""
    service = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "workspace"))
    result = service.run(mode="offline", compare_rerank=False)

    class MockPhoenixClient:
        def __init__(self, *, base_url: str) -> None:
            self.base_url = base_url
            self.projects = MockPhoenixProjects()
            self.spans = MockPhoenixSpans()
            clients.append(self)

    class MockPhoenixProjects:
        def get(self, *, project_name: str) -> None:
            import httpx

            request = httpx.Request("GET", "http://127.0.0.1/projects/PolicyNIM%20Eval")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("Project not found", request=request, response=response)

        def create(self, *, name: str) -> dict[str, str]:
            return {"name": name}

    class MockPhoenixSpans:
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

    clients: list[MockPhoenixClient] = []
    mock_client_module = types.ModuleType("phoenix.client")
    setattr(mock_client_module, "Client", MockPhoenixClient)
    monkeypatch.setitem(sys.modules, "phoenix.client", mock_client_module)

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
