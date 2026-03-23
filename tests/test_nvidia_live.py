"""Opt-in live NVIDIA provider smoke coverage."""

from __future__ import annotations

import pytest

from policynim.errors import ProviderError
from policynim.settings import get_settings
from policynim.types import PolicyMetadata, PreflightRequest, ScoredChunk

pytest.importorskip("policynim.providers.nvidia")

import policynim.providers.nvidia as nvidia_module


@pytest.mark.skipif(
    not (get_settings().nvidia_api_key or "").strip(),
    reason="NVIDIA_API_KEY is not configured.",
)
def test_nvidia_embed_query_live() -> None:
    embedder = nvidia_module.NVIDIAEmbedder.from_settings(get_settings())

    vector = embedder.embed_query("PolicyNIM live Day 3 smoke test")

    assert vector
    assert all(isinstance(value, float) for value in vector)


@pytest.mark.skipif(
    not (get_settings().nvidia_api_key or "").strip(),
    reason="NVIDIA_API_KEY is not configured.",
)
def test_nvidia_rerank_live() -> None:
    reranker_cls = getattr(nvidia_module, "NVIDIAReranker", None)
    if reranker_cls is None:
        pytest.skip("NVIDIAReranker is not implemented yet.")

    reranker = reranker_cls.from_settings(get_settings())
    candidates = [
        ScoredChunk(
            chunk_id="A",
            path="policies/example/a.md",
            section="Rules",
            lines="1-2",
            text="Use explicit request ids in logs.",
            policy=PolicyMetadata(
                policy_id="A-1",
                title="Logging",
                doc_type="guidance",
                domain="backend",
            ),
            score=0.1,
        ),
        ScoredChunk(
            chunk_id="B",
            path="policies/example/b.md",
            section="Rules",
            lines="1-2",
            text="Rotate session tokens promptly.",
            policy=PolicyMetadata(
                policy_id="B-1",
                title="Tokens",
                doc_type="guidance",
                domain="security",
            ),
            score=0.2,
        ),
    ]

    try:
        reranked = reranker.rerank("request ids in logs", candidates, top_k=2)
    except ProviderError as exc:
        pytest.skip(f"NVIDIAReranker live smoke could not complete: {exc}")

    assert reranked
    assert reranked[0].chunk_id == "A"
    assert all(hit.score is not None for hit in reranked)


@pytest.mark.skipif(
    not (get_settings().nvidia_api_key or "").strip(),
    reason="NVIDIA_API_KEY is not configured.",
)
def test_nvidia_generate_preflight_live() -> None:
    generator_cls = getattr(nvidia_module, "NVIDIAGenerator", None)
    if generator_cls is None:
        pytest.skip("NVIDIAGenerator is not implemented yet.")

    generator = generator_cls.from_settings(get_settings())
    context = [
        ScoredChunk(
            chunk_id="BACKEND-1",
            path="policies/backend/logging.md",
            section="Logging > Rules",
            lines="5-8",
            text="Use request ids in backend logs.",
            policy=PolicyMetadata(
                policy_id="BACKEND-LOG-001",
                title="Logging",
                doc_type="guidance",
                domain="backend",
            ),
            score=0.99,
        )
    ]

    result = generator.generate_preflight(
        PreflightRequest(task="add request ids to backend logs", top_k=3),
        context,
    )

    assert result.summary
    assert result.applicable_policies
    assert result.applicable_policies[0].citation_ids
    assert result.citation_ids
