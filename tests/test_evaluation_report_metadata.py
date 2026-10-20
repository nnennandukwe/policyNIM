"""Run-level evaluation provenance through sealed sources and actual request serialization."""

from __future__ import annotations

import html
import json
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import OpenAI
from test_ingest_service import MockEmbedder
from test_nvidia_policy_conformance import make_request
from test_policy_profiles import write_pack

from policynim.errors import ProviderError
from policynim.nvidia_models import chat_request_configuration
from policynim.providers.nvidia import NVIDIAPolicyConformanceEvaluator
from policynim.services.compiler import PolicyCompilerService
from policynim.services.conformance import PolicyConformanceService, conformance_thresholds
from policynim.services.eval import EvalService
from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store
from policynim.services.preflight import PreflightService
from policynim.services.profiles import initialize_profile
from policynim.services.regeneration import PolicyRegenerationService
from policynim.services.search import SearchService
from policynim.settings import Settings
from policynim.source_units import SourceContext, SourceSelection
from policynim.types import (
    CompileRequest,
    EmbeddingIdentity,
    EvalCase,
    EvalModeRunResult,
    EvalSuite,
    GeneratedCompiledPolicyDraft,
    GeneratedPolicyConformanceDraft,
    GeneratedPreflightDraft,
    PolicyConformanceRequest,
    PolicySelectionPacket,
    PreflightRequest,
    ScoredChunk,
)


class NoChat:
    """Fail if a search-only suite unexpectedly invokes compilation or generation."""

    def compile_policy_packet(
        self,
        request: CompileRequest,
        selection_packet: PolicySelectionPacket,
        context: Sequence[ScoredChunk],
    ) -> GeneratedCompiledPolicyDraft:
        """Keep configured chat settings separate from actual model invocation evidence."""
        pytest.fail("Search-only evaluation invoked compilation.")

    def generate_preflight(
        self, request: PreflightRequest, context: Sequence[ScoredChunk], **kwargs: object
    ) -> GeneratedPreflightDraft:
        """Reject unexpected generation without accessing a provider."""
        pytest.fail("Search-only evaluation invoked generation.")


class ControlledChat:
    """Return known source-bound outputs for report plumbing without judging model quality."""

    def compile_policy_packet(
        self,
        request: CompileRequest,
        selection_packet: PolicySelectionPacket,
        context: Sequence[ScoredChunk],
    ) -> GeneratedCompiledPolicyDraft:
        """Bind one controlled requirement to the actual selected source chunk."""
        assert isinstance(context, SourceContext)
        return context.catalog.compiler_draft(
            SourceSelection(
                source_ids=[unit.source_id for unit in context.catalog.units],
                insufficient_context=False,
            )
        )

    def generate_preflight(
        self, request: PreflightRequest, context: Sequence[ScoredChunk], **kwargs: object
    ) -> GeneratedPreflightDraft:
        """Copy canonical source ownership while retaining its real citation coordinates."""
        assert isinstance(context, SourceContext)
        return context.catalog.preflight_draft(
            SourceSelection(
                source_ids=[unit.source_id for unit in context.catalog.units],
                insufficient_context=False,
            )
        )


class InsufficientRetryChat(ControlledChat):
    """Retain an earlier judged attempt before refusing a later regeneration attempt."""

    def generate_preflight(
        self, request: PreflightRequest, context: Sequence[ScoredChunk], **kwargs: object
    ) -> GeneratedPreflightDraft:
        """Return insufficient context only after the real service supplies retry context."""
        if kwargs.get("regeneration_context") is not None:
            return GeneratedPreflightDraft(
                summary="Sources are insufficient.", insufficient_context=True
            )
        return super().generate_preflight(request, context, **kwargs)


class ControlledJudge:
    """Supply known scores solely to verify threshold and report propagation."""

    def evaluate_policy_conformance(
        self, request: PolicyConformanceRequest
    ) -> GeneratedPolicyConformanceDraft:
        """Use valid compiled coordinates so a low trajectory score remains observable."""
        return GeneratedPolicyConformanceDraft(
            final_adherence_score=0.8,
            trajectory_adherence_score=0.7,
            constraint_ids=["required_steps:0"],
            chunk_ids=request.compiled_packet.required_steps[0].citation_ids,
        )


class Ranker:
    """Select query-named policies from actual SQLite candidates without changing sources."""

    def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, top_k: int
    ) -> list[ScoredChunk]:
        """Return real candidate coordinates for the named fixture policies."""
        return [
            c.model_copy(update={"score": 1.0})
            for c in candidates
            if c.policy.policy_id in query.split()
        ][:top_k]


@pytest.mark.parametrize("profile_enabled", [True, False])
@pytest.mark.parametrize("evaluation_kind", ["search", "conformance", "regeneration_insufficient"])
def test_public_run_records_pinned_gold_coverage_and_sanitized_effective_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile_enabled: bool, evaluation_kind: str
) -> None:
    """Real generation and evaluator paths persist actual executed gold coverage in both modes."""
    with_conformance = evaluation_kind != "search"
    regenerate = evaluation_kind == "regeneration_insufficient"
    first = write_pack(tmp_path / "sources" / "first", "public.first", ["A", "B"])
    second = write_pack(tmp_path / "sources" / "second", "team.second", ["C", "D"])
    unused = write_pack(tmp_path / "sources" / "unused", "team.unused", ["E"])
    profile = tmp_path / "profile.toml"
    initialize_profile(profile, [first, second, unused], profile_id="reviewed")
    if not profile_enabled:
        for root in (first, second, unused):
            (root / "pack.toml").unlink()
    no_env: dict[str, Any] = {"_env_file": None}
    settings = Settings(
        **no_env,
        profile=profile if profile_enabled else None,
        corpus_dir=None if profile_enabled else tmp_path / "sources",
        generations_dir=tmp_path / "generations",
        eval_workspace_dir=tmp_path / "evaluations",
        nvidia_api_key="PRIVATE-KEY-SENTINEL",
        nvidia_embed_model="test-embed",
        nvidia_rerank_model="test-rerank",
        nvidia_chat_model="nvidia/nemotron-3.5-lightning-30b-a3b",
        nvidia_base_url="https://example.invalid/v1",
        nvidia_timeout_seconds=7,
        nvidia_max_retries=2,
        operation_timeout_seconds=35,
    )
    store = GenerationStore(
        root=settings.generations_dir,
        embedding=EmbeddingIdentity(model="test-embed", endpoint="https://example.invalid/v1"),
    )
    publication = store.build(settings, MockEmbedder())
    with closing(create_index_store(settings)) as reader:
        chunk_c = next(c.chunk_id for c in reader.list_chunks() if c.policy.policy_id == "C")
    with store.pin() as snapshot:
        catalog_digest = snapshot.catalog.digest
        profile_digest = snapshot.catalog.profile_digest
    suite = EvalSuite(
        name="controlled-run-provenance",
        expected_catalog_digest=catalog_digest,
        cases=[
            EvalCase(case_id="a", kind="search", input="A", top_k=1, expected_policy_ids=["A"]),
            EvalCase(case_id="c", kind="search", input="C", top_k=2, expected_chunk_ids=[chunk_c]),
            EvalCase(
                case_id="both",
                kind="search",
                input="A C",
                top_k=3,
                expected_policy_ids=["A", "C"],
            ),
            EvalCase(
                case_id="negative",
                kind="search",
                input="unknown",
                top_k=4,
                expected_insufficient_context=True,
            ),
        ],
    )
    if with_conformance:
        suite.cases.append(
            EvalCase(
                case_id="preflight",
                kind="preflight",
                input="A",
                top_k=1,
                expected_policy_ids=["A"],
            )
        )
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(suite.model_dump_json())

    def search_factory(active: Settings, *, rerank_enabled: bool) -> SearchService:
        """Read actual pinned SQLite data with deterministic offline providers."""
        return SearchService(
            embedder=MockEmbedder(),
            index_store=create_index_store(active),
            reranker=Ranker() if rerank_enabled else None,
        )

    def preflight_factory(active: Settings, *, rerank_enabled: bool) -> PreflightService:
        """Keep the evaluator's unused preflight dependency sealed and offline."""
        return PreflightService(
            embedder=MockEmbedder(),
            index_store=create_index_store(active),
            reranker=Ranker(),
            compiler=ControlledChat() if with_conformance else NoChat(),
            generator=ControlledChat() if with_conformance else NoChat(),
        )

    def regeneration_factory(
        active: Settings, *, backend: str, rerank_enabled: bool
    ) -> PolicyRegenerationService:
        """Run actual regeneration over sealed chunks and controlled judged/insufficient outputs."""
        return PolicyRegenerationService(
            compiler_service=PolicyCompilerService(
                embedder=MockEmbedder(),
                index_store=create_index_store(active),
                reranker=Ranker(),
                compiler=ControlledChat(),
            ),
            generator=InsufficientRetryChat(),
            conformance_service=PolicyConformanceService(evaluator=ControlledJudge()),
        )

    monkeypatch.setattr(
        "policynim.services.eval._create_live_regeneration_service", regeneration_factory
    )
    monkeypatch.setattr("policynim.services.eval._create_live_search_service", search_factory)
    monkeypatch.setattr("policynim.services.eval._create_live_preflight_service", preflight_factory)
    monkeypatch.setattr(
        "policynim.services.eval._create_live_conformance_service",
        lambda *args, **kwargs: PolicyConformanceService(evaluator=ControlledJudge()),
    )
    report = EvalService(settings=settings).run(
        mode="live",
        suite_path=suite_path,
        backend="nemo" if with_conformance else "default",
        regenerate=regenerate,
    )
    assert report.generation_id == publication.generation_id
    assert len(report.runs) == 2
    for run in report.runs:
        config, coverage = run.effective_configuration, run.source_coverage
        assert config is not None and coverage is not None
        expected_top_k = {"a": 1, "c": 2, "both": 3, "negative": 4}
        if with_conformance:
            expected_top_k["preflight"] = 1
        assert config.top_k_by_case == expected_top_k
        assert config.embedding_model == "test-embed"
        assert config.reranking_model == ("test-rerank" if run.rerank_enabled else None)
        assert config.chat is not None
        assert config.chat.model == settings.nvidia_chat_model
        assert (config.chat.temperature, config.chat.top_p, config.chat.enable_thinking) == (
            0,
            1,
            False,
        )
        if regenerate:
            retried = next(case for case in run.case_results if case.case_id == "preflight")
            assert retried.conformance_result is None
            assert retried.regeneration_result is not None
            assert len(retried.regeneration_result.attempts) == 2
            assert retried.regeneration_result.attempts[0].conformance_result is not None
            assert retried.regeneration_result.attempts[1].conformance_result is None
            assert retried.actual_insufficient_context
        if with_conformance:
            assert config.conformance_thresholds == conformance_thresholds()
        else:
            assert config.conformance_thresholds is None
        assert (
            config.request_timeout_seconds,
            config.operation_timeout_seconds,
            config.max_retries,
        ) == (7, 35, 2)
        assert coverage.executed_case_count == 4 + int(with_conformance)
        assert coverage.profile_id == ("reviewed" if profile_enabled else None)
        assert coverage.profile_digest == profile_digest
        families = {(f.family, f.source): f for f in coverage.families}
        if profile_enabled:
            assert set(families) == {
                ("public.first", "pack"),
                ("team.second", "pack"),
                ("team.unused", "pack"),
                ("unassigned", "unassigned"),
            }
            assert families["public.first", "pack"].case_ids == (
                ["a", "both", "preflight"] if with_conformance else ["a", "both"]
            )
            assert families["team.second", "pack"].case_ids == ["c", "both"]
            assert families["team.unused", "pack"].case_count == 0
        else:
            assert set(families) == {("backend", "domain"), ("unassigned", "unassigned")}
            assert families["backend", "domain"].case_ids == (
                ["a", "c", "both", "preflight"] if with_conformance else ["a", "c", "both"]
            )
        assert families["unassigned", "unassigned"].case_ids == ["negative"]
        for family in families.values():
            assert family.case_count == len(family.case_ids)
            assert family.passed_case_count == len(family.passed_case_ids)
            preflight_count = int("preflight" in family.case_ids)
            assert family.conformance_case_count == preflight_count
            assert family.operation_counts == {
                "search": family.case_count - preflight_count,
                "preflight": preflight_count,
            }
        persisted = EvalModeRunResult.model_validate_json(Path(run.result_json_path).read_text())
        assert persisted.effective_configuration == config
        assert persisted.source_coverage == coverage
        rendered = html.unescape(Path(run.report_html_path).read_text())
        assert config.model_dump_json() in rendered
        assert coverage.model_dump_json() in rendered
        assert "PRIVATE-KEY-SENTINEL" not in rendered + Path(run.result_json_path).read_text()


@pytest.mark.parametrize(
    "model,temperature,top_p,thinking,schema",
    [
        ("nvidia/nemotron-3.5-lightning-30b-a3b", 0, 1, False, True),
        ("nvidia/nemotron-3-super-120b-a12b", 1, 0.95, False, False),
        ("explicit-custom-model", 0, 1, None, False),
    ],
)
@pytest.mark.parametrize("reference_case", ["valid", "foreign_constraint", "foreign_chunk"])
def test_conformance_actual_wire_matches_reported_configuration_and_rejects_foreign_ids(
    model: str,
    temperature: float,
    top_p: float,
    thinking: bool | None,
    schema: bool,
    reference_case: str,
) -> None:
    """Exercise real SDK JSON serialization and strict allowed IDs without a live judge call."""
    requests: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Capture the outgoing request and return typed fixture scores and source references."""
        requests.append(json.loads(request.content))
        content = {
            "final_adherence_score": 0.8,
            "trajectory_adherence_score": 0.7,
            "constraint_ids": [
                "foreign:0" if reference_case == "foreign_constraint" else "required_steps:0"
            ],
            "chunk_ids": ["FOREIGN" if reference_case == "foreign_chunk" else "BACKEND-1"],
        }
        return httpx.Response(
            200,
            json={
                "id": "offline-fixture",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(content),
                        },
                    }
                ],
            },
        )

    client = OpenAI(
        api_key="offline-only",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    with closing(client):
        evaluator = NVIDIAPolicyConformanceEvaluator(
            api_key="offline-only",
            model=model,
            base_url="https://example.invalid/v1",
            timeout_seconds=2,
            max_retries=0,
            client=client,
        )
        if reference_case != "valid":
            error = "constraint" if reference_case == "foreign_constraint" else "chunk"
            with pytest.raises(ProviderError, match=f"unsupported {error} ids"):
                evaluator.evaluate_policy_conformance(make_request())
        else:
            result = PolicyConformanceService(evaluator=evaluator).evaluate(
                make_request(), backend="nemo"
            )
            metrics = {metric.name: metric for metric in result.metrics}
            thresholds = conformance_thresholds()
            assert (thresholds.final_adherence, thresholds.trajectory_adherence) == (0.75, 0.75)
            assert metrics["final_adherence"].score == 0.8
            assert metrics["final_adherence"].passed
            assert metrics["trajectory_adherence"].score == 0.7
            assert not metrics["trajectory_adherence"].passed
    assert len(requests) == 1
    body = requests[0]
    config = chat_request_configuration(model)
    assert body["model"] == config.model == model
    assert body["temperature"] == config.temperature == temperature
    assert body["top_p"] == config.top_p == top_p
    assert config.enable_thinking is thinking
    assert body.get("chat_template_kwargs") == (
        None if thinking is None else {"enable_thinking": thinking}
    )
    assert ("response_format" in body) is schema
    if schema:
        assert config.output_mode == "json_schema"
        response_format = body["response_format"]
        assert isinstance(response_format, dict)
        assert response_format["type"] == "json_schema"
    else:
        assert config.output_mode == "default"


def test_historical_run_readability_preserves_case_serialization_hashes() -> None:
    """Backward-default run metadata doesn't alter retained historical case JSON digests."""
    import hashlib

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "eval-report-before-provenance.json").read_text()
    )
    assert "cda87acc54bfdc25cdd70a1cce234e22e5205d4a" in fixture["origin"]
    assert "effective_configuration" not in fixture["mode"]
    assert "source_coverage" not in fixture["mode"]
    historical = EvalModeRunResult.model_validate(fixture["mode"])
    assert historical.effective_configuration is None
    assert historical.source_coverage is None
    assert {
        case.case_id: hashlib.sha256(case.model_dump_json().encode()).hexdigest()
        for case in historical.case_results
    } == fixture["case_json_sha256"]


@pytest.mark.parametrize("backend", ["default", "nemo"])
def test_actual_offline_reports_capture_synthetic_settings_and_only_executed_thresholds(
    tmp_path: Path, backend
) -> None:
    """Actual fixture scoring never labels configured NVIDIA settings as synthetic model usage."""
    no_env: dict[str, Any] = {"_env_file": None}
    settings = Settings(
        **no_env,
        eval_workspace_dir=tmp_path / "reports",
        nvidia_api_key="PRIVATE-KEY-SENTINEL",
        nvidia_chat_model="UNUSED-MODEL-SENTINEL",
        operation_timeout_seconds=37,
    )
    report = EvalService(settings=settings).run(
        mode="offline", backend=backend, compare_rerank=False
    )
    run = report.runs[0]
    configuration = run.effective_configuration
    assert configuration is not None
    assert configuration.provider == "synthetic"
    assert configuration.chat is None and configuration.embedding_model is None
    assert configuration.reranking_model is None
    assert configuration.request_timeout_seconds is None and configuration.max_retries is None
    assert configuration.operation_timeout_seconds == 37
    assert configuration.top_k_by_case == {case.case_id: case.top_k for case in run.case_results}
    assert configuration.conformance_thresholds == (
        conformance_thresholds() if backend == "nemo" else None
    )
    assert run.provider_usage == "not_performed" and run.provider_models == {}
    persisted = EvalModeRunResult.model_validate_json(Path(run.result_json_path).read_text())
    assert persisted.effective_configuration == configuration
    output = Path(run.report_html_path).read_text() + Path(run.result_json_path).read_text()
    assert "PRIVATE-KEY-SENTINEL" not in output and "UNUSED-MODEL-SENTINEL" not in output
    assert configuration.model_dump_json() in html.unescape(output)
