"""Tests for the Day 3 ingest service."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from textwrap import dedent

import pytest

from policynim.services.ingest import IngestService
from policynim.storage import LanceDBIndexStore
from policynim.types import EmbeddedChunk


class MockEmbedder:
    """Deterministic offline embedder for service tests."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(index + 1), float(len(text))] for index, text in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, float(len(text))]


class FailingEmbedder:
    """Embedder that fails before index replacement."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("embedding failed")

    def embed_query(self, text: str) -> list[float]:
        return [1.0, float(len(text))]


def write_policy(path: Path, content: str) -> None:
    """Write one temporary policy file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip(), encoding="utf-8")


class RecordingIndexStore:
    """Track whether replace was called during ingest."""

    def __init__(self, *, uri: Path, table_name: str) -> None:
        self.uri = uri
        self.table_name = table_name
        self.replace_calls = 0

    def replace(self, chunks: Sequence[EmbeddedChunk]) -> None:
        self.replace_calls += 1

    def count(self) -> int:
        return 0


def test_ingest_service_builds_and_rebuilds_local_index(tmp_path: Path) -> None:
    policies_dir = tmp_path / "policies"
    artifact_path = tmp_path / "runtime" / "runtime_rules.json"
    write_policy(
        policies_dir / "backend" / "logging.md",
        """
        ---
        policy_id: BACKEND-LOG-001
        title: Logging
        domain: backend
        runtime_rules:
          - action: shell_command
            effect: confirm
            reason: Review deploy commands.
            command_regexes:
              - "^deploy:"
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
        runtime_rules_artifact_path=artifact_path,
    )

    first_result = service.run()

    assert first_result.document_count == 2
    assert first_result.chunk_count == store.count()
    assert first_result.embedding_dimension == 2
    first_artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert first_artifact["schema_version"] == 1
    assert first_artifact["rules"] == [
        {
            "policy_id": "BACKEND-LOG-001",
            "title": "Logging",
            "domain": "backend",
            "source_path": "policies/backend/logging.md",
            "action": "shell_command",
            "effect": "confirm",
            "reason": "Review deploy commands.",
            "path_globs": [],
            "command_regexes": ["^deploy:"],
            "url_host_patterns": [],
            "start_line": 6,
            "end_line": 10,
        }
    ]

    (policies_dir / "security" / "tokens.md").unlink()

    second_result = service.run()

    assert second_result.document_count == 1
    assert second_result.chunk_count < first_result.chunk_count
    assert store.count() == second_result.chunk_count
    second_artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert len(second_artifact["rules"]) == 1


def test_ingest_service_rejects_unwritable_runtime_rules_artifact_before_index_replace(
    tmp_path: Path,
) -> None:
    policies_dir = tmp_path / "policies"
    blocking_parent = tmp_path / "blocked-parent"
    blocking_parent.write_text("not a directory", encoding="utf-8")
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

    store = RecordingIndexStore(uri=tmp_path / "index", table_name="policy_chunks")
    service = IngestService(
        embedder=MockEmbedder(),
        index_store=store,
        corpus_root=policies_dir,
        embedding_model="mock-embedder",
        runtime_rules_artifact_path=blocking_parent / "runtime_rules.json",
    )

    with pytest.raises(OSError):
        service.run()

    assert store.replace_calls == 0


def test_ingest_service_does_not_leave_staged_runtime_rule_artifacts_on_embed_failure(
    tmp_path: Path,
) -> None:
    policies_dir = tmp_path / "policies"
    runtime_dir = tmp_path / "runtime"
    artifact_path = runtime_dir / "runtime_rules.json"
    write_policy(
        policies_dir / "backend" / "logging.md",
        """
        ---
        policy_id: BACKEND-LOG-001
        title: Logging
        domain: backend
        runtime_rules:
          - action: shell_command
            effect: confirm
            reason: Review deploy commands.
            command_regexes:
              - "^deploy:"
        ---
        # Logging

        ## Rules

        Log with context.
        """,
    )

    service = IngestService(
        embedder=FailingEmbedder(),
        index_store=RecordingIndexStore(uri=tmp_path / "index", table_name="policy_chunks"),
        corpus_root=policies_dir,
        embedding_model="mock-embedder",
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(RuntimeError, match="embedding failed"):
        service.run()

    assert not runtime_dir.exists()


def test_ingest_service_rejects_directory_runtime_rules_artifact_before_index_replace(
    tmp_path: Path,
) -> None:
    policies_dir = tmp_path / "policies"
    artifact_path = tmp_path / "runtime" / "runtime_rules.json"
    artifact_path.mkdir(parents=True)
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

    store = RecordingIndexStore(uri=tmp_path / "index", table_name="policy_chunks")
    service = IngestService(
        embedder=MockEmbedder(),
        index_store=store,
        corpus_root=policies_dir,
        embedding_model="mock-embedder",
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(OSError, match="must not be a directory"):
        service.run()

    assert store.replace_calls == 0
    assert not list(artifact_path.parent.glob(".runtime_rules.json.*.tmp"))
