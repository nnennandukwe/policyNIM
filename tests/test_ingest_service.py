"""Tests for the Day 3 ingest service."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from textwrap import dedent

from policynim.services.ingest import IngestService
from policynim.storage import LanceDBIndexStore


class MockEmbedder:
    """Deterministic offline embedder for service tests."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(index + 1), float(len(text))] for index, text in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, float(len(text))]


def write_policy(path: Path, content: str) -> None:
    """Write one temporary policy file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip(), encoding="utf-8")


def test_ingest_service_builds_and_rebuilds_local_index(tmp_path: Path) -> None:
    policies_dir = tmp_path / "policies"
    write_policy(
        policies_dir / "backend" / "logging.md",
        """
        ---
        policy_id: BACKEND-LOG-001
        title: Logging
        domain: backend
        ---
        # Logging

        ## Rules

        Log with context.
        """,
    )
    write_policy(
        policies_dir / "security" / "tokens.md",
        """
        ---
        policy_id: SECURITY-TOKEN-001
        title: Tokens
        domain: security
        ---
        # Tokens

        ## Rules

        Expire session tokens.
        """,
    )

    store = LanceDBIndexStore(uri=tmp_path / "index", table_name="policy_chunks")
    service = IngestService(
        embedder=MockEmbedder(),
        index_store=store,
        corpus_root=policies_dir,
        embedding_model="mock-embedder",
    )

    first_result = service.run()

    assert first_result.document_count == 2
    assert first_result.chunk_count == store.count()
    assert first_result.embedding_dimension == 2

    (policies_dir / "security" / "tokens.md").unlink()

    second_result = service.run()

    assert second_result.document_count == 1
    assert second_result.chunk_count < first_result.chunk_count
    assert store.count() == second_result.chunk_count
