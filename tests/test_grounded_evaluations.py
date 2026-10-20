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
            """Return the controlled query vector."""
            return [1.0, 0.0]

        def embed_documents(self, texts):
            """Reject unexpected corpus embedding during evaluation."""
            pytest.fail("Evaluation must not re-embed the generation")

    class Reranker:
        """Retain real candidates; this fixture makes no semantic ranking claim."""

        def rerank(self, query, candidates, *, top_k):
            """Retain the controlled candidate order."""
            return [chunk.model_copy(update={"score": 1.0}) for chunk in candidates[:top_k]]

    class Compiler:
        """Require the actual retained source text with its actual chunk IDs."""

        def compile_policy_packet(self, request, selection_packet, context):
            """Compile constraints from the exact supplied source chunks."""
            return GeneratedCompiledPolicyDraft(
                required_steps=[
                    GeneratedPolicyConstraint(statement=chunk.text, citation_ids=[chunk.chunk_id])
                    for chunk in context
                ]
            )

    class Generator:
        """Produce controlled provider output so local grounding checks remain independent."""

        def generate_preflight(self, request, context, *, compiled_packet=None, **kwargs):
            """Return controlled output with the exact supplied policy citations."""
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
    assert run.provider_usage == "unavailable"
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


@pytest.mark.parametrize(
    "field",
    [
        "summary",
        "plan_steps",
        "implementation_guidance",
        "review_flags",
        "tests_required",
        "rationale",
        "title",
    ],
)
def test_forbidden_policy_in_generated_narrative_fails(field: str) -> None:
    """Generated text can't evade exclusion checks by omitting structured policy fields."""
    policy_id = "PRIVATE-EXCLUDED-001"
    payload = {"task": "Review", "summary": "Review", "insufficient_context": True}
    if field in {"rationale", "title"}:
        payload["applicable_policies"] = [
            {
                "policy_id": "ALLOWED",
                "title": policy_id if field == "title" else "Allowed",
                "rationale": policy_id if field == "rationale" else "Allowed guidance",
            }
        ]
    else:
        payload[field] = policy_id if field == "summary" else [policy_id]
    result = _score_preflight_case(
        EvalCase(
            case_id="excluded-narrative",
            kind="preflight",
            input="Review",
            expected_insufficient_context=True,
            forbidden_policy_ids=[policy_id],
        ),
        result=PreflightResult.model_validate(payload),
        rerank_enabled=True,
    )
    assert not result.passed
    assert f"forbidden policy_id values: {policy_id}" in result.failure_reasons


def test_mixed_suite_metrics_include_every_applicable_expectation() -> None:
    """Search policy misses and preflight chunk misses must affect the run summary."""
    from policynim.services.eval import _aggregate_metrics

    search = _score_search_case(
        EvalCase(
            case_id="search-miss", kind="search", input="Review", expected_policy_ids=["MISSING"]
        ),
        result=SearchResult(query="Review", top_k=1),
        rerank_enabled=True,
    )
    preflight = _score_preflight_case(
        EvalCase(
            case_id="preflight-miss",
            kind="preflight",
            input="Review",
            expected_chunk_ids=["MISSING"],
        ),
        result=PreflightResult(task="Review", summary="Review"),
        rerank_enabled=True,
    )
    metrics = _aggregate_metrics([search, preflight])
    assert metrics.expected_policy_recall == 0.0
    assert metrics.expected_chunk_recall == 0.0


def test_shipped_live_expectations_match_the_shipped_catalog(installed_generation) -> None:
    """Policy changes require an explicit reviewed fixture update in offline CI."""
    from policynim.services.catalog import build_policy_catalog
    from policynim.services.eval import _load_eval_suite, _validate_suite_generation

    suite = _load_eval_suite(ROOT / "evals/grounded_cases.json")
    _, generations = installed_generation
    with generations.pin() as snapshot:
        assert snapshot.catalog.digest == build_policy_catalog(ROOT / "policies").digest
        _validate_suite_generation(suite, snapshot)
    assert all(
        case.expected_chunk_ids for case in suite.cases if not case.expected_insufficient_context
    )


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX permissions aren't Windows ACLs")
def test_eval_reports_are_private_even_under_a_permissive_umask(tmp_path: Path) -> None:
    """A shared parent directory mustn't expose saved policy content to other users."""
    import os
    import stat

    old_mask = os.umask(0)
    try:
        result = EvalService(settings=Settings(eval_workspace_dir=tmp_path / "shared")).run(
            compare_rerank=False
        )
    finally:
        os.umask(old_mask)
    run = result.runs[0]
    assert stat.S_IMODE(Path(run.result_json_path).stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(run.report_html_path).stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(run.result_json_path).parent.stat().st_mode) == 0o700


def test_eval_report_cannot_replace_historical_evidence(tmp_path: Path) -> None:
    """Exclusive report creation preserves previous runs and rejects symlink targets."""
    from policynim.services.eval import _write_private_eval_file

    path = tmp_path / "report.json"
    path.write_text("historical")
    with pytest.raises(FileExistsError):
        _write_private_eval_file(path, "replacement")
    assert path.read_text() == "historical"


def test_public_case_evaluation_requires_a_generation_before_provider_work() -> None:
    """Durable workers can't accidentally follow a changing active generation."""
    suite = EvalSuite(
        name="captured", cases=[EvalCase(case_id="case", kind="search", input="Review")]
    )
    with pytest.raises(PolicyNIMError, match="explicit generation_id"):
        EvalService(settings=Settings()).evaluate_case(suite, "case")


def test_public_case_evaluation_uses_captured_inputs_without_report_side_effects(
    installed_generation,
    monkeypatch,
) -> None:
    """The worker executes only the requested captured case and validates its evidence."""
    settings, generations = installed_generation
    with generations.pin() as snapshot:
        generation_id = snapshot.generation_id
        catalog_digest = snapshot.catalog.digest
    settings = settings.model_copy(update={"generation_id": generation_id})
    with closing(create_index_store(settings)) as store:
        chunk = store.list_chunks()[0]
    suite = EvalSuite(
        name="captured",
        expected_catalog_digest=catalog_digest,
        cases=[
            EvalCase(case_id="first", kind="search", input="Original input"),
            EvalCase(case_id="second", kind="search", input="Don't execute this case"),
        ],
    )
    observed = []

    def evaluate_live(self, selected, **kwargs):
        """Represent the provider boundary and return actual held-generation coordinates."""
        observed.extend(selected.cases)
        return [
            _score_search_case(
                selected.cases[0],
                result=SearchResult(
                    generation_id=generation_id,
                    query=selected.cases[0].input,
                    top_k=1,
                    hits=[ScoredChunk(**chunk.model_dump())],
                ),
                rerank_enabled=True,
            )
        ]

    monkeypatch.setattr(EvalService, "_run_live_suite", evaluate_live)
    result = EvalService(settings=settings).evaluate_case(suite, "first")
    assert result.passed
    assert result.generation_id == generation_id
    assert [case.input for case in observed] == ["Original input"]
    assert len(suite.cases) == 2
    assert not settings.eval_workspace_dir.exists()
    with pytest.raises(PolicyNIMError, match="isn't in the captured"):
        EvalService(settings=settings).evaluate_case(suite, "absent")


def test_refusal_only_suite_marks_recall_as_not_applicable(tmp_path: Path) -> None:
    """A suite without expected evidence mustn't report measured zero recall."""
    from policynim.services.eval import _aggregate_metrics, _write_eval_html_report

    case = EvalCase(
        case_id="refusal", kind="preflight", input="Unsupported", expected_insufficient_context=True
    )
    result = _score_preflight_case(
        case,
        result=PreflightResult(task=case.input, summary="Unsupported", insufficient_context=True),
        rerank_enabled=True,
    )
    metrics = _aggregate_metrics([result])
    assert metrics.passed_count == 1
    assert metrics.expected_chunk_case_count == 0
    assert metrics.expected_policy_case_count == 0
    report = tmp_path / "report.html"
    _write_eval_html_report(
        report,
        suite=EvalSuite(name="refusal-only", cases=[case]),
        suite_path=tmp_path / "suite.json",
        mode="live",
        backend="default",
        rerank_enabled=True,
        metrics=metrics,
        rows=[],
    )
    assert report.read_text().count("not applicable") == 2


@pytest.mark.parametrize("tampering", ["owner", "title", "missing-link"])
def test_preflight_expected_policy_needs_its_own_source_evidence(
    installed_generation, tampering: str
) -> None:
    """A required policy name can't borrow another policy's real chunk or title."""
    settings, _ = installed_generation
    with closing(create_index_store(settings)) as store:
        first, *remaining = store.list_chunks()
        second = next(
            chunk for chunk in remaining if chunk.policy.policy_id != first.policy.policy_id
        )
    first_citation, second_citation = _eval_citation(first), _eval_citation(second)
    case = EvalCase(
        case_id="misattributed-guidance",
        kind="preflight",
        input="Review policy evidence",
        expected_policy_ids=[first.policy.policy_id],
        expected_chunk_ids=[first.chunk_id],
    )
    guidance = PolicyGuidance(
        policy_id=first.policy.policy_id,
        title="Forged title" if tampering == "title" else first.policy.title,
        rationale="Follow this policy",
        citation_ids=(
            []
            if tampering == "missing-link"
            else [second.chunk_id if tampering == "owner" else first.chunk_id]
        ),
    )
    scored = _score_preflight_case(
        case,
        result=PreflightResult(
            task=case.input,
            summary="Grounded review",
            applicable_policies=[guidance],
            citations=[first_citation, second_citation],
        ),
        rerank_enabled=True,
    )
    assert not scored.passed
    assert scored.matched_policy_ids == []
    assert scored.metrics.expected_policy_recall == 0
    assert any("guidance evidence" in reason for reason in scored.failure_reasons)
