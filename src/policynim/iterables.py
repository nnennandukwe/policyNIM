"""Iterable helpers for deterministic ordering behavior."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from typing import TypeVar

T = TypeVar("T", bound=Hashable)


def ordered_unique(values: Iterable[T]) -> list[T]:
    """Return first-seen hashable values while preserving input order."""
    seen: set[T] = set()
    ordered: list[T] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
