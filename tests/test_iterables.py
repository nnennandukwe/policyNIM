from __future__ import annotations

from policynim.iterables import ordered_unique


def test_ordered_unique_preserves_first_seen_order() -> None:
    assert ordered_unique(["alpha", "beta", "alpha", "", "beta", "gamma"]) == [
        "alpha",
        "beta",
        "",
        "gamma",
    ]
