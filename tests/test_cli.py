"""Tests for the Day 3 CLI surface."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from policynim.errors import ConfigurationError, MissingIndexError
from policynim.interfaces.cli import app
from policynim.types import (
    Citation,
    IngestResult,
    PolicyChunk,
    PolicyGuidance,
    PolicyMetadata,
    PreflightResult,
    ScoredChunk,
    SearchResult,
)

runner = CliRunner()


class FakeIngestService:
    """Static ingest service for CLI tests."""

    def run(self) -> IngestResult:
        return IngestResult(
            corpus_path="policies",
            index_uri="data/lancedb",
            table_name="policy_chunks",
            embedding_model="fake-model",
            document_count=8,
            chunk_count=24,
            embedding_dimension=2,
        )


class FakeSearchService:
    """Static search service for CLI tests."""

    def search(self, request) -> SearchResult:
        return SearchResult(
            query=request.query,
            domain=request.domain,
            top_k=request.top_k,
            hits=[
                ScoredChunk(
                    chunk_id="BACKEND-1",
                    path="policies/backend/logging.md",
                    section="Logging > Rules",
                    lines="5-8",
                    text="Use request ids in backend logs.",
                    policy=PolicyMetadata(
                        policy_id="BACKEND-LOG-001",
                        title="Logging",
                        doc_type="guidance",
                        domain="backend",
                    ),
                    score=0.99,
                )
            ],
        )


class FakePreflightService:
    """Static preflight service for CLI tests."""

    def __init__(self) -> None:
        self.closed = False

    def preflight(self, request) -> PreflightResult:
        return PreflightResult(
            task=request.task,
            domain=request.domain,
            summary="Follow background-job and auth cleanup policies.",
            applicable_policies=[
                PolicyGuidance(
                    policy_id="AUTH-001",
                    title="Auth Reviews",
                    rationale="Cleanup logic must preserve revocation and auditing behavior.",
                    citation_ids=["AUTH-1"],
                )
            ],
            implementation_guidance=[
                "Retain revocation checks before deleting stale refresh tokens."
            ],
            review_flags=["Ensure cleanup jobs redact token values from logs."],
            tests_required=[
                "Add a test that expired tokens are deleted without removing active ones."
            ],
            citations=[
                Citation(
                    policy_id="AUTH-001",
                    title="Auth Reviews",
                    path="policies/security/auth-review.md",
                    section="Cleanup",
                    lines="10-16",
                    chunk_id="AUTH-1",
                )
            ],
            insufficient_context=False,
        )

    def close(self) -> None:
        self.closed = True


class FakeIndexDumpService:
    """Static dump service for CLI tests."""

    def list_chunks(self) -> list[PolicyChunk]:
        return [
            PolicyChunk(
                chunk_id="BACKEND-1",
                path="policies/backend/logging.md",
                section="Logging > Rules",
                lines="5-8",
                text="Use request ids in backend logs.",
                policy=PolicyMetadata(
                    policy_id="BACKEND-LOG-001",
                    title="Logging",
                    doc_type="guidance",
                    domain="backend",
                ),
            )
        ]


def test_ingest_command_prints_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_ingest_service",
        lambda settings: FakeIngestService(),
    )

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 0
    assert "Indexed 24 chunks from 8 documents." in result.stdout
    assert "fake-model" in result.stdout


def test_ingest_command_surfaces_value_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_ingest_service",
        lambda settings: (_ for _ in ()).throw(ValueError("chunk/vector mismatch")),
    )

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 1
    assert "chunk/vector mismatch" in result.stderr


def test_search_command_prints_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_search_service",
        lambda settings: FakeSearchService(),
    )

    result = runner.invoke(app, ["search", "--query", "backend logs", "--top-k", "3"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["query"] == "backend logs"
    assert payload["top_k"] == 3
    assert payload["hits"][0]["chunk_id"] == "BACKEND-1"


def test_preflight_command_prints_json(monkeypatch) -> None:
    service = FakePreflightService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: service,
    )

    result = runner.invoke(
        app,
        ["preflight", "--task", "refresh token cleanup", "--domain", "security", "--top-k", "3"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["task"] == "refresh token cleanup"
    assert payload["domain"] == "security"
    assert payload["citations"][0]["chunk_id"] == "AUTH-1"
    assert service.closed is True


def test_dump_index_command_prints_chunks(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_index_dump_service",
        lambda settings: FakeIndexDumpService(),
    )

    result = runner.invoke(app, ["dump-index"])

    assert result.exit_code == 0
    assert "Indexed chunks: 1" in result.stdout
    assert "BACKEND-1" in result.stdout
    assert "Use request ids in backend logs." in result.stdout


def test_help_includes_dump_index_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "dump-index" in result.stdout


def test_dump_index_help_mentions_less_for_paging() -> None:
    result = runner.invoke(app, ["dump-index", "--help"])

    assert result.exit_code == 0
    assert "add ` | less`" in result.stdout
    assert "paging large output" in result.stdout


def test_preflight_help_mentions_current_top_k_behavior() -> None:
    result = runner.invoke(app, ["preflight", "--help"])

    assert result.exit_code == 0
    assert "Retrieval depth." in result.stdout
    assert "1-20." in result.stdout
    assert "Reserved retrieval depth" not in result.stdout


def test_search_command_surfaces_configuration_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_search_service",
        lambda settings: (_ for _ in ()).throw(ConfigurationError("missing NVIDIA key")),
    )

    result = runner.invoke(app, ["search", "--query", "backend logs"])

    assert result.exit_code == 1
    assert "missing NVIDIA key" in result.stderr


def test_preflight_command_surfaces_missing_index_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: (_ for _ in ()).throw(MissingIndexError("Run `policynim ingest` first.")),
    )

    result = runner.invoke(app, ["preflight", "--task", "refresh token cleanup"])

    assert result.exit_code == 1
    assert "Run `policynim ingest` first." in result.stderr


def test_preflight_command_surfaces_configuration_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: (_ for _ in ()).throw(ConfigurationError("missing NVIDIA key")),
    )

    result = runner.invoke(app, ["preflight", "--task", "refresh token cleanup"])

    assert result.exit_code == 1
    assert "missing NVIDIA key" in result.stderr


def test_preflight_command_closes_service_when_it_errors(monkeypatch) -> None:
    class FailingPreflightService(FakePreflightService):
        def preflight(self, request) -> PreflightResult:
            raise MissingIndexError("Run `policynim ingest` first.")

    service = FailingPreflightService()
    monkeypatch.setattr(
        "policynim.interfaces.cli.create_preflight_service",
        lambda settings: service,
    )

    result = runner.invoke(app, ["preflight", "--task", "refresh token cleanup"])

    assert result.exit_code == 1
    assert service.closed is True
