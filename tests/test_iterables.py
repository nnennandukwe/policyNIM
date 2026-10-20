"""Behavioral contract for shared first-seen hashable de-duplication."""

from collections.abc import Hashable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import assert_type, cast

import pytest

from policynim.iterables import ordered_unique


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], []),
        (["one"], ["one"]),
        (["b", "a", "b", "c", "a"], ["b", "a", "c"]),
        (["", " ", "", "A", "a", " "], ["", " ", "A", "a"]),
    ],
)
def test_string_values_keep_first_seen_order_without_normalization(
    values: list[str], expected: list[str]
) -> None:
    """Existing string callers retain whitespace, case and stable first-occurrence ordering."""
    original = values.copy()
    result = ordered_unique(values)
    assert result == expected and values == original
    assert_type(result, list[str])


def test_generator_input_is_consumed_once() -> None:
    """One-pass inputs don't need materialization or replay support."""
    visited: list[str] = []

    def source() -> Iterator[str]:
        """Record each produced value so duplicate iteration can't go unnoticed."""
        for value in ("second", "first", "second"):
            visited.append(value)
            yield value

    result = ordered_unique(source())
    assert result == ["second", "first"]
    assert visited == ["second", "first", "second"]


@dataclass(frozen=True)
class EqualRecord:
    """Make records equal by key while retaining which original instance won."""

    key: str
    label: str = field(compare=False)


def test_equal_hashable_records_preserve_the_first_original_instance() -> None:
    """Equal later objects can't replace the original value or change its runtime type."""
    first = EqualRecord("key", "first")
    later = EqualRecord("key", "later")
    distinct = EqualRecord("other", "other")
    result = ordered_unique((first, later, distinct, first))
    assert result == [first, distinct]
    assert result[0] is first and result[1] is distinct
    assert_type(result, list[EqualRecord])


def test_python_equal_numeric_values_keep_first_value_types() -> None:
    """Python equality treats bool/int/float aliases as duplicates without coercing the survivor."""
    result = ordered_unique([True, 1, 1.0, False, 0, 2])
    assert result == [True, False, 2]
    assert [type(value) for value in result] == [bool, bool, int]
    integers = ordered_unique([2, 1, 2])
    assert_type(integers, list[int])


def test_unhashable_input_keeps_python_type_error() -> None:
    """A violated hashable contract doesn't introduce string conversion or silent filtering."""
    invalid = cast(Iterable[Hashable], [["nested"]])
    with pytest.raises(TypeError, match="unhashable"):
        ordered_unique(invalid)


def test_source_iteration_error_is_preserved() -> None:
    """Caller iteration failures propagate unchanged rather than returning partial results."""
    failure = RuntimeError("iteration failed")

    def source() -> Iterator[str]:
        """Fail only after a valid value has crossed the iterator boundary."""
        yield "first"
        raise failure

    with pytest.raises(RuntimeError) as caught:
        ordered_unique(source())
    assert caught.value is failure
