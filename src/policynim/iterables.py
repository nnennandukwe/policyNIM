"""Focused iterable helpers shared across PolicyNIM modules."""

from __future__ import annotations

from collections.abc import Iterable


def ordered_unique(values: Iterable[str]) -> list[str]:
    """Keep first-seen string order while dropping duplicates."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
