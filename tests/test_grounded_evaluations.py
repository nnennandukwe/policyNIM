"""Independent quality gates for revision-pinned policy evaluations."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError

from policynim.errors import PolicyNIMError
from policynim.services.eval import (
    EvalService,
    _eval_citation,
    _score_preflight_case,
    _score_search_case,
    _validate_case_evidence,
)
from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store
from policynim.settings import Settings
from policynim.types import (
    EvalCase,
    EvalSuite,
    PolicyGuidance,
    PreflightResult,
    ScoredChunk,
    SearchResult,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed_generation(tmp_path: Path):
    """Use the shipped public vectors and exact sources without any model calls."""
    from policynim.types import EmbeddingIdentity

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        corpus_dir=ROOT / "policies",
        generations_dir=tmp_path / "generations",
        eval_workspace_dir=tmp_path / "evaluations",
    )
    store = GenerationStore(
        root=settings.generations_dir,
        embedding=EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        ),
    )
    bundle = ROOT / "artifacts/public-generation"
    store.import_bundle(
        bundle / "generation.zip",
        expected_digest=(bundle / "generation.sha256").read_text().strip(),
        source_settings=settings,
    )
    return settings, store


def test_preflight_expected_chunk_is_a_gate_even_when_policy_matches() -> None:
    """A policy-name match must not hide a missing exact source citation."""
    case = EvalCase(
        case_id="missing-citation",
        kind="preflight",
        input="Review authentication",
        expected_chunk_ids=["AUTH:required-rules"],
        expected_policy_ids=["AUTH"],
    )
    result = PreflightResult(
        task=case.input,
        summary="Review authentication",
        applicable_policies=[PolicyGuidance(policy_id="AUTH", title="Auth", rationale="Review it")],
    )
    scored = _score_preflight_case(case, result=result, rerank_enabled=True)
    assert not scored.passed
    assert "missing expected chunk_id values: AUTH:required-rules" in scored.failure_reasons
    assert "grounded preflight has no citations" in scored.failure_reasons


def test_search_scores_policy_expectations_and_forbidden_evidence(installed_generation) -> None:
    """Search quality includes policy identity, not only chunk recall."""
    settings, _ = installed_generation
    with closing(create_index_store(settings)) as store:
        chunk = store.list_chunks()[0]
    case = EvalCase(
        case_id="forbidden-search",
        kind="search",
        input="Search",
        expected_policy_ids=["OTHER"],
        forbidden_policy_ids=[chunk.policy.policy_id],
    )
    scored = _score_search_case(
        case,
        result=SearchResult(query="Search", top_k=1, hits=[ScoredChunk(**chunk.model_dump())]),
        rerank_enabled=True,
    )
    assert not scored.passed
    assert any("missing expected policy_id" in reason for reason in scored.failure_reasons)
    assert any("forbidden policy_id" in reason for reason in scored.failure_reasons)
    assert scored.search_result is not None
    assert scored.actual_citations == [_eval_citation(chunk)]


def test_forged_coordinates_fail_independent_generation_validation(installed_generation) -> None:
    """An existing chunk ID with falsified line spans must fail acceptance."""
    settings, generations = installed_generation
    with closing(create_index_store(settings)) as store:
        chunk = store.list_chunks()[0]
    case = EvalCase(case_id="forged-lines", kind="preflight", input="Review")
    citation = _eval_citation(chunk).model_copy(update={"lines": "99999-99999"})
    scored = _score_preflight_case(
        case,
        result=PreflightResult(task="Review", summary="Review", citations=[citation]),
        rerank_enabled=True,
    )
    assert scored.passed
    with generations.pin() as snapshot:
        _validate_case_evidence([scored], snapshot)
    assert not scored.passed
    assert any("citation doesn't match" in reason for reason in scored.failure_reasons)


def test_insufficient_context_cannot_expose_implementation_guidance() -> None:
    """A refusal flag with actionable output is a quality failure."""
    scored = _score_preflight_case(
        EvalCase(
            case_id="contradictory-refusal",
            kind="preflight",
            input="Unknown area",
            expected_insufficient_context=True,
        ),
        result=PreflightResult(
            task="Unknown area",
            summary="Insufficient context",
            insufficient_context=True,
            plan_steps=["Deploy without evidence"],
        ),
        rerank_enabled=True,
    )
    assert not scored.passed
    assert (
        "insufficient-context preflight exposed implementation guidance" in scored.failure_reasons
    )


@pytest.mark.parametrize("mismatch", ["catalog", "revision", "policy", "chunk", "unpinned"])
def test_stale_or_impossible_suite_rejected_before_providers(
    installed_generation, monkeypatch, tmp_path: Path, mismatch: str
) -> None:
    """Bad source assumptions never consume provider work or produce passing reports."""
    settings, generations = installed_generation
    with generations.pin() as snapshot:
        payload = {
            "name": "reviewed-suite",
            "expected_catalog_digest": snapshot.catalog.digest,
            "cases": [{"case_id": "check", "kind": "search", "input": "Review"}],
        }
    if mismatch == "catalog":
        payload["expected_catalog_digest"] = "sha256:" + "0" * 64
    elif mismatch == "revision":
        payload["expected_policy_revisions"] = {"SEC-AUTH-001": "sha256:" + "0" * 64}
    elif mismatch == "policy":
        payload["cases"][0]["expected_policy_ids"] = ["ABSENT"]
    elif mismatch == "chunk":
        payload["cases"][0]["expected_chunk_ids"] = ["ABSENT"]
    else:
        del payload["expected_catalog_digest"]
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps(payload))

    def forbid_provider(*args, **kwargs):
        """Construction itself is forbidden before fixture validation."""
        pytest.fail("Provider constructed for a stale fixture")

    monkeypatch.setattr("policynim.services.eval._create_live_search_service", forbid_provider)
    with pytest.raises(PolicyNIMError):
        EvalService(settings=settings).run(mode="live", suite_path=suite)
    assert not settings.eval_workspace_dir.exists()


def test_custom_suite_cannot_be_presented_as_offline_quality(tmp_path: Path) -> None:
    """Synthetic fixtures cannot establish real-corpus evaluation results."""
    with pytest.raises(PolicyNIMError, match="Custom suites require --mode live"):
        EvalService(settings=Settings()).run(suite_path=tmp_path / "suite.json")


@pytest.mark.parametrize(
    "cases",
    [
        [],
        [{"case_id": "same", "kind": "search", "input": "x"}] * 2,
        [
            {
                "case_id": "x",
                "kind": "search",
                "input": "x",
                "expected_policy_ids": ["A"],
                "forbidden_policy_ids": ["A"],
            }
        ],
        [
            {
                "case_id": "x",
                "kind": "preflight",
                "input": "x",
                "expected_chunk_ids": ["A"],
                "expected_insufficient_context": True,
            }
        ],
    ],
)
def test_invalid_fixture_expectations_are_not_a_passing_empty_suite(cases) -> None:
    """Reject empty, ambiguous, and contradictory evaluation definitions."""
    with pytest.raises(ValidationError):
        EvalSuite(name="invalid", cases=cases)


def test_real_pipeline_respects_profile_exclusions_and_archives_expectations(
    monkeypatch, tmp_path: Path
) -> None:
    """Run real generation, SQLite retrieval, routing, compilation and citation validation."""
    from test_ingest_service import MockEmbedder
    from test_policy_profiles import compose, write_pack

    from policynim.services.preflight import PreflightService
    from policynim.services.profiles import initialize_profile
    from policynim.services.search import SearchService
    from policynim.types import (
        EmbeddingIdentity,
        GeneratedCompiledPolicyDraft,
        GeneratedPolicyConstraint,
        GeneratedPolicyGuidance,
        GeneratedPreflightDraft,
    )

    baseline = write_pack(tmp_path / "baseline", "public.baseline", ["A", "B", "C"])
    custom = write_pack(tmp_path / "custom", "team.custom", ["D", "E"])
    profile = tmp_path / "profile.toml"
    initialize_profile(profile, [baseline, custom], profile_id="team")
    compose(profile)
    settings = Settings(
        profile=profile,
        generations_dir=tmp_path / "generations",
        eval_workspace_dir=tmp_path / "evaluations",
        nvidia_embed_model="test-embed",
        nvidia_base_url="https://example.invalid/v1",
    )
    generations = GenerationStore(
        root=settings.generations_dir,
        embedding=EmbeddingIdentity(model="test-embed", endpoint="https://example.invalid/v1"),
    )
    original = generations.build(settings, embedder=MockEmbedder()).generation_id
    with generations.pin() as snapshot:
        digest = snapshot.catalog.digest
    suite = EvalSuite(
        name="composed-policy-quality",
        expected_catalog_digest=digest,
        cases=[
            EvalCase(
                case_id="custom-policy-preflight",
                kind="preflight",
                input="Implement deployment controls",
                top_k=10,
                expected_policy_ids=["C", "D", "E"],
                forbidden_policy_ids=["A", "B"],
            ),
            EvalCase(
                case_id="custom-policy-search",
                kind="search",
                input="deployment controls",
                top_k=10,
                expected_policy_ids=["C", "D", "E"],
                forbidden_policy_ids=["A", "B"],
            ),
        ],
    )
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(suite.model_dump_json())

    class QueryEmbedder:
        """Exercise real SQLite with a deterministic two-dimensional query."""

        def embed_query(self, text):
            return [1.0, 0.0]

        def embed_documents(self, texts):
            pytest.fail("Evaluation must not re-embed the generation")

    class Reranker:
        """Retain real candidates; this fixture makes no semantic ranking claim."""

        def rerank(self, query, candidates, *, top_k):
            return [chunk.model_copy(update={"score": 1.0}) for chunk in candidates[:top_k]]

    class Compiler:
        """Require the actual retained source text with its actual chunk IDs."""

        def compile_policy_packet(self, request, selection_packet, context):
            return GeneratedCompiledPolicyDraft(
                required_steps=[
                    GeneratedPolicyConstraint(statement=chunk.text, citation_ids=[chunk.chunk_id])
                    for chunk in context
                ]
            )

    class Generator:
        """Produce controlled provider output so local grounding checks remain independent."""

        def generate_preflight(self, request, context, *, compiled_packet=None, **kwargs):
            return GeneratedPreflightDraft(
                summary="Apply the selected deployment controls.",
                applicable_policies=[
                    GeneratedPolicyGuidance(
                        policy_id=chunk.policy.policy_id,
                        title=chunk.policy.title,
                        rationale="Selected deployment rule",
                        citation_ids=[chunk.chunk_id],
                    )
                    for chunk in context
                ],
                citation_ids=[chunk.chunk_id for chunk in context],
            )

    def search_factory(active_settings, *, rerank_enabled):
        """Mutating the input file after capture cannot rewrite the job expectations."""
        suite_path.write_text('{"name":"changed","cases":[]}')
        return SearchService(
            embedder=QueryEmbedder(),
            index_store=create_index_store(active_settings),
            reranker=Reranker() if rerank_enabled else None,
        )

    monkeypatch.setattr("policynim.services.eval._create_live_search_service", search_factory)
    monkeypatch.setattr(
        "policynim.services.eval._create_live_preflight_service",
        lambda active_settings, rerank_enabled: PreflightService(
            embedder=QueryEmbedder(),
            index_store=create_index_store(active_settings),
            reranker=Reranker(),
            compiler=Compiler(),
            generator=Generator(),
        ),
    )
    report = EvalService(settings=settings).run(
        mode="live", suite_path=suite_path, compare_rerank=False
    )
    assert report.generation_id == original
    run = report.runs[0]
    assert run.suite_definition == suite
    assert run.suite_digest == report.suite_digest
    assert run.catalog_digest == digest
    assert all(case.passed for case in run.case_results), run.model_dump_json(indent=2)
    assert all(set(case.actual_policy_ids) == {"C", "D", "E"} for case in run.case_results)
    assert run.case_results[0].preflight_result is not None
    assert run.case_results[0].evidence_trace is not None
    before = Path(run.result_json_path).read_bytes()
    suite_path.write_text(suite.model_dump_json())
    repeated = EvalService(settings=settings).run(
        mode="live", suite_path=suite_path, compare_rerank=False
    )
    assert repeated.runs[0].result_json_path != run.result_json_path
    assert Path(run.result_json_path).read_bytes() == before
