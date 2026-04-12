"""Tests for the policy compiler service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import pytest

from policynim.errors import MissingIndexError
from policynim.services.compiler import PolicyCompilerService
from policynim.types import (
    CompileRequest,
    GeneratedCompiledPolicyDraft,
    GeneratedPolicyConstraint,
    PolicyMetadata,
    PolicySelectionPacket,
    RouteResult,
    ScoredChunk,
    SelectedPolicy,
    SelectedPolicyEvidence,
)


class MockRouter:
    """Static router double for compiler tests."""

    def __init__(self, route_result: RouteResult) -> None:
        self._route_result = route_result
        self.last_request: object | None = None
        self.closed = False

    def route(self, request) -> RouteResult:
        self.last_request = request
        return self._route_result

    def close(self) -> None:
        self.closed = True


class MissingIndexRouter:
    """Router double that preserves missing-index failures."""

    def route(self, request) -> RouteResult:
        raise MissingIndexError("Run `policynim ingest` before compiling policy constraints.")

    def close(self) -> None:
        return None


class MockCompiler:
    """Static policy compiler double for compiler tests."""

    def __init__(self, draft: GeneratedCompiledPolicyDraft) -> None:
        self._draft = draft
        self.calls = 0
        self.closed = False

    def compile_policy_packet(
        self,
        request: CompileRequest,
        selection_packet: PolicySelectionPacket,
        context: Sequence[ScoredChunk],
    ) -> GeneratedCompiledPolicyDraft:
        self.calls += 1
        assert request.task == selection_packet.task
        assert context
        return self._draft

    def close(self) -> None:
        self.closed = True


def test_compiler_service_materializes_grounded_constraints_and_citations() -> None:
    route_result = make_route_result()
    compiler = MockCompiler(
        GeneratedCompiledPolicyDraft(
            required_steps=[
                GeneratedPolicyConstraint(
                    statement="Thread request ids through backend log context.",
                    citation_ids=["BACKEND-1"],
                )
            ],
            forbidden_patterns=[
                GeneratedPolicyConstraint(
                    statement="Do not log raw token values.",
                    citation_ids=["SECURITY-1"],
                )
            ],
            architectural_expectations=[
                GeneratedPolicyConstraint(
                    statement="Keep logging changes in the backend service layer.",
                    citation_ids=["BACKEND-1"],
                )
            ],
            test_expectations=[
                GeneratedPolicyConstraint(
                    statement="Add a regression test for token redaction.",
                    citation_ids=["SECURITY-1"],
                )
            ],
            style_constraints=[
                GeneratedPolicyConstraint(
                    statement="Use explicit request-id naming in log fields.",
                    citation_ids=["BACKEND-1"],
                )
            ],
        )
    )
    service = PolicyCompilerService(
        router=cast(Any, MockRouter(route_result)),
        compiler=compiler,
    )

    result = service.compile(CompileRequest(task="fix backend logging bug", top_k=2))

    assert compiler.calls == 1
    assert not result.packet.insufficient_context
    assert result.packet.task_type == "bug_fix"
    assert result.packet.required_steps[0].source_policy_ids == ["BACKEND-LOG-001"]
    assert result.packet.forbidden_patterns[0].source_policy_ids == ["SECURITY-TOKEN-001"]
    assert [citation.chunk_id for citation in result.packet.citations] == [
        "BACKEND-1",
        "SECURITY-1",
    ]
    assert [chunk.chunk_id for chunk in result.retained_context] == ["BACKEND-1", "SECURITY-1"]


def test_compiler_service_bypasses_provider_for_insufficient_route_context() -> None:
    route_result = make_route_result(insufficient_context=True, retained_context=[])
    compiler = MockCompiler(
        GeneratedCompiledPolicyDraft(
            required_steps=[
                GeneratedPolicyConstraint(statement="Should not run.", citation_ids=["BACKEND-1"])
            ]
        )
    )
    service = PolicyCompilerService(
        router=cast(Any, MockRouter(route_result)),
        compiler=compiler,
    )

    result = service.compile(CompileRequest(task="unknown task", top_k=1))

    assert compiler.calls == 0
    assert result.packet.insufficient_context
    assert result.packet.required_steps == []
    assert result.retained_context == []


def test_compiler_service_fails_closed_for_unknown_citation_ids() -> None:
    service = PolicyCompilerService(
        router=cast(Any, MockRouter(make_route_result())),
        compiler=MockCompiler(
            GeneratedCompiledPolicyDraft(
                required_steps=[
                    GeneratedPolicyConstraint(
                        statement="Unsupported citation should fail closed.",
                        citation_ids=["UNKNOWN"],
                    )
                ]
            )
        ),
    )

    result = service.compile(CompileRequest(task="fix backend logging bug", top_k=2))

    assert result.packet.insufficient_context
    assert result.packet.citations == []
    assert result.packet.required_steps == []


@pytest.mark.parametrize(
    "draft",
    [
        GeneratedCompiledPolicyDraft(
            required_steps=[GeneratedPolicyConstraint(statement="   ", citation_ids=["BACKEND-1"])]
        ),
        GeneratedCompiledPolicyDraft(
            required_steps=[
                GeneratedPolicyConstraint(statement="Missing citation.", citation_ids=[])
            ]
        ),
        GeneratedCompiledPolicyDraft(),
    ],
)
def test_compiler_service_fails_closed_for_empty_or_unsupported_constraints(
    draft: GeneratedCompiledPolicyDraft,
) -> None:
    service = PolicyCompilerService(
        router=cast(Any, MockRouter(make_route_result())),
        compiler=MockCompiler(draft),
    )

    result = service.compile(CompileRequest(task="fix backend logging bug", top_k=2))

    assert result.packet.insufficient_context
    assert result.packet.citations == []


def test_compiler_service_surfaces_missing_index_errors() -> None:
    service = PolicyCompilerService(
        router=cast(Any, MissingIndexRouter()),
        compiler=MockCompiler(GeneratedCompiledPolicyDraft()),
    )

    with pytest.raises(MissingIndexError, match="policynim ingest"):
        service.compile(CompileRequest(task="fix backend logging bug", top_k=2))


def test_compiler_service_close_closes_owned_components() -> None:
    router = MockRouter(make_route_result())
    compiler = MockCompiler(GeneratedCompiledPolicyDraft())
    service = PolicyCompilerService(router=cast(Any, router), compiler=compiler)

    service.close()

    assert router.closed is True
    assert compiler.closed is True


def make_route_result(
    *,
    insufficient_context: bool = False,
    retained_context: list[ScoredChunk] | None = None,
) -> RouteResult:
    backend = make_chunk(
        chunk_id="BACKEND-1",
        policy_id="BACKEND-LOG-001",
        title="Backend Logging",
        domain="backend",
        text="Use request ids in backend logs.",
    )
    security = make_chunk(
        chunk_id="SECURITY-1",
        policy_id="SECURITY-TOKEN-001",
        title="Token Handling",
        domain="security",
        text="Never log token values.",
    )
    selected_policies = [
        make_selected_policy(backend),
        make_selected_policy(security),
    ]
    selected_context = retained_context if retained_context is not None else [backend, security]
    if retained_context is None and insufficient_context:
        selected_context = []
    return RouteResult(
        packet=PolicySelectionPacket(
            task="fix backend logging bug",
            domain=None,
            top_k=2,
            task_type="bug_fix",
            explicit_task_type=None,
            profile_signals=["fix", "bug"],
            selected_policies=selected_policies,
            insufficient_context=insufficient_context,
        ),
        retained_context=selected_context,
    )


def make_selected_policy(chunk: ScoredChunk) -> SelectedPolicy:
    return SelectedPolicy(
        policy_id=chunk.policy.policy_id,
        title=chunk.policy.title,
        domain=chunk.policy.domain,
        reason="Selected for compiler tests.",
        evidence=[
            SelectedPolicyEvidence(
                chunk_id=chunk.chunk_id,
                path=chunk.path,
                section=chunk.section,
                lines=chunk.lines,
                text=chunk.text,
                score=chunk.score,
            )
        ],
    )


def make_chunk(
    *,
    chunk_id: str,
    policy_id: str,
    title: str,
    domain: str,
    text: str,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        path=f"policies/{domain}/policy.md",
        section="Rules",
        lines="1-4",
        text=text,
        policy=PolicyMetadata(
            policy_id=policy_id,
            title=title,
            doc_type="guidance",
            domain=domain,
        ),
        score=0.99,
    )
