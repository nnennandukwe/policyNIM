"""Integration proofs for immutable grounded MCP inspection and guidance."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from mcp import Client
from mcp.types import TextContent, TextResourceContents
from test_policy_generations import seed_legacy

from policynim.errors import GenerationError
from policynim.interfaces import grounded_mcp as registration
from policynim.interfaces import mcp as mcp_module
from policynim.services import grounded_mcp as service_module
from policynim.services.evidence_trace import compiled_policy_packet_id
from policynim.services.generations import GenerationStore
from policynim.services.grounded_mcp import (
    PolicyResourceError,
    chunk_resource_uri,
    read_chunk_resource,
    read_policy_catalog,
    read_policy_resource,
    resource_key,
)
from policynim.settings import Settings
from policynim.types import (
    Citation,
    CompiledPolicyPacket,
    CompileRequest,
    CompileResult,
    PolicyChunk,
    PolicyMetadata,
    PreflightEvidenceTraceResult,
    PreflightRequest,
    PreflightResult,
    PreflightTraceResult,
    ScoredChunk,
)


@pytest.fixture
def generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Publish a real two-policy generation without NVIDIA calls or personal settings."""
    import os

    for key in list(os.environ):
        if key.startswith("POLICYNIM_") or key == "NVIDIA_API_KEY":
            monkeypatch.delenv(key)
    settings, identity, index, rules = seed_legacy(tmp_path)
    settings = settings.model_copy(update={"generations_dir": tmp_path / "generations"})
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    published = store.migrate(settings, index_path=index, rules_path=rules)
    monkeypatch.setattr(registration, "get_settings", lambda: settings)
    return settings, store, published.generation_id


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
def test_public_catalog_resources_and_prompt_need_no_provider(
    generation, monkeypatch, mode
) -> None:
    """Read catalog, source body and chunk through the actual SDK resource path."""
    settings, store, generation_id = generation

    def fail_provider(*args, **kwargs):
        """Fail if read-only discovery starts any generation pipeline."""
        pytest.fail("resource discovery mustn't construct model services")

    monkeypatch.setattr(service_module, "create_policy_compiler_service", fail_provider)
    monkeypatch.setattr(service_module, "create_preflight_service", fail_provider)

    async def exercise() -> None:
        """Inspect resource templates and execute the public resource/prompt protocol."""
        server = mcp_module._register_tools(mcp_module._new_mcp_server())
        async with Client(server, mode=mode) as client:
            resources = await client.list_resources()
            assert [str(item.uri) for item in resources.resources] == ["policynim://catalog"]
            templates = await client.list_resource_templates()
            assert len(templates.resource_templates) == 3
            catalog_result = await client.read_resource("policynim://catalog")
            contents = catalog_result.contents[0]
            assert isinstance(contents, TextResourceContents)
            catalog = json.loads(contents.text)
            assert catalog["generation_id"] == generation_id
            historical = await client.read_resource(catalog["resource_uri"])
            assert (
                isinstance(historical.contents[0], TextResourceContents)
                and json.loads(historical.contents[0].text) == catalog
            )
            document = await client.read_resource(catalog["policy_resources"][0]["uri"])
            assert isinstance(document.contents[0], TextResourceContents)
            assert json.loads(document.contents[0].text)["document"]["metadata"]["policy_id"] == "A"
            from policynim.services.index_store import create_index_store

            index = create_index_store(settings)
            try:
                chunk = index.list_chunks()[0]
            finally:
                index.close()
            chunk_uri = chunk_resource_uri(generation_id, chunk.chunk_id)
            retrieved = await client.read_resource(chunk_uri)
            assert isinstance(retrieved.contents[0], TextResourceContents)
            assert json.loads(retrieved.contents[0].text)["chunk"] == chunk.model_dump(mode="json")
            prompts = await client.list_prompts()
            assert [item.name for item in prompts.prompts] == ["policy_guided_review"]
            prompt = await client.get_prompt(
                "policy_guided_review", {"task": "review token cleanup"}
            )
            assert isinstance(prompt.messages[0].content, TextContent)
            assert "doesn't authorize execution" in prompt.messages[0].content.text
            names = {item.name for item in (await client.list_tools()).tools}
            assert names == {
                "policy_search",
                "policy_preflight",
                "policy_compile",
                "policy_preflight_evidence",
            }

    asyncio.run(exercise())


def test_historical_source_never_switches_to_current(generation) -> None:
    """An explicit immutable URI still returns its original body after active changes."""
    settings, store, generation_id = generation
    original = read_policy_resource(
        settings, generation_id=generation_id, policy_key=resource_key("A")
    )
    source = Path(settings.corpus_dir) / "A.md"
    source.write_text(source.read_text() + "\nA changed retained-source example.\n")
    from test_ingest_service import MockEmbedder

    updated = store.build(settings, embedder=MockEmbedder())
    assert updated.generation_id != generation_id
    assert (
        read_policy_resource(settings, generation_id=generation_id, policy_key=resource_key("A"))
        == original
    )
    catalog = read_policy_catalog(settings)
    assert catalog["generation_id"] == updated.generation_id


@pytest.mark.parametrize(
    "key", ["../etc/passwd", "..", "%2e%2e", "A", "a===", "_w", "", "a" * 4097]
)
def test_malformed_logical_keys_are_rejected_before_lookup(generation, key: str) -> None:
    """Malformed and noncanonical keys can't select a source path."""
    settings, _, generation_id = generation
    with pytest.raises(PolicyResourceError, match="invalid_reference"):
        read_policy_resource(settings, generation_id=generation_id, policy_key=key)


def test_unknown_generation_and_out_of_corpus_ids_never_fall_back(generation) -> None:
    """Explicit missing identities remain missing even when an active catalog exists."""
    settings, _, generation_id = generation
    with pytest.raises(GenerationError):
        read_policy_catalog(settings, generation_id="f" * 64)
    with pytest.raises(PolicyResourceError, match="invalid_reference"):
        read_policy_catalog(settings, generation_id="../active")
    with pytest.raises(PolicyResourceError, match="unknown_policy"):
        read_policy_resource(
            settings, generation_id=generation_id, policy_key=resource_key("../A.md")
        )
    with pytest.raises(PolicyResourceError, match="unknown_chunk"):
        read_chunk_resource(
            settings, generation_id=generation_id, chunk_key=resource_key("missing")
        )


def traced_fixture(generation_id: str) -> PreflightTraceResult:
    """Describe one authoritative pipeline result at the service injection seam."""
    metadata = PolicyMetadata(
        policy_id="A", title="A policy", doc_type="guidance", domain="backend"
    )
    chunk = PolicyChunk(
        chunk_id="A:1",
        path="A.md",
        section="Review",
        lines="1-2",
        text="Review changes.",
        policy=metadata,
    )
    citation = Citation(
        policy_id="A", title="A policy", path="A.md", section="Review", lines="1-2", chunk_id="A:1"
    )
    packet = CompiledPolicyPacket(
        generation_id=generation_id,
        task="review",
        top_k=5,
        task_type="unknown",
        citations=[citation],
    )
    return PreflightTraceResult(
        result=PreflightResult(
            generation_id=generation_id,
            task="review",
            summary="Review changes.",
            citations=[citation],
        ),
        compiled_packet=packet,
        retained_context=[ScoredChunk(**chunk.model_dump(), score=1.0)],
    )


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
def test_typed_tools_trace_one_run_and_preserve_packet_identity(
    generation, monkeypatch, mode
) -> None:
    """Compilation and evidence use shared SDK tools; evidence never regenerates guidance."""
    _, _, generation_id = generation
    traced = traced_fixture(generation_id)
    calls: list[str] = []

    class Pipeline:
        """Expose only the supported preflight-with-trace service boundary."""

        def preflight_with_trace(self, request: PreflightRequest, *, principal=None):
            """Record the single generation invocation."""
            calls.append("preflight")
            return traced

        def compile(self, request: CompileRequest):
            """Return the exact compiler packet without another preflight run."""
            calls.append("compile")
            return CompileResult(
                generation_id=generation_id,
                packet=traced.compiled_packet,
                retained_context=traced.retained_context,
            )

    @contextmanager
    def factory(settings: Settings):
        """Own and close the fake service through the same public context contract."""
        try:
            yield Pipeline()
        finally:
            calls.append("closed")

    monkeypatch.setattr(service_module, "create_preflight_service", factory)
    monkeypatch.setattr(service_module, "create_policy_compiler_service", factory)

    async def exercise() -> None:
        """Invoke typed tools through client validation and MCP dispatch."""
        server = mcp_module._register_tools(mcp_module._new_mcp_server())
        async with Client(server, mode=mode) as client:
            compile_output = await client.call_tool("policy_compile", {"task": "review"})
            assert not compile_output.is_error
            compiled = CompileResult.model_validate(compile_output.structured_content)
            assert compiled.generation_id == generation_id
            assert compiled.packet.citations[0].resource_uri == chunk_resource_uri(
                generation_id, "A:1"
            )
            evidence_output = await client.call_tool(
                "policy_preflight_evidence", {"task": "review"}
            )
            assert not evidence_output.is_error
            result = PreflightEvidenceTraceResult.model_validate(evidence_output.structured_content)
            assert result.result.run_id
            assert result.result.run_id == result.evidence_trace.run_id
            assert result.evidence_trace.compiled_packet_id == compiled_policy_packet_id(
                traced.compiled_packet
            )
            assert (
                result.evidence_trace.generation_id == result.result.generation_id == generation_id
            )
            assert result.result.citations[0].resource_uri == chunk_resource_uri(
                generation_id, "A:1"
            )
        assert calls == ["compile", "closed", "preflight", "closed"]

    asyncio.run(exercise())


def test_resource_reads_fail_after_retention_purges_original(generation) -> None:
    """A purged URI fails explicitly after enough publications displace its protected slot."""
    from test_ingest_service import MockEmbedder

    settings, store, generation_id = generation
    source = Path(settings.corpus_dir) / "A.md"
    for revision in range(2):
        source.write_text(source.read_text() + f"\nGeneration revision {revision}.\n")
        store.build(settings, embedder=MockEmbedder())
    result = store.prune(keep_recent=0)
    assert generation_id in result.removed
    with pytest.raises(GenerationError):
        read_policy_resource(settings, generation_id=generation_id, policy_key=resource_key("A"))


def test_hosted_auth_is_required_before_grounded_service_allocation(
    generation, monkeypatch
) -> None:
    """No grounded tool or resource service starts without a hosted principal."""
    import policynim.interfaces.grounded_mcp as interface

    def fail_allocation(*args, **kwargs):
        """Catch any service access before the principal gate."""
        pytest.fail("unauthenticated calls mustn't allocate services")

    monkeypatch.setattr(interface, "compile_policy", fail_allocation)
    monkeypatch.setattr(interface, "preflight_evidence", fail_allocation)
    monkeypatch.setattr(interface, "read_policy_catalog", fail_allocation)

    async def exercise() -> None:
        """Dispatch unauthorized modern calls and inspect protocol errors."""
        server = mcp_module._register_tools(mcp_module._new_mcp_server(), require_principal=True)
        async with Client(server) as client:
            for name in ("policy_compile", "policy_preflight_evidence"):
                result = await client.call_tool(name, {"task": "review"})
                assert result.is_error
                assert isinstance(result.content[0], TextContent)
                assert "invalid_token" in result.content[0].text
            with pytest.raises(Exception, match="invalid_token"):
                await client.read_resource("policynim://catalog")

    asyncio.run(exercise())


def test_compile_and_evidence_share_existing_operation_capacity(generation, monkeypatch) -> None:
    """Saturated guidance calls never construct additional provider services."""
    from threading import Event

    settings, _, _ = generation
    started, release = Event(), Event()

    def fail_allocation(*args, **kwargs):
        """Catch an incorrectly admitted grounded service construction."""
        pytest.fail("saturated grounded calls mustn't construct provider services")

    monkeypatch.setattr(service_module, "create_policy_compiler_service", fail_allocation)
    monkeypatch.setattr(service_module, "create_preflight_service", fail_allocation)

    def occupied_operation() -> None:
        """Hold an existing admitted operation until the rejection checks finish."""
        started.set()
        assert release.wait(5)

    async def exercise() -> None:
        """Keep one shared runtime slot occupied while dispatching both new tools."""
        runtime = mcp_module._ToolExecutor(capacity=1, settings=settings)
        server = mcp_module._register_tools(mcp_module._new_mcp_server(runtime))
        async with Client(server) as client:
            occupied = asyncio.create_task(runtime.run(occupied_operation))
            try:
                async with asyncio.timeout(5):
                    while not started.is_set():
                        await asyncio.sleep(0.001)
                for name in ("policy_compile", "policy_preflight_evidence"):
                    result = await client.call_tool(name, {"task": "review"})
                    assert result.is_error
                    assert isinstance(result.content[0], TextContent)
                    assert "server_busy" in result.content[0].text
            finally:
                release.set()
                await occupied

    asyncio.run(exercise())


def test_read_only_principal_cannot_start_compile_or_evidence(generation, monkeypatch) -> None:
    """Hosted read permission remains sufficient only for catalog and source inspection."""
    from policynim.services.authorization import AuthenticatedPrincipal

    principal = AuthenticatedPrincipal(
        issuer="https://issuer.example",
        subject="reader",
        client_id="fixture",
        scopes=frozenset({"policies:read"}),
        source="oauth",
    )

    def fail_allocation(*args, **kwargs):
        """Catch any provider activity after an insufficient-scope principal."""
        pytest.fail("read-only callers mustn't start expensive guidance work")

    monkeypatch.setattr(registration, "compile_policy", fail_allocation)
    monkeypatch.setattr(registration, "preflight_evidence", fail_allocation)

    async def exercise() -> None:
        """Use the real principal resolver around SDK-dispatched calls."""
        token = mcp_module._HOSTED_PRINCIPAL.set(principal)
        try:
            server = mcp_module._register_tools(
                mcp_module._new_mcp_server(), require_principal=True
            )
            async with Client(server) as client:
                catalog = await client.read_resource("policynim://catalog")
                assert isinstance(catalog.contents[0], TextResourceContents)
                for name in ("policy_compile", "policy_preflight_evidence"):
                    output = await client.call_tool(name, {"task": "review"})
                    assert output.is_error
                    assert isinstance(output.content[0], TextContent)
                    assert "insufficient_scope" in output.content[0].text
        finally:
            mcp_module._HOSTED_PRINCIPAL.reset(token)

    asyncio.run(exercise())


def test_long_valid_heading_keeps_a_bounded_readable_citation_uri(generation) -> None:
    """Qodo e07136e2 regression: heading length can't create an unreadable resource key."""
    from contextlib import closing

    from test_ingest_service import MockEmbedder

    from policynim.services.index_store import create_index_store

    settings, store, _ = generation
    source = Path(settings.corpus_dir) / "A.md"
    source.write_text(source.read_text() + "\n## " + "heading" * 1000 + "\nReview this change.\n")
    published = store.build(settings, embedder=MockEmbedder())
    with closing(create_index_store(settings)) as index:
        chunk = max(index.list_chunks(), key=lambda item: len(item.chunk_id))
    assert len(chunk.chunk_id) > 4096
    uri = chunk_resource_uri(published.generation_id, chunk.chunk_id)
    assert len(uri.rsplit("/", 1)[1]) == 64
    result = read_chunk_resource(
        settings, generation_id=published.generation_id, chunk_key=resource_key(chunk.chunk_id)
    )
    assert result["chunk"] == chunk.model_dump(mode="json")


def test_resource_presentation_links_preserve_existing_packet_fingerprints() -> None:
    """Qodo regression: additive citation metadata can't change an unchanged compiled packet ID."""
    import hashlib

    traced = traced_fixture("a" * 64)
    packet = traced.compiled_packet
    legacy_payload = packet.model_dump(mode="json")
    for citation in legacy_payload["citations"]:
        citation.pop("resource_uri")
    legacy_digest = hashlib.sha256(
        json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert compiled_policy_packet_id(packet) == legacy_digest
    linked = packet.model_copy(
        update={
            "citations": [
                item.model_copy(
                    update={"resource_uri": chunk_resource_uri("a" * 64, item.chunk_id)}
                )
                for item in packet.citations
            ]
        }
    )
    assert compiled_policy_packet_id(linked) == legacy_digest


def test_chunk_reads_materialize_only_selected_evidence(generation, monkeypatch) -> None:
    """Qodo regression: repeated citation reads use cached IDs and indexed single-row lookup."""
    from contextlib import closing

    from policynim.services.index_store import create_index_store
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore

    settings, _, generation_id = generation
    with closing(create_index_store(settings)) as store:
        chunk = store.list_chunks()[0]
    service_module._chunk_key_lookup.cache_clear()
    original_ids = SQLiteVecIndexStore.list_chunk_ids
    identifier_reads: list[int] = []

    def identifiers(self):
        """Count only cold metadata lookup without fetching corpus text."""
        identifier_reads.append(1)
        return original_ids(self)

    def fail_full_corpus(self):
        """Catch accidental full-corpus materialization in a resource read."""
        pytest.fail("a chunk resource mustn't load all chunk text")

    monkeypatch.setattr(SQLiteVecIndexStore, "list_chunk_ids", identifiers)
    monkeypatch.setattr(SQLiteVecIndexStore, "list_chunks", fail_full_corpus)
    for _ in range(2):
        result = read_chunk_resource(
            settings, generation_id=generation_id, chunk_key=resource_key(chunk.chunk_id)
        )
        assert result["chunk"] == chunk.model_dump(mode="json")
    assert identifier_reads == [1]


def test_compile_and_evidence_use_principal_daily_admission(
    generation, monkeypatch, tmp_path
) -> None:
    """Grounded tools share the same persisted principal allowance as existing tools."""
    from policynim.services.authorization import AuthenticatedPrincipal

    settings, _, generation_id = generation
    settings = settings.model_copy(
        update={
            "beta_auth_db_path": tmp_path / "auth.sqlite3",
            "mcp_principal_daily_operation_quota": 1,
        }
    )
    monkeypatch.setattr(registration, "get_settings", lambda: settings)
    calls: list[str] = []

    def compile_fixture(*args, **kwargs):
        """Record only genuinely admitted provider graph execution."""
        calls.append("compiled")
        return CompileResult(
            generation_id=generation_id, packet=traced_fixture(generation_id).compiled_packet
        )

    monkeypatch.setattr(registration, "compile_policy", compile_fixture)
    caller = AuthenticatedPrincipal(
        issuer="https://issuer.example",
        subject="quota-owner",
        client_id="fixture",
        scopes=frozenset({"policies:read", "policies:evaluate"}),
        source="oauth",
    )

    async def exercise() -> None:
        """Check the second expensive operation rejects before service construction."""
        context = mcp_module._HOSTED_PRINCIPAL.set(caller)
        try:
            runtime = mcp_module._ToolExecutor(settings=settings)
            server = mcp_module._register_tools(mcp_module._new_mcp_server(runtime))
            async with Client(server) as client:
                first = await client.call_tool("policy_compile", {"task": "review"})
                assert not first.is_error
                second = await client.call_tool("policy_preflight_evidence", {"task": "review"})
                assert second.is_error
                assert isinstance(second.content[0], TextContent)
                assert "quota_exceeded" in second.content[0].text
        finally:
            mcp_module._HOSTED_PRINCIPAL.reset(context)
        assert calls == ["compiled"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "updates",
    [
        {"nvidia_embed_model": "different-embedding-model"},
        {"nvidia_base_url": "https://changed.example/v1"},
    ],
)
def test_retained_resources_use_their_sealed_embedding_identity(generation, updates) -> None:
    """Qodo regression: retained source evidence outlives the runtime's model configuration."""
    from contextlib import closing

    from policynim.services.index_store import create_index_store

    settings, _, generation_id = generation
    original_catalog = read_policy_catalog(settings, generation_id=generation_id)
    original_policy = read_policy_resource(
        settings, generation_id=generation_id, policy_key=resource_key("A")
    )
    with closing(create_index_store(settings)) as store:
        chunk_id = store.list_chunks()[0].chunk_id
    original_chunk = read_chunk_resource(
        settings, generation_id=generation_id, chunk_key=resource_key(chunk_id)
    )
    changed = settings.model_copy(update=updates)
    assert read_policy_catalog(changed, generation_id=generation_id) == original_catalog
    assert (
        read_policy_resource(changed, generation_id=generation_id, policy_key=resource_key("A"))
        == original_policy
    )
    assert (
        read_chunk_resource(changed, generation_id=generation_id, chunk_key=resource_key(chunk_id))
        == original_chunk
    )
    with pytest.raises(GenerationError):
        create_index_store(changed)


def test_invalid_guidance_does_not_charge_principal_allowance(
    generation, monkeypatch, tmp_path
) -> None:
    """Qodo regression: request rejection happens before accepted-operation accounting."""
    from policynim.services.authorization import AuthenticatedPrincipal

    settings, _, generation_id = generation
    settings = settings.model_copy(
        update={
            "beta_auth_db_path": tmp_path / "invalid-input-auth.sqlite3",
            "mcp_principal_daily_operation_quota": 1,
        }
    )
    monkeypatch.setattr(registration, "get_settings", lambda: settings)
    calls: list[str] = []

    def compile_fixture(*args, **kwargs):
        """Record a genuinely valid admitted request after the invalid attempts."""
        calls.append("compiled")
        return CompileResult(
            generation_id=generation_id, packet=traced_fixture(generation_id).compiled_packet
        )

    monkeypatch.setattr(registration, "compile_policy", compile_fixture)
    caller = AuthenticatedPrincipal(
        issuer="https://issuer.example",
        subject="request-validation",
        client_id="fixture",
        scopes=frozenset({"policies:read", "policies:evaluate"}),
        source="oauth",
    )

    async def exercise() -> None:
        """Leave no accounting database after schema-invalid task/domain input."""
        context = mcp_module._HOSTED_PRINCIPAL.set(caller)
        try:
            runtime = mcp_module._ToolExecutor(settings=settings)
            server = mcp_module._register_tools(mcp_module._new_mcp_server(runtime))
            async with Client(server) as client:
                for name in ("policy_compile", "policy_preflight_evidence"):
                    for arguments in ({"task": " "}, {"task": "review", "domain": " "}):
                        rejected = await client.call_tool(name, arguments)
                        assert rejected.is_error
                        assert isinstance(rejected.content[0], TextContent)
                        assert "invalid_request" in rejected.content[0].text
                assert not settings.beta_auth_db_path.exists()
                assert calls == []
                accepted = await client.call_tool("policy_compile", {"task": "review"})
                assert not accepted.is_error
                assert calls == ["compiled"]
        finally:
            mcp_module._HOSTED_PRINCIPAL.reset(context)

    asyncio.run(exercise())


def test_resource_concurrency_preserves_another_principal_without_charging(
    generation, tmp_path, monkeypatch
) -> None:
    """A read-only caller can't take every hosted slot and isn't charged an operation allowance."""
    from threading import Event, Lock
    from urllib.parse import parse_qs

    import httpx
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.exceptions import MCPError
    from test_mcp_authorization import oauth_settings, token_claims
    from test_mcp_protocol import _serve_http

    from policynim.services.authorization import IntrospectionTokenVerifier

    base, _, _ = generation
    settings = oauth_settings(
        tmp_path,
        generations_dir=base.generations_dir,
        nvidia_embed_model=base.nvidia_embed_model,
        nvidia_base_url=base.nvidia_base_url,
        mcp_max_concurrent_operations=2,
        mcp_max_concurrent_operations_per_principal=1,
    )

    def introspect(request: httpx.Request) -> httpx.Response:
        """Authenticate independent subjects through the actual issuer boundary."""
        token = parse_qs(request.content.decode())["token"][0]
        return httpx.Response(200, json=token_claims(sub=token))

    verifier = IntrospectionTokenVerifier(settings, transport=httpx.MockTransport(introspect))
    monkeypatch.setattr(mcp_module, "IntrospectionTokenVerifier", lambda *args, **kwargs: verifier)
    monkeypatch.setattr(registration, "get_settings", lambda: settings)
    read = registration.read_policy_catalog
    started, release = Event(), Event()
    lock = Lock()
    calls: list[int] = []

    def held_read(*args, **kwargs):
        """Keep the first real catalog read active while checking shared capacity."""
        with lock:
            calls.append(1)
            first = len(calls) == 1
        if first:
            started.set()
            assert release.wait(5)
        return read(*args, **kwargs)

    monkeypatch.setattr(registration, "read_policy_catalog", held_read)

    async def exercise() -> None:
        """Cross TCP, issuer authentication and resource dispatch for two principals."""
        async with _serve_http(mcp_module._build_streamable_http_app(settings)) as origin:
            async with (
                httpx2.AsyncClient(
                    headers={"Authorization": "Bearer owner"}, trust_env=False
                ) as own,
                httpx2.AsyncClient(
                    headers={"Authorization": "Bearer other"}, trust_env=False
                ) as other,
                Client(streamable_http_client(origin + "/mcp", http_client=own)) as owner_client,
                Client(streamable_http_client(origin + "/mcp", http_client=other)) as other_client,
            ):
                pending = asyncio.create_task(owner_client.read_resource("policynim://catalog"))
                try:
                    async with asyncio.timeout(5):
                        while not started.is_set():
                            await asyncio.sleep(0.001)
                    with pytest.raises(MCPError, match="principal_busy"):
                        await owner_client.read_resource("policynim://catalog")
                    assert (await other_client.read_resource("policynim://catalog")).contents
                finally:
                    release.set()
                    await pending
                assert (await owner_client.read_resource("policynim://catalog")).contents
        assert len(calls) == 3
        assert not settings.beta_auth_db_path.exists()

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["policy_compile", "policy_preflight_evidence"])
def test_grounded_service_checks_its_named_operation_before_provider_allocation(
    generation, monkeypatch, operation
) -> None:
    """Future permission changes can't silently fall back to the older preflight rule."""
    from policynim.services import authorization
    from policynim.services.authorization import AuthenticatedPrincipal, AuthorizationError
    from policynim.types import CompileRequest, PreflightRequest

    settings, _, _ = generation
    caller = AuthenticatedPrincipal(
        issuer="https://issuer.example",
        subject="owner",
        client_id="fixture",
        scopes=frozenset({"policies:read", "policies:evaluate"}),
        source="oauth",
    )
    monkeypatch.setitem(authorization.OPERATION_SCOPES, operation, frozenset({"future:scope"}))

    def unavailable(*args, **kwargs):
        """Permission rejection must precede provider graph allocation."""
        pytest.fail("unauthorized operation allocated providers")

    monkeypatch.setattr(service_module, "create_policy_compiler_service", unavailable)
    monkeypatch.setattr(service_module, "create_preflight_service", unavailable)
    with pytest.raises(AuthorizationError, match="insufficient_scope"):
        if operation == "policy_compile":
            service_module.compile_policy(settings, CompileRequest(task="review"), principal=caller)
        else:
            service_module.preflight_evidence(
                settings, PreflightRequest(task="review"), principal=caller
            )


@pytest.mark.parametrize(
    "updates",
    [
        {"nvidia_embed_model": "changed-model"},
        {"nvidia_base_url": "https://changed-endpoint.example/v1"},
    ],
)
def test_current_catalog_requires_retrieval_compatible_configured_generation(generation, updates):
    """The current catalog can't advertise readiness that configured retrieval rejects."""
    from policynim.errors import GenerationError
    from policynim.services.index_store import create_index_store

    settings, _, generation_id = generation
    pinned = settings.model_copy(update={"generation_id": generation_id, **updates})
    with pytest.raises(GenerationError):
        read_policy_catalog(pinned)
    with pytest.raises(GenerationError):
        create_index_store(pinned)
    archived = read_policy_catalog(pinned, generation_id=generation_id)
    assert archived["generation_id"] == generation_id
