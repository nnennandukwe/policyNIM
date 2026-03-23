"""Opt-in live NVIDIA embedding smoke coverage."""

from __future__ import annotations

import pytest

from policynim.providers import NVIDIAEmbedder
from policynim.settings import get_settings


@pytest.mark.skipif(
    not (get_settings().nvidia_api_key or "").strip(),
    reason="NVIDIA_API_KEY is not configured.",
)
def test_nvidia_embed_query_live() -> None:
    embedder = NVIDIAEmbedder.from_settings(get_settings())

    vector = embedder.embed_query("PolicyNIM live Day 3 smoke test")

    assert vector
    assert all(isinstance(value, float) for value in vector)
