"""Reject invented policy metadata attached to otherwise valid retrieved citations."""

from __future__ import annotations

import pytest
from test_preflight_service import (
    MockEmbedder,
    MockGenerator,
    MockIndexStore,
    MockPolicyCompiler,
    MockReranker,
    fixture_catalog,
    fixture_draft,
    make_chunk,
)

from policynim.services.preflight import PreflightService
from policynim.types import (
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    PreflightRequest,
)


@pytest.mark.parametrize(
    "policy_id,title,citation_ids",
    [
        ("INVENTED-POLICY", "Logging", ["BACKEND-1"]),
        ("SECURITY-TOKEN-001", "Token handling", ["BACKEND-1"]),
        ("BACKEND-LOG-001", "Invented policy title", ["BACKEND-1"]),
        ("BACKEND-LOG-001", "Logging", ["BACKEND-1", "SECURITY-1"]),
    ],
)
def test_preflight_refuses_guidance_with_forged_citation_owner_metadata(
    policy_id: str, title: str, citation_ids: list[str]
) -> None:
    """Real service orchestration must reject invented identities even when chunk IDs exist."""
    chunks = [
        make_chunk(chunk_id="BACKEND-1", policy_id="BACKEND-LOG-001", domain="backend", score=0.99),
        make_chunk(
            chunk_id="SECURITY-1", policy_id="SECURITY-TOKEN-001", domain="security", score=0.98
        ),
    ]
    generator = MockGenerator(
        GeneratedPreflightDraft(
            summary="An apparently cited policy claim.",
            applicable_policies=[
                GeneratedPolicyGuidance(
                    policy_id=policy_id,
                    title=title,
                    rationale="Claimed policy authority.",
                    citation_ids=citation_ids,
                )
            ],
            citation_ids=["BACKEND-1"],
            implementation_guidance=["Treat the invented claim as policy."],
        )
    )
    with PreflightService(
        source_catalog_resolver=fixture_catalog,
        embedder=MockEmbedder(),
        index_store=MockIndexStore(chunks),
        reranker=MockReranker(),
        generator=generator,
        compiler=MockPolicyCompiler(),
    ) as service:
        result = service.preflight(PreflightRequest(task="backend guidance", top_k=2))
    assert generator.last_context
    assert result.insufficient_context
    assert result.applicable_policies == []
    assert result.citations == []
    assert result.implementation_guidance == []


def test_compiled_packet_can_add_other_policy_citations_without_forging_guidance() -> None:
    """Global compiled evidence can span policies while each guidance item owns its citations."""
    chunks = [
        make_chunk(chunk_id="BACKEND-1", policy_id="BACKEND-LOG-001", domain="backend", score=0.99),
        make_chunk(
            chunk_id="SECURITY-1", policy_id="SECURITY-TOKEN-001", domain="security", score=0.98
        ),
    ]
    generator = MockGenerator(fixture_draft(chunks))
    compiler = MockPolicyCompiler()
    with PreflightService(
        source_catalog_resolver=fixture_catalog,
        embedder=MockEmbedder(),
        index_store=MockIndexStore(chunks),
        reranker=MockReranker(),
        generator=generator,
        compiler=compiler,
    ) as service:
        result = service.preflight(PreflightRequest(task="backend guidance", top_k=2))
    assert not result.insufficient_context
    assert {citation.chunk_id for citation in result.citations} == {"BACKEND-1", "SECURITY-1"}
    assert result.applicable_policies[0].citation_ids == ["BACKEND-1"]


def test_preflight_materializes_every_valid_guidance_reference() -> None:
    """Per-policy references must resolve even when the generator omits them globally."""
    chunks = [
        make_chunk(chunk_id="BACKEND-1", policy_id="BACKEND-LOG-001", domain="backend", score=0.99),
        make_chunk(
            chunk_id="SECURITY-1", policy_id="SECURITY-TOKEN-001", domain="security", score=0.98
        ),
    ]
    generator = MockGenerator(
        fixture_draft(chunks).model_copy(update={"citation_ids": ["BACKEND-1"]})
    )
    with PreflightService(
        source_catalog_resolver=fixture_catalog,
        embedder=MockEmbedder(),
        index_store=MockIndexStore(chunks),
        reranker=MockReranker(),
        generator=generator,
        compiler=MockPolicyCompiler(),
    ) as service:
        result = service.preflight(PreflightRequest(task="backend guidance", top_k=2))
    assert not result.insufficient_context
    by_id = {citation.chunk_id: citation for citation in result.citations}
    assert list(by_id) == ["BACKEND-1", "SECURITY-1"]
    for item in result.applicable_policies:
        for citation_id in item.citation_ids:
            assert (by_id[citation_id].policy_id, by_id[citation_id].title) == (
                item.policy_id,
                item.title,
            )
