"""Ground authored security scenarios in real source bytes, without model judgments."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from policynim.ingest import chunk_policy_documents, load_policy_documents
from policynim.services.catalog import build_policy_catalog
from policynim.services.index_store import create_index_store
from policynim.services.ingest import IngestService
from policynim.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
SUITE = json.loads((ROOT / "evals/security_mcp_scenarios.json").read_text())
CATALOG = build_policy_catalog(ROOT / "policies")
CHUNKS = chunk_policy_documents(load_policy_documents(ROOT / "policies"))
RULE = re.compile(r"^- \*\*([A-Z]+-\d+)\*\* (.+) Support: (.+)\.$", re.MULTILINE)


@pytest.mark.parametrize("policy_id", SUITE["changed_policy_revisions"])
def test_security_policy_has_reviewable_sources_and_paired_cases(policy_id: str) -> None:
    """Pin actual source revisions and require role-specific evidence for changed content."""
    entry = CATALOG.show(policy_id)
    provenance = entry.policy.provenance
    assert provenance is not None
    assert provenance.content_digest == SUITE["changed_policy_revisions"][policy_id]
    assert provenance.pack_version == "0.2.0"
    assert provenance.grounding_kind == "adaptation"
    assert provenance.applicability
    assert entry.policy.grounded_in == [str(source.url) for source in provenance.sources]
    assert all(
        source.revision
        and source.revision in str(source.url)
        and source.sections
        and source.retrieved_on
        and source.license
        and source.attribution
        and source.change_notice
        for source in provenance.sources
    )
    for heading in (
        "Intent",
        "Applicability",
        "Required Rules",
        "Recommended Guidance",
        "Review and Test Expectations",
        "Exceptions and Boundaries",
        "Examples",
        "Sources and Change Notice",
    ):
        assert f"## {heading}" in entry.body
    rules = RULE.findall(entry.body)
    assert len(rules) >= 3
    assert len({rule[0] for rule in rules}) == len(rules)
    for _, _, support in rules:
        for reference in support.split("; "):
            if reference == "PolicyNIM decision":
                continue
            alias, section = reference.split(":", 1)
            matching = [
                source
                for source in provenance.sources
                if str(source.url) == SUITE["source_aliases"][alias]
            ]
            assert len(matching) == 1
            assert section in matching[0].sections
    cases = [case for case in SUITE["cases"] if case["policy_id"] == policy_id]
    assert {case["kind"] for case in cases} >= {"applicable", "violating", "out_of_scope"}


@pytest.mark.parametrize("case", SUITE["cases"], ids=lambda case: case["case_id"])
def test_security_scenario_evidence_survives_real_chunking(case: dict) -> None:
    """Check authored expectations against the selected policy's actual requirement chunk."""
    entry = CATALOG.show(case["policy_id"])
    provenance = entry.policy.provenance
    assert provenance is not None
    assert case["policy_revision"] == provenance.content_digest
    assert case["input"] and case["rationale"]
    if case["kind"] == "out_of_scope":
        assert case["role"] not in provenance.applicability
        assert case["expected_policy_ids"] == []
        assert case["out_of_scope_policy_ids"] == [case["policy_id"]]
        assert case["expected_evidence"] == []
        return
    assert case["role"] in provenance.applicability
    assert case["expected_policy_ids"] == [case["policy_id"]]
    assert case["out_of_scope_policy_ids"] == []
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
        assert matches[0].lines


class ContentFixtureEmbedder:
    """Produce deterministic vectors solely to exercise the real ingestion path."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Return distinct synthetic vectors without contacting a provider."""
        return [[float(index + 1), float(len(text))] for index, text in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        """Return a synthetic query vector; it doesn't measure semantic retrieval."""
        return [1.0, float(len(text))]

    def close(self) -> None:
        """The deterministic fixture owns no external resources."""


def test_security_corpus_ingests_with_revisions_and_source_evidence(tmp_path: Path) -> None:
    """Round-trip the whole selected corpus through actual ingest and SQLite persistence."""
    store = create_index_store(Settings(index_db_path=tmp_path / "index.sqlite3"), legacy=True)
    with IngestService(
        embedder=ContentFixtureEmbedder(),
        index_store=store,
        corpus_root=ROOT / "policies",
        embedding_model="offline-content-fixture",
        runtime_rules_artifact_path=tmp_path / "rules.json",
    ) as service:
        result = service.run()
    assert result.document_count == CATALOG.effective_count == 15
    assert result.chunk_count == len(CHUNKS)
    assert result.catalog_digest == CATALOG.digest
    assert store.inspect_readiness().state == "ready"
    persisted = {chunk.chunk_id: chunk for chunk in store.list_chunks()}
    assert persisted.keys() == {chunk.chunk_id for chunk in CHUNKS}
    for chunk in CHUNKS:
        assert persisted[chunk.chunk_id].text == chunk.text
        assert persisted[chunk.chunk_id].policy.provenance == chunk.policy.provenance
