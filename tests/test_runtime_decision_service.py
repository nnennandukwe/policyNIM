"""Tests for the Day 2 runtime decision service."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from policynim.errors import (
    MissingIndexError,
    RuntimeCitationLinkError,
    RuntimeRulesArtifactInvalidError,
    RuntimeRulesArtifactMissingError,
)
from policynim.services.runtime_decision import RuntimeDecisionService
from policynim.types import (
    CompiledRuntimeRule,
    EmbeddedChunk,
    FileWriteActionRequest,
    HTTPRequestActionRequest,
    PolicyChunk,
    PolicyMetadata,
    RuntimeActionKind,
    RuntimeRuleEffect,
    RuntimeRulesArtifact,
    ScoredChunk,
    ShellCommandActionRequest,
)


class MockIndexStore:
    """Lightweight index store for runtime decision tests."""

    def __init__(self, chunks: list[PolicyChunk], *, exists: bool = True) -> None:
        self._chunks = list(chunks)
        self._exists = exists
        self.list_chunks_calls = 0

    def exists(self) -> bool:
        return self._exists

    def count(self) -> int:
        return len(self._chunks) if self._exists else 0

    def list_chunks(self) -> list[PolicyChunk]:
        self.list_chunks_calls += 1
        return list(self._chunks)

    def replace(self, chunks: Sequence[EmbeddedChunk]) -> None:
        self._chunks = [PolicyChunk(**chunk.model_dump(exclude={"vector"})) for chunk in chunks]

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        domain: str | None = None,
    ) -> list[ScoredChunk]:
        raise AssertionError("RuntimeDecisionService must not perform semantic search.")


def test_runtime_decision_service_returns_allow_for_unmatched_shell_command(tmp_path: Path) -> None:
    index_store = MockIndexStore([make_chunk(lines="bad-span")])
    artifact_path = write_runtime_rules_artifact(
        tmp_path / "runtime_rules.json",
        rules=[
            make_rule(
                action="shell_command",
                effect="confirm",
                reason="Review deploy commands.",
                command_regexes=["^deploy:"],
            )
        ],
    )
    service = RuntimeDecisionService(
        index_store=index_store,
        runtime_rules_artifact_path=artifact_path,
    )

    result = service.decide(
        ShellCommandActionRequest(
            kind="shell_command",
            task="Run unit tests.",
            cwd=tmp_path,
            command=["make", "test"],
        )
    )

    assert result.decision == "allow"
    assert result.summary == "No runtime policy rules matched this action."
    assert result.matched_rules == []
    assert result.citations == []
    assert index_store.list_chunks_calls == 0


def test_runtime_decision_service_matches_shell_command_rule_and_links_citations(
    tmp_path: Path,
) -> None:
    artifact_path = write_runtime_rules_artifact(
        tmp_path / "runtime_rules.json",
        rules=[
            make_rule(
                action="shell_command",
                effect="confirm",
                reason="Review deploy commands.",
                command_regexes=["^deploy:"],
                start_line=6,
                end_line=10,
            )
        ],
    )
    service = RuntimeDecisionService(
        index_store=MockIndexStore(
            [
                make_chunk(
                    lines="8-12",
                    path="policies/backend/runtime.md",
                    policy_id="BACKEND-RUNTIME-001",
                )
            ]
        ),
        runtime_rules_artifact_path=artifact_path,
    )

    result = service.decide(
        ShellCommandActionRequest(
            kind="shell_command",
            task="Deploy staging stack.",
            cwd=tmp_path,
            command=["deploy:staging"],
        )
    )

    assert result.decision == "confirm"
    assert result.summary == "Review deploy commands."
    assert [rule.reason for rule in result.matched_rules] == ["Review deploy commands."]
    assert [citation.chunk_id for citation in result.citations] == ["BACKEND-1"]


def test_runtime_decision_service_matches_file_write_rule_using_repo_relative_path(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    cwd = repo_root / "tools"
    artifact_path = write_runtime_rules_artifact(
        tmp_path / "runtime_rules.json",
        rules=[
            make_rule(
                action="file_write",
                effect="block",
                reason="Protect production secrets files.",
                path_globs=["secrets/prod.env"],
            )
        ],
    )
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk()]),
        runtime_rules_artifact_path=artifact_path,
    )

    result = service.decide(
        FileWriteActionRequest(
            kind="file_write",
            task="Update production secret file.",
            cwd=cwd,
            repo_root=repo_root,
            path=Path("../secrets/prod.env"),
            content="TOKEN=redacted",
        )
    )

    assert result.decision == "block"
    assert result.summary == "Protect production secrets files."
    assert [citation.chunk_id for citation in result.citations] == ["BACKEND-1"]


def test_runtime_decision_service_matches_repo_relative_file_rules_through_symlinks(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    cwd = repo_root / "tools"
    outside_secrets = tmp_path / "outside" / "secrets"
    outside_secrets.mkdir(parents=True)
    repo_root.mkdir()
    cwd.mkdir()
    try:
        (repo_root / "secrets").symlink_to(outside_secrets, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable in this environment: {exc}")

    artifact_path = write_runtime_rules_artifact(
        tmp_path / "runtime_rules.json",
        rules=[
            make_rule(
                action="file_write",
                effect="block",
                reason="Protect production secrets files.",
                path_globs=["secrets/prod.env"],
            )
        ],
    )
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk()]),
        runtime_rules_artifact_path=artifact_path,
    )

    result = service.decide(
        FileWriteActionRequest(
            kind="file_write",
            task="Update production secret file through a symlinked repo path.",
            cwd=cwd,
            repo_root=repo_root,
            path=Path("../secrets/prod.env"),
            content="TOKEN=redacted",
        )
    )

    assert result.decision == "block"
    assert [citation.chunk_id for citation in result.citations] == ["BACKEND-1"]


def test_runtime_decision_service_matches_http_request_host_case_insensitively(
    tmp_path: Path,
) -> None:
    artifact_path = write_runtime_rules_artifact(
        tmp_path / "runtime_rules.json",
        rules=[
            make_rule(
                action="http_request",
                effect="block",
                reason="Block direct NVIDIA API calls.",
                url_host_patterns=["*.NVIDIA.COM"],
            )
        ],
    )
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk()]),
        runtime_rules_artifact_path=artifact_path,
    )

    result = service.decide(
        HTTPRequestActionRequest.model_validate(
            {
                "kind": "http_request",
                "task": "Call the NVIDIA API directly.",
                "cwd": tmp_path,
                "method": "get",
                "url": "https://Integrate.API.NVIDIA.com/v1/models",
            }
        )
    )

    assert result.decision == "block"
    assert result.summary == "Block direct NVIDIA API calls."
    assert [citation.chunk_id for citation in result.citations] == ["BACKEND-1"]


def test_runtime_decision_service_preserves_match_order_and_block_overrides_confirm(
    tmp_path: Path,
) -> None:
    artifact_path = write_runtime_rules_artifact(
        tmp_path / "runtime_rules.json",
        rules=[
            make_rule(
                action="shell_command",
                effect="confirm",
                reason="Review deploy commands.",
                command_regexes=["^deploy:"],
                start_line=6,
                end_line=10,
            ),
            make_rule(
                action="shell_command",
                effect="block",
                reason="Do not run production deploys locally.",
                command_regexes=["^deploy:"],
                start_line=11,
                end_line=14,
            ),
        ],
    )
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk(lines="1-20")]),
        runtime_rules_artifact_path=artifact_path,
    )

    result = service.decide(
        ShellCommandActionRequest(
            kind="shell_command",
            task="Deploy from local machine.",
            cwd=tmp_path,
            command=["deploy:prod"],
        )
    )

    assert result.decision == "block"
    assert [rule.reason for rule in result.matched_rules] == [
        "Review deploy commands.",
        "Do not run production deploys locally.",
    ]
    assert result.summary == "Review deploy commands.; Do not run production deploys locally."
    assert [citation.chunk_id for citation in result.citations] == ["BACKEND-1"]


def test_runtime_decision_service_requires_runtime_rules_artifact(tmp_path: Path) -> None:
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk()]),
        runtime_rules_artifact_path=tmp_path / "missing-runtime-rules.json",
    )

    with pytest.raises(RuntimeRulesArtifactMissingError, match="Run `policynim ingest`"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Run tests.",
                cwd=tmp_path,
                command=["make", "test"],
            )
        )


def test_runtime_decision_service_rejects_directory_artifact_path(tmp_path: Path) -> None:
    artifact_path = tmp_path / "runtime_rules.json"
    artifact_path.mkdir()
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk()]),
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(RuntimeRulesArtifactInvalidError, match="must be a file"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Run tests.",
                cwd=tmp_path,
                command=["make", "test"],
            )
        )


def test_runtime_decision_service_rejects_unreadable_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_path = write_runtime_rules_artifact(tmp_path / "runtime_rules.json", rules=[])
    original_read_text = Path.read_text

    def raising_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if self == artifact_path:
            raise OSError("permission denied")
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", raising_read_text)
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk()]),
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(RuntimeRulesArtifactInvalidError, match="Fix the file permissions"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Run tests.",
                cwd=tmp_path,
                command=["make", "test"],
            )
        )


def test_runtime_decision_service_rejects_malformed_artifact_json(tmp_path: Path) -> None:
    artifact_path = tmp_path / "runtime_rules.json"
    artifact_path.write_text("{not json", encoding="utf-8")
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk()]),
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(RuntimeRulesArtifactInvalidError, match="not valid JSON"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Run tests.",
                cwd=tmp_path,
                command=["make", "test"],
            )
        )


def test_runtime_decision_service_rejects_unsupported_artifact_schema_version(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "runtime_rules.json"
    artifact_path.write_text(json.dumps({"schema_version": 2, "rules": []}), encoding="utf-8")
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk()]),
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(RuntimeRulesArtifactInvalidError, match="schema_version"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Run tests.",
                cwd=tmp_path,
                command=["make", "test"],
            )
        )


def test_runtime_decision_service_rejects_invalid_command_regex(tmp_path: Path) -> None:
    artifact_path = write_runtime_rules_artifact(
        tmp_path / "runtime_rules.json",
        rules=[
            make_rule(
                action="shell_command",
                effect="confirm",
                reason="Broken matcher metadata.",
                command_regexes=["("],
            )
        ],
    )
    service = RuntimeDecisionService(
        index_store=MockIndexStore([], exists=False),
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(RuntimeRulesArtifactInvalidError, match="invalid command regex"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Run tests.",
                cwd=tmp_path,
                command=["make", "test"],
            )
        )


def test_runtime_decision_service_requires_existing_index(tmp_path: Path) -> None:
    artifact_path = write_runtime_rules_artifact(tmp_path / "runtime_rules.json", rules=[])
    service = RuntimeDecisionService(
        index_store=MockIndexStore([], exists=False),
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(MissingIndexError, match="Run `policynim ingest`"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Run tests.",
                cwd=tmp_path,
                command=["make", "test"],
            )
        )


def test_runtime_decision_service_requires_non_empty_index(tmp_path: Path) -> None:
    artifact_path = write_runtime_rules_artifact(tmp_path / "runtime_rules.json", rules=[])
    service = RuntimeDecisionService(
        index_store=MockIndexStore([], exists=True),
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(MissingIndexError, match="Run `policynim ingest`"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Run tests.",
                cwd=tmp_path,
                command=["make", "test"],
            )
        )


def test_runtime_decision_service_rejects_malformed_chunk_line_spans(tmp_path: Path) -> None:
    artifact_path = write_runtime_rules_artifact(
        tmp_path / "runtime_rules.json",
        rules=[
            make_rule(
                action="shell_command",
                effect="confirm",
                reason="Review deploy commands.",
                command_regexes=["^deploy:"],
            )
        ],
    )
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk(lines="bad-span")]),
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(RuntimeCitationLinkError, match="invalid line span"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Deploy staging stack.",
                cwd=tmp_path,
                command=["deploy:staging"],
            )
        )


def test_runtime_decision_service_rejects_matched_rules_without_overlapping_chunks(
    tmp_path: Path,
) -> None:
    artifact_path = write_runtime_rules_artifact(
        tmp_path / "runtime_rules.json",
        rules=[
            make_rule(
                action="shell_command",
                effect="confirm",
                reason="Review deploy commands.",
                command_regexes=["^deploy:"],
                start_line=20,
                end_line=30,
            )
        ],
    )
    service = RuntimeDecisionService(
        index_store=MockIndexStore([make_chunk(lines="1-4")]),
        runtime_rules_artifact_path=artifact_path,
    )

    with pytest.raises(RuntimeCitationLinkError, match="could not be linked"):
        service.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Deploy staging stack.",
                cwd=tmp_path,
                command=["deploy:staging"],
            )
        )


def write_runtime_rules_artifact(path: Path, *, rules: list[CompiledRuntimeRule]) -> Path:
    """Persist one runtime rules artifact for tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = RuntimeRulesArtifact(rules=rules)
    path.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2), encoding="utf-8")
    return path


def make_rule(
    *,
    action: RuntimeActionKind,
    effect: RuntimeRuleEffect,
    reason: str,
    path_globs: list[str] | None = None,
    command_regexes: list[str] | None = None,
    url_host_patterns: list[str] | None = None,
    start_line: int = 6,
    end_line: int = 10,
) -> CompiledRuntimeRule:
    """Return one compiled runtime rule fixture."""
    return CompiledRuntimeRule(
        policy_id="BACKEND-RUNTIME-001",
        title="Runtime Rules",
        domain="backend",
        source_path="policies/backend/runtime.md",
        action=action,
        effect=effect,
        reason=reason,
        path_globs=path_globs or [],
        command_regexes=command_regexes or [],
        url_host_patterns=url_host_patterns or [],
        start_line=start_line,
        end_line=end_line,
    )


def make_chunk(
    *,
    chunk_id: str = "BACKEND-1",
    lines: str = "6-10",
    path: str = "policies/backend/runtime.md",
    policy_id: str = "BACKEND-RUNTIME-001",
) -> PolicyChunk:
    """Return one indexed chunk fixture."""
    return PolicyChunk(
        chunk_id=chunk_id,
        path=path,
        section="Runtime Rules",
        lines=lines,
        text="Keep runtime actions bounded.",
        policy=PolicyMetadata(
            policy_id=policy_id,
            title="Runtime Rules",
            doc_type="guidance",
            domain="backend",
        ),
    )
