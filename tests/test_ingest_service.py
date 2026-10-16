"""Tests for the ingest service."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from textwrap import dedent

import pytest

from policynim.services.ingest import IngestService
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import EmbeddedChunk, EmbeddingIdentity


class MockEmbedder:
    """Deterministic offline embedder for service tests."""

    def __init__(self) -> None:
        """Track whether the ingest service closed the embedder."""
        self.closed = False

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(index + 1), float(len(text))] for index, text in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, float(len(text))]

    def close(self) -> None:
        """Record that the embedder was closed."""
        self.closed = True


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

    def validate_identity(self) -> None:
        """The fixture represents a compatible index."""

    def replace(self, chunks: Sequence[EmbeddedChunk], *, complete: bool = True) -> str:
        """Implement the test store's replacement protocol with a synthetic build receipt."""
        self.replace_calls += 1
        return "test-build"

    def validate_replacement(self) -> None:
        """Allow replacement in this recording fixture."""

    def complete_ingest(self, build_id: str) -> None:
        """Accept completion in this recording fixture."""

    def count(self) -> int:
        return 0


def test_ingest_service_builds_and_refreshes_into_separate_paths(tmp_path: Path) -> None:
    """Refresh into separate outputs while preserving the original complete pair."""
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

    store = SQLiteVecIndexStore(
        embedding_identity=EmbeddingIdentity(
            model="mock-embedder", endpoint="https://example.invalid/v1"
        ),
        path=tmp_path / "index.sqlite3",
    )
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

    original_index = store.path.read_bytes()
    original_rules = artifact_path.read_bytes()
    refreshed_store = SQLiteVecIndexStore(
        embedding_identity=EmbeddingIdentity(
            model="mock-embedder", endpoint="https://example.invalid/v1"
        ),
        path=tmp_path / "refreshed.sqlite3",
    )
    refreshed_rules = tmp_path / "runtime" / "refreshed_rules.json"
    refresh = IngestService(
        embedder=MockEmbedder(),
        index_store=refreshed_store,
        corpus_root=policies_dir,
        embedding_model="mock-embedder",
        runtime_rules_artifact_path=refreshed_rules,
    )
    second_result = refresh.run()
    assert store.path.read_bytes() == original_index
    assert artifact_path.read_bytes() == original_rules
    store.validate_identity()
    refreshed_store.validate_identity()

    assert second_result.document_count == 1
    assert second_result.chunk_count < first_result.chunk_count
    assert refreshed_store.count() == second_result.chunk_count
    second_artifact = json.loads(refreshed_rules.read_text(encoding="utf-8"))
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


def test_ingest_service_close_closes_embedder(tmp_path: Path) -> None:
    """Release the embedder when the ingest service is closed."""
    embedder = MockEmbedder()
    service = IngestService(
        embedder=embedder,
        index_store=RecordingIndexStore(uri=tmp_path / "index", table_name="policy_chunks"),
        corpus_root=tmp_path / "policies",
        embedding_model="mock-embedder",
        runtime_rules_artifact_path=tmp_path / "runtime" / "runtime_rules.json",
    )

    service.close()

    assert embedder.closed is True


def test_generation_index_failure_preserves_prior_runtime_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #87 failing-index regression keeps immutable prior rules without restoring files."""
    from typing import Any

    from policynim.services import generations
    from policynim.settings import Settings

    source = tmp_path / "policies"
    write_policy(
        source / "publication.md",
        """
        ---
        policy_id: PUBLICATION
        title: Publication
        domain: backend
        runtime_rules:
          - action: shell_command
            effect: block
            reason: Review publication.
            command_regexes:
              - "^publish$"
        ---
        # Publication

        Review publication before changing active state.
    """,
    )
    identity = EmbeddingIdentity(model="mock-embedder", endpoint="https://example.invalid/v1")
    no_env: dict[str, Any] = {"_env_file": None}
    settings = Settings(
        **no_env,
        corpus_dir=source,
        nvidia_embed_model=identity.model,
        nvidia_base_url=identity.endpoint,
    )
    store = generations.GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.build(settings, MockEmbedder())
    snapshot = store.inspect()
    old_index, old_rules = snapshot.index_path.read_bytes(), snapshot.rules_path.read_bytes()
    pointer = (store.root / "active.json").read_bytes()
    policy = source / "publication.md"
    policy.write_text(policy.read_text().replace("Review publication.", "Review new publication."))

    class FailingIndexStore(SQLiteVecIndexStore):
        """Fail the new index write after the new rules have already been staged."""

        attempts = 0

        def replace(self, chunks: Sequence[EmbeddedChunk], *, complete: bool = True) -> str:
            """Preserve the old complete generation when the staged index write fails."""
            type(self).attempts += 1
            raise OSError("Injected index replacement failure.")

    monkeypatch.setattr(generations, "SQLiteVecIndexStore", FailingIndexStore)
    with pytest.raises(generations.GenerationError):
        store.build(settings, MockEmbedder())
    assert FailingIndexStore.attempts == 1
    assert (store.root / "active.json").read_bytes() == pointer
    assert snapshot.index_path.read_bytes() == old_index
    assert snapshot.rules_path.read_bytes() == old_rules
    assert store.inspect().generation_id == first.generation_id
    assert not list((store.root / "staging").iterdir())


def test_failed_generation_activation_never_rewrites_prior_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed commit leaves prior rules intact, so no fallible restoration phase exists."""
    from typing import Any

    from policynim.services import generations
    from policynim.settings import Settings

    source = tmp_path / "policies"
    write_policy(source / "policy.md", "# Release policy\n\nReview changes before release.\n")
    identity = EmbeddingIdentity(model="mock-embedder", endpoint="https://example.invalid/v1")
    no_env: dict[str, Any] = {"_env_file": None}
    settings = Settings(
        **no_env,
        corpus_dir=source,
        nvidia_embed_model=identity.model,
        nvidia_base_url=identity.endpoint,
    )
    store = generations.GenerationStore(root=tmp_path / "generations", embedding=identity)
    first = store.build(settings, MockEmbedder())
    snapshot = store.inspect()
    old_index, old_rules = snapshot.index_path.read_bytes(), snapshot.rules_path.read_bytes()
    real_replace = generations.os.replace
    destinations: list[Path] = []

    def fail_activation(source, destination, *args, **kwargs):
        """Fail the actual pointer replacement while allowing the new staged SQLite write."""
        destinations.append(Path(destination))
        if Path(destination) == store.root / "active.json":
            raise OSError("Injected activation replacement failure.")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(generations.os, "replace", fail_activation)
    with pytest.raises(generations.GenerationError):
        store.build(settings, MockEmbedder())
    assert snapshot.index_path not in destinations and snapshot.rules_path not in destinations
    assert snapshot.index_path.read_bytes() == old_index
    assert snapshot.rules_path.read_bytes() == old_rules
    assert store.inspect().generation_id == first.generation_id
