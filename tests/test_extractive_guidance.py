"""Full pinned-source boundaries for compiler and preflight authority."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from test_ingest_service import MockEmbedder
from test_policy_generations import seed_legacy

from policynim.contracts import PolicyCompiler
from policynim.services.compiler import PolicyCompilerService
from policynim.services.generations import GenerationStore
from policynim.services.router import PolicyRouterService
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import (
    CompileRequest,
    GeneratedCompiledPolicyDraft,
    GeneratedPolicyConstraint,
    PolicySelectionPacket,
    ScoredChunk,
)


class PreserveRanking:
    """Keep actual SQLite candidates without inventing source fields."""

    def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, top_k: int
    ) -> list[ScoredChunk]:
        """Return the existing canonical candidates in discovery order."""
        return [chunk.model_copy(update={"score": 1.0}) for chunk in candidates[:top_k]]


class UnsupportedCompiler:
    """Attach known citations to an unsupported authoritative statement."""

    def __init__(self, statement: str) -> None:
        """Retain the observed failure class without an external provider call."""
        self.statement = statement

    def compile_policy_packet(
        self,
        request: CompileRequest,
        selection_packet: PolicySelectionPacket,
        context: Sequence[ScoredChunk],
    ) -> GeneratedCompiledPolicyDraft:
        """Return a typed draft with valid owner evidence and invented wording."""
        return GeneratedCompiledPolicyDraft(
            required_steps=[
                GeneratedPolicyConstraint(
                    statement=self.statement,
                    citation_ids=[context[0].chunk_id],
                )
            ]
        )


@pytest.mark.parametrize(
    "statement",
    [
        "Process 50-100 tokens per batch.",
        "Use context.Context with Done channel.",
        "Use structured JSON logs only.",
        "Terminate predecessor refresh tokens on every batch completion.",
    ],
)
def test_public_compiler_rejects_unsupported_statements_with_real_citations(
    tmp_path: Path, statement: str
) -> None:
    """A pinned generation and valid citations can't authorize invented requirements."""
    settings, identity, _, _ = seed_legacy(tmp_path)
    generations = GenerationStore(root=tmp_path / "generations", embedding=identity)
    generations.build(settings, MockEmbedder())
    snapshot = generations.pin()
    index = SQLiteVecIndexStore(
        path=snapshot.index_path, embedding_identity=identity, generation_snapshot=snapshot
    )
    with PolicyCompilerService(
        router=PolicyRouterService(
            embedder=MockEmbedder(), index_store=index, reranker=PreserveRanking()
        ),
        compiler=UnsupportedCompiler(statement),
    ) as service:
        result = service.compile(CompileRequest(task="Implement refresh cleanup", top_k=2))
    assert result.packet.insufficient_context
    assert result.retained_context
    assert not result.packet.required_steps


class ExactCompiler:
    """Select every supplied full source without synthesizing wording."""

    def compile_policy_packet(
        self,
        request: CompileRequest,
        selection_packet: PolicySelectionPacket,
        context: Sequence[ScoredChunk],
    ) -> GeneratedCompiledPolicyDraft:
        """Exercise the provider-facing typed context through the ordinary service API."""
        from policynim.source_units import SourceContext, SourceSelection

        assert isinstance(context, SourceContext)
        return context.catalog.compiler_draft(
            SourceSelection(
                source_ids=[unit.source_id for unit in context.catalog.units],
                insufficient_context=False,
            )
        )


def pinned_compiler(
    tmp_path: Path,
    *,
    custom_headings: bool = False,
    ids: list[str] | None = None,
    compiler: PolicyCompiler | None = None,
) -> PolicyCompilerService:
    """Build real owned source/index state and return its lease-owning compiler service."""
    settings, identity, _, _ = seed_legacy(tmp_path, ids=ids or ["A"])
    assert settings.corpus_dir is not None
    source = settings.corpus_dir / "A.md"
    if custom_headings:
        source.write_text(
            source.read_text()
            + (
                "\n## Private lifecycle conditions\n"
                "Replace the login session token after authentication or re-authentication "
                "and terminate its predecessor.\n"
                "\n## Exceptions that must travel with the rule\n"
                "Don't apply this lifecycle rule to periodic cleanup batches.\n"
            )
        )
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.build(settings, MockEmbedder())
    snapshot = store.pin()
    return PolicyCompilerService(
        router=PolicyRouterService(
            embedder=MockEmbedder(),
            reranker=PreserveRanking(),
            index_store=SQLiteVecIndexStore(
                path=snapshot.index_path, embedding_identity=identity, generation_snapshot=snapshot
            ),
        ),
        compiler=compiler or ExactCompiler(),
    )


class SourceGenerator:
    """Return exact source output or a typed unsupported mutation."""

    def __init__(self, field: str | None = None) -> None:
        """Choose one output boundary to corrupt after valid source selection."""
        self.field = field
        self.calls = 0

    def generate_preflight(
        self, request, context, *, compiled_packet=None, regeneration_context=None
    ):
        """Preserve the source wrapper and simulate a custom provider's free-text response."""
        from policynim.source_units import SourceContext, SourceSelection

        self.calls += 1
        assert isinstance(context, SourceContext)
        draft = context.catalog.preflight_draft(
            SourceSelection(
                source_ids=[unit.source_id for unit in context.catalog.units],
                insufficient_context=False,
            )
        )
        if self.field == "rationale":
            draft.applicable_policies[0].rationale = "Use context.Context with Done channel."
        elif self.field == "duplicate_reference":
            draft.citation_ids *= 2
        elif self.field == "duplicate_body":
            draft.plan_steps *= 2
        elif self.field == "summary":
            draft.summary = "Terminate predecessor refresh tokens on every batch completion."
        elif self.field is not None:
            draft = draft.model_copy(update={self.field: ["Process 50-100 tokens per batch."]})
        return draft


@pytest.mark.parametrize(
    "field",
    [
        "summary",
        "rationale",
        "plan_steps",
        "implementation_guidance",
        "review_flags",
        "tests_required",
        "duplicate_reference",
        "duplicate_body",
    ],
)
def test_public_preflight_rejects_custom_provider_inventions_in_every_field(
    tmp_path: Path, field: str
) -> None:
    """Canonical metadata and citations don't exempt any authoritative free-text field."""
    from policynim.services.preflight import PreflightService
    from policynim.types import PreflightRequest

    generator = SourceGenerator(field)
    with PreflightService(
        compiler_service=pinned_compiler(tmp_path), generator=generator
    ) as service:
        result = service.preflight(PreflightRequest(task="Implement refresh cleanup", top_k=2))
    assert generator.calls == 1
    assert result.insufficient_context
    assert not result.plan_steps


@pytest.mark.parametrize("custom_headings", [False, True])
def test_whole_source_conditions_are_retained_once_without_heading_assumptions(
    tmp_path: Path, custom_headings: bool
) -> None:
    """Whole source output keeps custom conditions and exceptions without generated authority."""
    from policynim.services.preflight import PreflightService
    from policynim.types import PreflightRequest

    generator = SourceGenerator()
    with PreflightService(
        compiler_service=pinned_compiler(tmp_path, custom_headings=custom_headings),
        generator=generator,
    ) as service:
        trace = service.preflight_with_trace(
            PreflightRequest(task="Implement refresh cleanup", top_k=2)
        )
    assert not trace.result.insufficient_context
    assert len(trace.result.plan_steps) == len(trace.result.applicable_policies) == 1
    assert trace.result.plan_steps == [
        constraint.statement for constraint in trace.compiled_packet.required_steps
    ]
    assert not trace.result.implementation_guidance
    assert not trace.result.review_flags
    assert not trace.result.tests_required
    if custom_headings:
        body = trace.result.plan_steps[0]
        assert "after authentication or re-authentication" in body
        assert "Don't apply this lifecycle rule to periodic cleanup batches." in body
        assert body.count("## Private lifecycle conditions") == 1


def test_actual_sdk_uses_compact_source_ids_and_materializes_full_bodies(tmp_path: Path) -> None:
    """Inspect real SDK request serialization without generating repeated policy bodies."""
    import json
    from contextlib import closing

    import httpx
    from openai import OpenAI

    from policynim.nvidia_models import NVIDIA_LIGHTNING_CHAT_MODEL
    from policynim.providers.nvidia import NVIDIAGenerator, NVIDIAPolicyCompiler
    from policynim.source_units import SourceContext
    from policynim.types import PreflightRequest

    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Capture the exact JSON wire and return only its allowed compact selection."""
        payload = json.loads(request.content)
        requests.append(payload)
        schema = payload["response_format"]["json_schema"]["schema"]
        source_ids = schema["properties"]["source_ids"]["items"]["enum"]
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "chat.completion",
                "created": 0,
                "model": NVIDIA_LIGHTNING_CHAT_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {"source_ids": source_ids, "insufficient_context": False}
                            ),
                        },
                    }
                ],
            },
        )

    with pinned_compiler(tmp_path, custom_headings=True) as service:
        compiled = service.compile(CompileRequest(task="Review cleanup", top_k=2))
        catalog = service.source_catalog(compiled.retained_context)
        context = SourceContext(tuple(compiled.retained_context), catalog)
        with closing(
            OpenAI(
                api_key="fixture",
                base_url="https://example.invalid/v1",
                max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(respond)),
            )
        ) as client:
            compiler = NVIDIAPolicyCompiler(
                api_key="fixture",
                model=NVIDIA_LIGHTNING_CHAT_MODEL,
                base_url="https://example.invalid/v1",
                timeout_seconds=1,
                max_retries=0,
                client=client,
            )
            generator = NVIDIAGenerator(
                api_key="fixture",
                model=NVIDIA_LIGHTNING_CHAT_MODEL,
                base_url="https://example.invalid/v1",
                timeout_seconds=1,
                max_retries=0,
                client=client,
            )
            draft = compiler.compile_policy_packet(
                CompileRequest(task="Review cleanup"),
                PolicySelectionPacket(task="Review cleanup", top_k=2, task_type="unknown"),
                context,
            )
            generated = generator.generate_preflight(
                PreflightRequest(task="Review cleanup"), context, compiled_packet=compiled.packet
            )
    assert catalog.accepts_compiler(draft)
    assert catalog.accepts_preflight(generated)
    assert len(requests) == 2
    for request in requests:
        schema_text = json.dumps(request["response_format"])
        assert "statement" not in schema_text and "rationale" not in schema_text
        assert catalog.units[0].body not in schema_text
        sources = json.loads(request["messages"][1]["content"])["sources"]
        assert [source["body"] for source in sources] == [catalog.units[0].body]
        assert request["temperature"] == 0
        assert request["chat_template_kwargs"] == {"enable_thinking": False}


def test_compact_selector_rejects_unknown_repeated_and_legacy_prose() -> None:
    """Internal grammar and local validation exclude alternate free-text response shapes."""
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError

    from policynim.source_units import SourceCatalog, SourceSelection, SourceUnit

    catalog = SourceCatalog((SourceUnit("s1", "P", "Policy", "Full source.", ("P:rule",)),))
    validator = Draft202012Validator(catalog.selection_schema())
    for invalid in [
        {"source_ids": ["unknown"], "insufficient_context": False},
        {"summary": "invented", "source_ids": ["s1"], "insufficient_context": False},
    ]:
        assert list(validator.iter_errors(invalid))
    with pytest.raises(ValidationError):
        SourceSelection.model_validate({"summary": "invented"})
    for ids in [["unknown"], ["s1", "s1"], []]:
        with pytest.raises(ValueError):
            catalog.compiler_draft(SourceSelection(source_ids=ids, insufficient_context=False))


def test_incomplete_chunks_cant_be_reassembled_as_whole_source() -> None:
    """Known full documents still require their entire canonical chunk inventory."""
    from policynim.ingest.chunking import chunk_policy_documents
    from policynim.source_units import SourceCatalog
    from policynim.types import ParsedDocument, PolicyMetadata

    document = ParsedDocument(
        source_path="private.md",
        metadata=PolicyMetadata(
            policy_id="P", title="Private", domain="security", doc_type="guidance"
        ),
        body=(
            "# Private\n\n## Rule\nApply only after authentication.\n\n"
            "## Exception\nKeep the preceding exception.\n"
        ),
    )
    chunks = [ScoredChunk(**chunk.model_dump()) for chunk in chunk_policy_documents([document])]
    assert SourceCatalog.from_documents([document], chunks).units
    assert not SourceCatalog.from_documents([document], chunks[:-1]).units
    assert not SourceCatalog.from_documents([], chunks).units
    assert not SourceCatalog.from_documents([document], [*chunks, chunks[0]]).units


def test_source_catalog_rejects_oversized_authority_before_dispatch() -> None:
    """Explicit composition can't bypass the same operational source budget."""
    from policynim.errors import PolicyContextError
    from policynim.source_units import MAX_SOURCE_BYTES, SourceCatalog, SourceUnit

    with pytest.raises(PolicyContextError):
        SourceCatalog((SourceUnit("s1", "P", "Policy", "x" * MAX_SOURCE_BYTES, ("P:rule",)),))


def test_sdk_rejects_lost_source_wrapper_before_http(tmp_path: Path) -> None:
    """A copied plain chunk list can't silently regain complete policy authority."""
    from test_nvidia_generator import MockOpenAIClient

    from policynim.errors import ProviderError
    from policynim.nvidia_models import NVIDIA_LIGHTNING_CHAT_MODEL
    from policynim.operations import OperationBudget, operation_scope
    from policynim.providers.nvidia import NVIDIAGenerator
    from policynim.types import PreflightRequest

    budget = OperationBudget(max_provider_calls=1)
    client = MockOpenAIClient('{"source_ids": ["s1"], "insufficient_context": false}')
    with pinned_compiler(tmp_path) as service:
        compiled = service.compile(CompileRequest(task="Review cleanup", top_k=2))
        generator = NVIDIAGenerator(
            api_key="fixture",
            model=NVIDIA_LIGHTNING_CHAT_MODEL,
            base_url="https://example.invalid/v1",
            timeout_seconds=1,
            max_retries=0,
            client=client,
        )
        with (
            operation_scope(budget),
            pytest.raises(ProviderError, match="Complete pinned source ownership"),
        ):
            generator.generate_preflight(PreflightRequest(task="Review"), compiled.retained_context)
    assert not client.chat.completions.calls
    assert budget.provider_attempts == 0
    assert budget.unavailable_usage_attempts == 0
    assert budget.measured_tokens == 0


def test_pinned_context_mutation_is_rejected_before_sdk_dispatch(tmp_path: Path) -> None:
    """Immutable source fingerprints catch changed coordinates even when IDs still match."""
    from test_nvidia_generator import MockOpenAIClient

    from policynim.errors import PolicyContextError
    from policynim.nvidia_models import NVIDIA_LIGHTNING_CHAT_MODEL
    from policynim.providers.nvidia import NVIDIAGenerator
    from policynim.source_units import SourceContext
    from policynim.types import PreflightRequest

    client = MockOpenAIClient('{"source_ids":["s1"],"insufficient_context":false}')
    with pinned_compiler(tmp_path) as service:
        compiled = service.compile(CompileRequest(task="Review cleanup", top_k=2))
        context = SourceContext(
            tuple(compiled.retained_context), service.source_catalog(compiled.retained_context)
        )
        context.chunks[0].lines = "9999-9999"
        generator = NVIDIAGenerator(
            api_key="fixture",
            model=NVIDIA_LIGHTNING_CHAT_MODEL,
            base_url="https://example.invalid/v1",
            timeout_seconds=1,
            max_retries=0,
            client=client,
        )
        with pytest.raises(PolicyContextError, match="doesn't match"):
            generator.generate_preflight(PreflightRequest(task="Review"), context)
    assert not client.chat.completions.calls


def test_public_result_budget_refuses_whole_output_without_echoing_task(tmp_path: Path) -> None:
    """The service's output ceiling includes caller intent and returns actionable fixed text."""
    from policynim.errors import PolicyContextError
    from policynim.source_units import MAX_OUTPUT_BYTES

    task = "sensitive-task-" + "x" * MAX_OUTPUT_BYTES
    with pinned_compiler(tmp_path) as service:
        with pytest.raises(PolicyContextError) as caught:
            service.compile(CompileRequest(task=task, top_k=2))
    assert caught.value.failure_class == "policy_output_budget_exceeded"
    assert "1-MiB" in str(caught.value)
    assert "sensitive-task" not in str(caught.value)


def test_refused_custom_provider_references_never_escape_in_public_trace(tmp_path: Path) -> None:
    """Rejected reference text mustn't bypass source limits through generation diagnostics."""
    from policynim.services.preflight import PreflightService
    from policynim.source_units import MAX_SOURCE_BYTES, SourceContext, SourceSelection
    from policynim.types import GeneratedPreflightDraft, PreflightRequest

    class InvalidReferenceGenerator:
        """Return otherwise exact source output with an oversized invented reference."""

        def generate_preflight(
            self, request: PreflightRequest, context: Sequence[ScoredChunk], **kwargs: object
        ) -> GeneratedPreflightDraft:
            """Keep source bodies intact while testing the refused diagnostic boundary."""
            assert isinstance(context, SourceContext)
            draft = context.catalog.preflight_draft(
                SourceSelection(
                    source_ids=[unit.source_id for unit in context.catalog.units],
                    insufficient_context=False,
                )
            )
            draft.citation_ids = ["private-reference-" + "x" * MAX_SOURCE_BYTES]
            return draft

    with PreflightService(
        compiler_service=pinned_compiler(tmp_path), generator=InvalidReferenceGenerator()
    ) as service:
        traced = service.preflight_with_trace(PreflightRequest(task="Review cleanup", top_k=2))
    assert traced.result.insufficient_context
    assert "private-reference-" not in traced.model_dump_json()
    generation = next(step for step in traced.trace_steps if step.kind == "grounded_generation")
    assert generation.citation_ids == []


def test_public_preflight_preserves_identical_bodies_for_distinct_source_owners(
    tmp_path: Path,
) -> None:
    """Whole-body identity doesn't erase separate policies or their canonical references."""
    from policynim.services.preflight import PreflightService
    from policynim.types import PreflightRequest

    settings, identity, _, _ = seed_legacy(tmp_path, ids=["A", "B"])
    assert settings.corpus_dir is not None
    shared = "# Shared\n\n## Rule\nKeep every condition and its exception together.\n"
    for source in settings.corpus_dir.glob("*.md"):
        frontmatter = source.read_text().split("---", 2)[1]
        source.write_text("---" + frontmatter + "---\n" + shared)
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.build(settings, MockEmbedder())
    snapshot = store.pin()
    compiler = PolicyCompilerService(
        router=PolicyRouterService(
            embedder=MockEmbedder(),
            reranker=PreserveRanking(),
            index_store=SQLiteVecIndexStore(
                path=snapshot.index_path, embedding_identity=identity, generation_snapshot=snapshot
            ),
        ),
        compiler=ExactCompiler(),
    )
    with PreflightService(compiler_service=compiler, generator=SourceGenerator()) as service:
        result = service.preflight(PreflightRequest(task="Review shared rules", top_k=2))
    assert not result.insufficient_context
    assert {guidance.policy_id for guidance in result.applicable_policies} == {"A", "B"}
    assert result.plan_steps == [snapshot.documents[0].body, snapshot.documents[1].body]
    assert result.plan_steps[0] == result.plan_steps[1]
    assert {citation.policy_id for citation in result.citations} == {"A", "B"}


def test_near_limit_source_catalog_uses_separate_materialized_output_budget() -> None:
    """Valid source input may add canonical metadata and repeat references in its output."""
    from policynim.source_units import (
        MAX_OUTPUT_BYTES,
        MAX_SOURCE_BYTES,
        SourceCatalog,
        SourceSelection,
        SourceUnit,
    )

    references = tuple(f"P:rule-{index}-" + "x" * 100 for index in range(80))
    small = SourceCatalog((SourceUnit("s1", "P", "Policy", "x", references),))
    overhead = len(small.prompt_payload().encode()) - 1
    catalog = SourceCatalog(
        (SourceUnit("s1", "P", "Policy", "x" * (MAX_SOURCE_BYTES - overhead), references),)
    )
    draft = catalog.preflight_draft(SourceSelection(source_ids=["s1"], insufficient_context=False))
    assert len(catalog.prompt_payload().encode()) == MAX_SOURCE_BYTES
    assert MAX_SOURCE_BYTES < len(draft.model_dump_json().encode()) < MAX_OUTPUT_BYTES
    assert catalog.accepts_preflight(draft)


@pytest.mark.parametrize("guardrails_enabled", [False, True])
def test_extractive_regeneration_stops_without_redundant_sdk_or_judge_calls(
    tmp_path: Path, guardrails_enabled: bool
) -> None:
    """An explicit extractive adapter can't revise a failed exact source set on retry."""
    import json

    import httpx
    from openai import OpenAI
    from test_policy_regeneration_service import (
        MockConformanceService,
        PassingRails,
        make_conformance_result,
    )

    from policynim.nvidia_models import NVIDIA_LIGHTNING_CHAT_MODEL
    from policynim.operations import OperationBudget, operation_scope
    from policynim.providers.nvidia import NVIDIAGenerator
    from policynim.providers.nvidia_guardrails import NeMoGuardrailsPreflightGenerator
    from policynim.services.regeneration import PolicyRegenerationService
    from policynim.types import PolicyConformanceMetric, PreflightRegenerationRequest

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Count actual SDK transport dispatch and return one bounded source selection."""
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "controlled-selection",
                "object": "chat.completion",
                "created": 0,
                "model": NVIDIA_LIGHTNING_CHAT_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {"source_ids": ["s1"], "insufficient_context": False}
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    client = OpenAI(
        api_key="fixture", base_url="https://example.invalid/v1", http_client=http_client
    )
    generator = NVIDIAGenerator(
        api_key="fixture",
        model=NVIDIA_LIGHTNING_CHAT_MODEL,
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=client,
    )
    judge = MockConformanceService(
        [
            make_conformance_result(
                passed=False,
                metrics=[
                    PolicyConformanceMetric(
                        name="plan_completeness",
                        score=0.0,
                        passed=False,
                        failure_reasons=["An applicability review is needed."],
                    )
                ],
            ),
            make_conformance_result(passed=True),
        ]
    )
    active_generator = (
        NeMoGuardrailsPreflightGenerator(base_generator=generator, rails=PassingRails())
        if guardrails_enabled
        else generator
    )
    budget = OperationBudget(max_provider_calls=4)
    try:
        with (
            PolicyRegenerationService(
                compiler_service=pinned_compiler(tmp_path),
                generator=active_generator,
                conformance_service=judge,
            ) as service,
            operation_scope(budget),
        ):
            result = service.regenerate(
                PreflightRegenerationRequest(task="Review cleanup", top_k=2, max_regenerations=3)
            )
    finally:
        client.close()
    assert not result.passed and result.stop_reason == "no_material_trigger"
    assert len(result.attempts) == len(requests) == len(judge.requests) == 1
    assert budget.provider_attempts == 1 and budget.measured_tokens == 10
    assert budget.unavailable_usage_attempts == 0


def test_reversed_custom_compiler_order_keeps_canonical_preflight_sources(tmp_path: Path) -> None:
    """Source selection is a set even when a valid compiler lists its owners in reverse."""
    from test_nvidia_generator import MockOpenAIClient

    from policynim.nvidia_models import NVIDIA_LIGHTNING_CHAT_MODEL
    from policynim.providers.nvidia import NVIDIAGenerator
    from policynim.services.preflight import PreflightService
    from policynim.types import PreflightRequest

    class ReversedCompiler(ExactCompiler):
        """Return only exact complete sources, using a different permitted constraint order."""

        def compile_policy_packet(
            self,
            request: CompileRequest,
            selection_packet: PolicySelectionPacket,
            context: Sequence[ScoredChunk],
        ) -> GeneratedCompiledPolicyDraft:
            """Retain source identity and references while reversing the compiler's list."""
            draft = super().compile_policy_packet(request, selection_packet, context)
            draft.required_steps.reverse()
            return draft

    client = MockOpenAIClient('{"source_ids":["s1","s2"],"insufficient_context":false}')
    generator = NVIDIAGenerator(
        api_key="fixture",
        model=NVIDIA_LIGHTNING_CHAT_MODEL,
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=client,
    )
    with PreflightService(
        compiler_service=pinned_compiler(tmp_path, ids=["A", "B"], compiler=ReversedCompiler()),
        generator=generator,
    ) as service:
        result = service.preflight(PreflightRequest(task="Review both policies", top_k=2))
    assert not result.insufficient_context
    assert [owner.policy_id for owner in result.applicable_policies] == ["A", "B"]
    assert len(client.chat.completions.calls) == 1


def test_public_compiler_accepts_router_budget_boundary_with_same_json_encoding(
    tmp_path: Path,
) -> None:
    """Canonical source binding mustn't reject bytes that fit the router's context limit."""
    import json

    from policynim.ingest.chunking import chunk_policy_documents
    from policynim.source_units import MAX_SOURCE_BYTES

    settings, identity, _, _ = seed_legacy(tmp_path, ids=["A"])
    assert settings.corpus_dir is not None
    source = settings.corpus_dir / "A.md"
    frontmatter = source.read_text().split("---", 2)[1]
    source.write_text("---" + frontmatter + "---\nx")
    store = GenerationStore(root=tmp_path / "generations", embedding=identity)
    store.build(settings, MockEmbedder())
    with store.pin() as small:
        context = [
            ScoredChunk(**chunk.model_dump(), score=1.0)
            for chunk in chunk_policy_documents(small.documents)
        ]
    assert len(context) == 1
    compact_overhead = (
        len(
            json.dumps(
                [c.model_dump(mode="json") for c in context],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
        )
        - 1
    )
    source.write_text("---" + frontmatter + "---\n" + "x" * (MAX_SOURCE_BYTES - compact_overhead))
    store.build(settings, MockEmbedder())
    snapshot = store.pin()
    expected = [
        ScoredChunk(**chunk.model_dump(), score=1.0)
        for chunk in chunk_policy_documents(snapshot.documents)
    ]
    encoded = [chunk.model_dump(mode="json") for chunk in expected]
    assert (
        len(json.dumps(encoded, ensure_ascii=True, separators=(",", ":")).encode())
        <= MAX_SOURCE_BYTES
    )
    assert len(json.dumps(encoded, ensure_ascii=True).encode()) > MAX_SOURCE_BYTES
    with PolicyCompilerService(
        router=PolicyRouterService(
            embedder=MockEmbedder(),
            reranker=PreserveRanking(),
            index_store=SQLiteVecIndexStore(
                path=snapshot.index_path, embedding_identity=identity, generation_snapshot=snapshot
            ),
        ),
        compiler=ExactCompiler(),
    ) as service:
        result = service.compile(CompileRequest(task="Review the policy", top_k=1))
    assert not result.packet.insufficient_context
    assert len(result.packet.required_steps) == 1
