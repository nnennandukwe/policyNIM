"""Shared iterable operations that preserve caller values and ordering."""

from collections.abc import Hashable, Iterable
from typing import TypeVar

_HashableValue = TypeVar("_HashableValue", bound=Hashable)


def ordered_unique(values: Iterable[_HashableValue]) -> list[_HashableValue]:
    """Keep the first instance of each equal hashable value in input order."""
    seen: set[_HashableValue] = set()
    ordered: list[_HashableValue] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
