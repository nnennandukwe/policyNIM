"""Tests for iterable helper utilities."""

from __future__ import annotations

from policynim.iterables import ordered_unique


def test_ordered_unique_preserves_first_seen_order() -> None:
    """Keep the first instance of each value while preserving order."""
    assert ordered_unique(["alpha", "beta", "alpha", "gamma", "beta"]) == [
        "alpha",
        "beta",
        "gamma",
    ]


def test_ordered_unique_accepts_generic_iterables() -> None:
    """Support iterators rather than only concrete sequence inputs."""
    values = (value for value in ("one", "one", "two"))
    assert ordered_unique(values) == ["one", "two"]
