"""Tests for LanceDB row conversion behavior."""

from __future__ import annotations

from decimal import Decimal

from policynim.storage.lancedb import LanceDBIndexStore


def test_chunk_from_row_accepts_numeric_like_distance_values() -> None:
    store = LanceDBIndexStore.__new__(LanceDBIndexStore)

    float_chunk = store._chunk_from_row(_row_with_distance(0.25))
    int_chunk = store._chunk_from_row(_row_with_distance(1))
    decimal_chunk = store._chunk_from_row(_row_with_distance(Decimal("0.4")))

    assert float_chunk.score == 0.75
    assert int_chunk.score == 0.0
    assert decimal_chunk.score == 0.6


def test_chunk_from_row_falls_back_when_distance_is_not_numeric() -> None:
    store = LanceDBIndexStore.__new__(LanceDBIndexStore)

    chunk = store._chunk_from_row(_row_with_distance("not-a-number"))

    assert chunk.score == 1.0


def test_chunk_from_row_normalizes_sequence_and_scalar_metadata() -> None:
    store = LanceDBIndexStore.__new__(LanceDBIndexStore)

    tuple_chunk = store._chunk_from_row(
        _row_with_distance(0.0, tags=("observability", "  "), grounded_in=(" https://a ", 2))
    )
    string_chunk = store._chunk_from_row(
        _row_with_distance(0.0, tags=" observability ", grounded_in=" https://b ")
    )
    scalar_chunk = store._chunk_from_row(_row_with_distance(0.0, tags=3, grounded_in=None))

    assert tuple_chunk.policy.tags == ["observability"]
    assert tuple_chunk.policy.grounded_in == ["https://a", "2"]
    assert string_chunk.policy.tags == ["observability"]
    assert string_chunk.policy.grounded_in == ["https://b"]
    assert scalar_chunk.policy.tags == ["3"]
    assert scalar_chunk.policy.grounded_in == []


def _row_with_distance(
    distance: object,
    *,
    tags: object = ("observability",),
    grounded_in: object = ("https://example.com/policy",),
) -> dict[str, object]:
    return {
        "_distance": distance,
        "chunk_id": "BACKEND-1",
        "path": "policies/backend/logging.md",
        "section": "Rules",
        "lines": "1-4",
        "text": "Use request ids in backend logs.",
        "policy_id": "BACKEND-LOG-001",
        "title": "Logging",
        "doc_type": "guidance",
        "domain": "backend",
        "tags": tags,
        "grounded_in": grounded_in,
    }
