"""Verify selectable engineering packs and authored evidence through real consumers."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("policy_id", SUITE["changed_policy_revisions"])
def test_changed_policy_has_pinned_sources_and_paired_scope_cases(policy_id: str) -> None:
    """Every changed policy names actual sources and authored positive/negative scope."""
    entry = CATALOG.show(policy_id)
    provenance = entry.policy.provenance
    assert provenance is not None
    assert provenance.content_digest == SUITE["changed_policy_revisions"][policy_id]
    assert provenance.grounding_kind == "project_recommendation"
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
    assert {case["kind"] for case in cases} == {"applicable", "violating", "out_of_scope"}
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
        }[case["kind"]]
    )
    if case["kind"] == "out_of_scope":
        assert case["role"] not in provenance.applicability
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
