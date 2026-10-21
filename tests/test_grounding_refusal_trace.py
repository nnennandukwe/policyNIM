"""Content-free reasons for unchanged grounding refusals at the public preflight boundary."""

from __future__ import annotations

import pytest
from test_preflight_service import (
    MockEmbedder,
    MockGenerator,
    MockIndexStore,
    MockPolicyCompiler,
    MockReranker,
    fixture_catalog,
    make_chunk,
)

from policynim.services.preflight import PreflightService
from policynim.types import GeneratedPolicyGuidance, GeneratedPreflightDraft, PreflightRequest


@pytest.mark.parametrize(
    "refusal",
    [
        "model_insufficient_context",
        "unknown_citation",
        "policy_id_mismatch",
        "policy_title_mismatch",
        "missing_policy_guidance",
    ],
)
def test_public_trace_explains_grounding_refusal_without_weakening_result(refusal: str) -> None:
    """Keep the empty fail-closed result while exposing only a fixed diagnostic code."""
    chunk = make_chunk(
        chunk_id="BACKEND-1", policy_id="BACKEND-LOG-001", domain="backend", score=0.9
    )
    guidance = GeneratedPolicyGuidance(
        policy_id=chunk.policy.policy_id,
        title=chunk.policy.title,
        rationale="Review retained source.",
        citation_ids=[chunk.chunk_id],
    )
    draft = GeneratedPreflightDraft(
        summary="generated-private-sentinel",
        applicable_policies=[guidance],
        citation_ids=[chunk.chunk_id],
    )
    if refusal == "model_insufficient_context":
        draft.insufficient_context = True
    elif refusal == "unknown_citation":
        guidance.citation_ids = ["private-invalid-reference"]
    elif refusal == "policy_id_mismatch":
        guidance.policy_id = "private-invalid-owner"
    elif refusal == "policy_title_mismatch":
        guidance.title = "private-invalid-title"
    else:
        draft.applicable_policies = []
    service = PreflightService(
        source_catalog_resolver=fixture_catalog,
        embedder=MockEmbedder(),
        index_store=MockIndexStore([chunk]),
        reranker=MockReranker(),
        compiler=MockPolicyCompiler(),
        generator=MockGenerator(draft),
    )
    request = PreflightRequest(task="backend guidance", top_k=1)
    traced = service.preflight_with_trace(request)
    assert traced.result == service.preflight(request)
    assert traced.result.insufficient_context
    assert not traced.result.citations and not traced.result.applicable_policies
    assert not traced.result.plan_steps and not traced.result.implementation_guidance
    diagnostic = next(step for step in traced.trace_steps if step.kind == "grounding_refusal")
    assert diagnostic.step_id == "grounding_validation"
    assert diagnostic.summary == f"Grounding refused: {refusal}."
    assert diagnostic.citation_ids == []
    assert "private" not in diagnostic.model_dump_json()


@pytest.mark.parametrize("empty_context", [False, True])
def test_shared_validator_retains_empty_input_refusals(empty_context: bool) -> None:
    """Exercise the two lower-level guards that complete compilation normally prevents."""
    from policynim.services.preflight import (
        _grounding_refusal_code,
        _validate_and_materialize_result,
    )

    context = (
        []
        if empty_context
        else [
            make_chunk(
                chunk_id="BACKEND-1", policy_id="BACKEND-LOG-001", domain="backend", score=0.9
            )
        ]
    )
    draft = GeneratedPreflightDraft(summary="No cited guidance.")
    assert _grounding_refusal_code(context, draft) == (
        "empty_source_context" if empty_context else "missing_citations"
    )
    assert (
        _validate_and_materialize_result(PreflightRequest(task="backend guidance"), context, draft)
        is None
    )
