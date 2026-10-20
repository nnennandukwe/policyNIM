"""Verify selectable engineering packs and authored evidence through real consumers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from test_security_content import ContentFixtureEmbedder

from policynim.ingest import chunk_policy_documents, load_policy_documents
from policynim.services.catalog import build_policy_catalog
from policynim.services.generation_sources import capture_sources
from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store
from policynim.services.profiles import initialize_profile, resolve_profile
from policynim.settings import Settings
from policynim.types import EmbeddingIdentity

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "policies"
PROFILE = POLICIES / "profiles/full-engineering.toml"
SUITE = json.loads((ROOT / "evals/engineering_scenarios.json").read_text())
CATALOG = build_policy_catalog(POLICIES)
CHUNKS = chunk_policy_documents(load_policy_documents(POLICIES))
TOPICS = {
    "testing": {"TEST-FAIL-001", "TEST-CONTRACT-001"},
    "api": {"ARCH-API-001", "API-ERROR-001", "API-PAGE-001"},
    "data": {"DATA-MIGRATE-001", "DATA-TRANSACTION-001"},
    "reliability": {"ARCH-JOB-001", "BE-CONFIG-001", "REL-TIMEOUT-001", "REL-OVERLOAD-001"},
    "observability": {"BE-LOG-001", "BE-TRACE-001", "OBS-READINESS-001"},
    "supply-chain": {"SUPPLY-BUILD-001", "SUPPLY-PROVENANCE-001"},
}
NO_ENV: dict[str, Any] = {"_env_file": None}


@pytest.mark.parametrize("topic,expected_ids", TOPICS.items())
def test_topic_selection_has_only_its_owned_policies(topic: str, expected_ids: set[str]) -> None:
    """Nested manifests select their own members without borrowing aggregate provenance."""
    catalog = build_policy_catalog(POLICIES / topic)
    assert {entry.policy.policy_id for entry in catalog.policies} == expected_ids
    assert not catalog.unlisted_markdown
    for entry in catalog.policies:
        provenance = entry.policy.provenance
        aggregate = CATALOG.show(entry.policy.policy_id).policy.provenance
        assert provenance is not None and aggregate is not None
        assert provenance.pack_id == f"policynim.{topic}"
        assert aggregate.pack_id == "policynim.baseline"
        assert provenance.pack_version == aggregate.pack_version == "0.3.0"
        assert provenance.content_digest == aggregate.content_digest
        assert provenance.sources == aggregate.sources
        assert provenance.applicability == aggregate.applicability


def test_full_profile_composes_nonoverlapping_topics_and_retains_notices() -> None:
    """The shipped profile resolves pinned topic roots and keeps source notices per root."""
    resolved = resolve_profile(PROFILE)
    assert {entry.policy.policy_id for entry in resolved.catalog.policies} == {
        entry.policy.policy_id for entry in CATALOG.policies
    }
    assert len(resolved.catalog.pack_pins) == 7
    assert len({entry.path for entry in resolved.catalog.policies}) == 26
    snapshot, roots = capture_sources(Settings(**NO_ENV, profile=PROFILE))
    assert len(roots) == len(snapshot.packs) == 7
    for pack in snapshot.packs:
        assert pack.notices
        assert all(path.startswith("licenses/") for path in pack.notices)
    assert snapshot.resolve()[1].effective_count == CATALOG.effective_count


@pytest.mark.parametrize("topic", TOPICS)
def test_topic_can_enter_a_custom_profile_alone(tmp_path: Path, topic: str) -> None:
    """A caller can select one engineering area without adding the default aggregate."""
    path = tmp_path / "profile.toml"
    initialize_profile(path, [POLICIES / topic], profile_id="test.topic")
    resolved = resolve_profile(path)
    assert {entry.policy.policy_id for entry in resolved.catalog.policies} == TOPICS[topic]
    assert len(resolved.roots) == 1


def test_every_selected_engineering_policy_retains_scenario_coverage() -> None:
    """Deleting a revision and all its cases can't silently remove a topic's coverage."""
    required_ids = set().union(*TOPICS.values())
    assert set(SUITE["changed_policy_revisions"]) == required_ids
    assert {case["policy_id"] for case in SUITE["cases"]} == required_ids


def test_authored_scenarios_match_reviewed_bundle_and_profile(tmp_path: Path) -> None:
    """Bind source scenarios to the actual imported aggregate and separately pinned profile."""
    settings = Settings(**NO_ENV, corpus_dir=POLICIES, generations_dir=tmp_path / "generations")
    generations = GenerationStore(
        root=settings.generations_dir,
        embedding=EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        ),
    )
    bundle = ROOT / "artifacts/public-generation"
    generations.import_bundle(
        bundle / "generation.zip",
        expected_digest=(bundle / "generation.sha256").read_text().strip(),
        source_settings=settings,
    )
    with generations.pin() as snapshot:
        assert SUITE["generation_id"] == snapshot.generation_id
        assert SUITE["catalog_digest"] == snapshot.catalog.digest == CATALOG.digest
        assert SUITE["profile_id"] == snapshot.manifest.profile_id
        assert SUITE["profile_digest"] == snapshot.manifest.profile_digest
    composed = resolve_profile(PROFILE).catalog
    assert SUITE["full_engineering_profile"] == {
        "profile_id": composed.profile_id,
        "profile_digest": composed.profile_digest,
    }


@pytest.mark.parametrize("policy_id", sorted(set().union(*TOPICS.values())))
def test_changed_policy_has_pinned_sources_and_paired_scope_cases(policy_id: str) -> None:
    """Every changed policy names actual sources and authored positive/negative scope."""
    entry = CATALOG.show(policy_id)
    provenance = entry.policy.provenance
    assert provenance is not None
    assert provenance.content_digest == SUITE["changed_policy_revisions"][policy_id]
    assert provenance.grounding_kind == (
        "adaptation" if policy_id in {"BE-LOG-001", "BE-CONFIG-001"} else "project_recommendation"
    )
    assert provenance.sources and provenance.applicability
    assert entry.policy.grounded_in == [str(source.url) for source in provenance.sources]
    assert all(
        source.revision
        and source.sections
        and source.retrieved_on
        and source.license
        and source.attribution
        and source.change_notice
        for source in provenance.sources
    )
    cases = [case for case in SUITE["cases"] if case["policy_id"] == policy_id]
    assert {case["kind"] for case in cases} == {
        "applicable",
        "violating",
        "out_of_scope",
        "boundary",
    }
    for heading in (
        "Intent",
        "Applicability",
        "Required Rules",
        "Review and Test Expectations",
        "Exceptions and Boundaries",
        "Examples",
        "Sources and Change Notice",
    ):
        assert f"## {heading}" in entry.body


@pytest.mark.parametrize("case", SUITE["cases"], ids=lambda case: case["case_id"])
def test_authored_scenario_evidence_matches_actual_chunks(case: dict) -> None:
    """Expected evidence must survive the actual parser and chunker with source support."""
    entry = CATALOG.show(case["policy_id"])
    provenance = entry.policy.provenance
    assert provenance is not None
    assert case["policy_revision"] == provenance.content_digest
    assert case["input"] and case["rationale"]
    assert (
        case["expected_outcome"]
        == {
            "applicable": "apply_guidance",
            "violating": "require_changes",
            "out_of_scope": "not_applicable",
            "boundary": "respect_documented_boundary",
        }[case["kind"]]
    )
    if case["kind"] == "boundary":
        assert case["role"] in provenance.applicability
        assert case["expected_policy_ids"] == [case["policy_id"]]
        assert not case["out_of_scope_policy_ids"]
        boundary = entry.body.split("## Exceptions and Boundaries\n\n", 1)[1].split("\n\n##", 1)[0]
        assert case["boundary_evidence"] == boundary
        return
    if case["kind"] == "out_of_scope":
        assert case["role"] in provenance.applicability
        actor = case["role"].replace("-", " ")
        assert case["input"].lower().startswith((f"a {actor} ", f"an {actor} "))
        intent = entry.body.split("## Intent\n\n", 1)[1].split("\n\n##", 1)[0]
        assert case["scope_evidence"] == intent
        assert case["expected_policy_ids"] == case["expected_evidence"] == []
        assert case["out_of_scope_policy_ids"] == [case["policy_id"]]
        return
    assert case["role"] in provenance.applicability
    assert case["expected_policy_ids"] == [case["policy_id"]]
    assert not case["out_of_scope_policy_ids"]
    assert case["expected_evidence"]
    for evidence in case["expected_evidence"]:
        expected = (
            f"- **{evidence['requirement_id']}** {evidence['quote']} "
            f"Support: {'; '.join(evidence['support'])}."
        )
        matches = [
            chunk
            for chunk in CHUNKS
            if chunk.policy.policy_id == case["policy_id"] and expected in chunk.text
        ]
        assert len(matches) == 1
        assert matches[0].policy.provenance == provenance
        for reference in evidence["support"]:
            if reference == "PolicyNIM decision":
                continue
            alias, section = reference.split(":", 1)
            sources = [
                source
                for source in provenance.sources
                if str(source.url) == SUITE["source_aliases"][alias]
            ]
            assert len(sources) == 1
            assert section in sources[0].sections


def test_full_profile_publishes_and_reads_a_complete_offline_generation(tmp_path: Path) -> None:
    """Topic composition is accepted by the real generation inspector and SQLite reader."""
    settings = Settings(
        **NO_ENV,
        profile=PROFILE,
        generations_dir=tmp_path / "generations",
        nvidia_embed_model="offline-engineering-fixture",
    )
    identity = EmbeddingIdentity(
        model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
    )
    generations = GenerationStore(root=settings.generations_dir, embedding=identity)
    result = generations.build(settings, ContentFixtureEmbedder())
    with closing(create_index_store(settings)) as store:
        assert store.generation_id == result.generation_id
        assert {chunk.policy.policy_id for chunk in store.list_chunks()} == {
            entry.policy.policy_id for entry in CATALOG.policies
        }
    with generations.pin() as snapshot:
        assert snapshot.indexed_catalog.effective_count == 26
        assert snapshot.manifest.pack_pins == snapshot.indexed_catalog.pack_pins


def test_scope_cases_have_policy_specific_inputs() -> None:
    """Authored exclusions vary the affected behavior instead of excluding every role."""
    cases = [case for case in SUITE["cases"] if case["kind"] == "out_of_scope"]
    assert len({case["input"] for case in cases}) == len(set().union(*TOPICS.values()))
    assert all(case.get("scope_evidence") for case in cases)


def test_security_topic_retains_aggregate_provenance_and_notice_bytes() -> None:
    """Selecting security alone can't silently change the aggregate's source attribution."""
    security = build_policy_catalog(POLICIES / "security")
    for entry in security.policies:
        actual = entry.policy.provenance
        expected = CATALOG.show(entry.policy.policy_id).policy.provenance
        assert actual is not None and expected is not None
        assert actual.model_dump(exclude={"pack_id", "pack_version"}) == expected.model_dump(
            exclude={"pack_id", "pack_version"}
        )
        assert entry.body == CATALOG.show(entry.policy.policy_id).body
    for notice in POLICIES.glob("*/licenses/*"):
        assert notice.read_bytes() == (POLICIES / "licenses" / notice.name).read_bytes()


def test_nested_runtime_data_is_ignored_without_hiding_policy_sources() -> None:
    """A command launched below the checkout can't leave its runtime database unignored."""
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=ROOT,
        input="data/auth.sqlite3\nscratch/data/auth.sqlite3\npolicies/data/pack.toml\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["data/auth.sqlite3", "scratch/data/auth.sqlite3"]


def test_service_configuration_only_requires_model_identity_when_applicable() -> None:
    """The general service rule doesn't require a model for an ordinary backend."""
    rule = next(
        line for line in CATALOG.show("BE-CONFIG-001").body.splitlines() if "**CONFIG-2**" in line
    )
    assert "model identity when models are used" in rule


def test_authored_expectations_match_the_independent_source_review() -> None:
    """Changed expectations need a new reviewed payload rather than a copied status label."""
    evidence = ROOT / "docs/evidence/engineering-source-review"
    full_bytes = (evidence / "refreshed.json").read_bytes()
    delta_bytes = (evidence / "rationale-delta.json").read_bytes()
    full = json.loads(full_bytes)
    delta = json.loads(delta_bytes)
    binding = json.loads((evidence / "current-fixture-binding.json").read_text())

    def digest(raw: bytes) -> str:
        """Bind the exact retained receipt or fixture bytes."""
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    assert binding["kind"] == "derived_current_fixture_binding"
    assert binding["reviewed_fixture_revision"] == delta["source_sha"]
    assert binding["scenario_sha256"] == digest(
        (ROOT / "evals/engineering_scenarios.json").read_bytes()
    )
    assert binding["full_review_receipt_sha256"] == digest(full_bytes)
    assert binding["delta_review_receipt_sha256"] == digest(delta_bytes)
    assert delta["prior_review"]["sha256"] == hashlib.sha256(full_bytes).hexdigest()
    assert delta["base_sha"] == full["source_sha"]
    assert delta["findings"] == []
    assert binding["model_evaluated"] is False
    payload = {key: SUITE[key] for key in ("changed_policy_revisions", "source_aliases", "cases")}
    serialized = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    assert digest(serialized) == binding["reviewed_semantic_payload_sha256"]
    assert hashlib.sha256(serialized).hexdigest() == delta["semantic_payload_sha256"]
    assert {row["policy_id"]: row["policy_revision"] for row in full["policy_revision_table"]} == (
        SUITE["changed_policy_revisions"]
    )
    assert {row["case_id"] for row in full["per_case_assessments"]} == {
        case["case_id"] for case in SUITE["cases"]
    }
    assert full["counts"]["outcomes_supported"] == len(SUITE["cases"]) == 64
    current = {case["case_id"]: case for case in SUITE["cases"]}
    assert {change["case_id"] for change in delta["changes"]} == {
        finding["case_id"] for finding in full["findings"]
    }
    for change in delta["changes"]:
        assert current[change["case_id"]][change["field"]] == change["after"]
    assert binding["source_catalog_digest"] == CATALOG.digest
    assert SUITE["review_status"] == "independent_source_reviewed_not_model_evaluated"


def test_noop_scope_pairs_keep_runtime_changes_in_applicable_boundaries() -> None:
    """Boundary verdicts exercise applicable work instead of contradicting no-op exclusions."""
    cases = {case["case_id"]: case for case in SUITE["cases"]}
    failure = cases["test-fail-001-boundary"]
    contract = cases["test-contract-001-boundary"]
    assert "changes publisher recovery" in failure["input"]
    assert "restarts the process" in failure["input"]
    assert "SSDF" in failure["input"]
    assert "changes a public serialized response" in contract["input"]
    assert "strict installed consumer" in contract["input"]
    assert "live provider quality" in contract["input"]
    for policy_id in ("test-fail-001", "test-contract-001"):
        boundary = cases[f"{policy_id}-boundary"]
        excluded = cases[f"{policy_id}-out-of-scope"]
        assert boundary["expected_policy_ids"] == [boundary["policy_id"]]
        assert boundary["expected_outcome"] == "respect_documented_boundary"
        assert excluded["expected_policy_ids"] == []
        assert excluded["expected_outcome"] == "not_applicable"


@pytest.mark.parametrize(
    "policy_id,alias,sections",
    [
        ("TEST-FAIL-001", "SSDF", ["PW.8.1", "PW.8.2"]),
        ("TEST-CONTRACT-001", "SSDF", ["PW.8.2"]),
        ("TEST-CONTRACT-001", "SRE-TEST", ["Traditional Tests"]),
        ("SUPPLY-BUILD-001", "SSDF", ["PW.4.1"]),
        (
            "SUPPLY-BUILD-001",
            "OSPS",
            ["OSPS-BR-01.01", "OSPS-BR-01.03", "OSPS-BR-05.01", "OSPS-DO-06.01"],
        ),
        ("SUPPLY-PROVENANCE-001", "SSDF", ["PS.2.1"]),
        ("SUPPLY-PROVENANCE-001", "OSPS", ["OSPS-BR-06.01"]),
        ("API-ERROR-001", "HTTP", ["15"]),
        ("REL-TIMEOUT-001", "HTTP", ["10.2.3"]),
        ("ARCH-JOB-001", "AWS-RETRY", ["Retries and backoff"]),
        ("BE-LOG-001", "OTEL-LOG", ["Trace Context Fields", "Field: Resource"]),
        ("BE-TRACE-001", "OTEL-LOG", ["Trace Context Fields"]),
    ],
)
def test_source_section_lists_are_specific_to_the_policy(
    policy_id: str, alias: str, sections: list[str]
) -> None:
    """Related policies don't inherit the union of every section from a shared reference."""
    entry = CATALOG.show(policy_id)
    assert entry.policy.provenance is not None
    source = next(
        source
        for source in entry.policy.provenance.sources
        if str(source.url) == SUITE["source_aliases"][alias]
    )
    assert source.sections == sections
    assert f"Sections: {', '.join(sections)}." in entry.body


@pytest.mark.parametrize("policy_id", ["BE-LOG-001", "BE-CONFIG-001"])
def test_owasp_adaptation_body_and_aggregate_notices_agree(policy_id: str) -> None:
    """Retained adaptations keep their attribution in prose as well as manifest fields."""
    entry = CATALOG.show(policy_id)
    assert entry.policy.provenance is not None
    assert entry.policy.provenance.grounding_kind == "adaptation"
    assert "PolicyNIM adaptation of the cited OWASP" in entry.body
    assert "Original PolicyNIM review guidance; source text isn't reproduced." not in entry.body
    notice = (POLICIES / "licenses/ENGINEERING-SOURCES.txt").read_text()
    assert "BE-LOG-001 and BE-CONFIG-001 retain their OWASP adaptation" in notice
