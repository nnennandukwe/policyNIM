"""Exercise shared SDK clients, cancellation, and graceful server shutdown offline."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from typing import Any

import httpx
import pytest
from openai import OpenAI

from policynim.errors import ProviderError
from policynim.interfaces.mcp import _ToolExecutor
from policynim.providers import lifecycle
from policynim.providers.nvidia import NVIDIAEmbedder, NVIDIAPolicyCompiler
from policynim.settings import Settings

NO_ENV: dict[str, Any] = {"_env_file": None}


def settings() -> Settings:
    """Use isolated settings that can't pick up operator credentials or dotenv files."""
    return Settings(
        **NO_ENV,
        nvidia_api_key="offline-key",
        nvidia_base_url="https://provider.invalid/v1",
        nvidia_max_retries=0,
    )


class BlockingTransport(httpx.BaseTransport):
    """Hold real SDK HTTP requests until the test releases active I/O."""

    def __init__(self, expected: int) -> None:
        """Track request admission, active I/O, and transport cleanup."""
        self.expected = expected
        self.started = Event()
        self.release = Event()
        self.lock = Lock()
        self.requests = 0
        self.active = 0
        self.closes = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Return an embedding only after cancellation or shutdown has been observed."""
        with self.lock:
            self.requests += 1
            self.active += 1
            if self.requests == self.expected:
                self.started.set()
        try:
            assert self.release.wait(3), "Test didn't release the provider transport"
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})
        finally:
            with self.lock:
                self.active -= 1

    def close(self) -> None:
        """Require all active I/O to drain before the shared transport closes."""
        assert self.active == 0
        self.closes += 1


def install_sdk(monkeypatch, transport: httpx.BaseTransport) -> list[OpenAI]:
    """Preserve the real SDK while replacing its network transport."""
    clients: list[OpenAI] = []

    def create(**kwargs) -> OpenAI:
        """Build a real SDK client whose sockets are replaced with a controlled transport."""
        client = OpenAI(http_client=httpx.Client(transport=transport), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(lifecycle, "OpenAI", create)
    return clients


def test_concurrent_adapters_borrow_one_sdk_and_dont_close_it(monkeypatch) -> None:
    """Worker-local adapters share one immutable SDK/HTTP client and preserve owner cleanup."""
    clients = install_sdk(
        monkeypatch,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
            )
        ),
    )
    pool = lifecycle.NVIDIAClients()
    active_settings = settings()

    def invoke() -> list[float]:
        """Borrow the same SDK through independent adapters in each worker thread."""
        with lifecycle.borrow_clients(pool):
            embedder = NVIDIAEmbedder.from_settings(active_settings)
            compiler = NVIDIAPolicyCompiler.from_settings(active_settings)
            assert embedder._client is compiler._client
            try:
                return embedder.embed_query("private query")
            finally:
                embedder.close()
                compiler.close()

    with ThreadPoolExecutor(max_workers=4) as workers:
        assert list(workers.map(lambda index: invoke(), range(12))) == [[1.0, 0.0]] * 12
    assert len(clients) == 1
    assert not clients[0].is_closed()
    pool.close()
    pool.close()
    assert clients[0].is_closed()
    with pytest.raises(ProviderError, match="shutting down"):
        pool.get(active_settings)


@pytest.mark.parametrize("stop", ["cancel", "shutdown"])
def test_executor_drains_inflight_requests_before_closing_clients(monkeypatch, stop: str) -> None:
    """Cancellation and shutdown stop later work while keeping current I/O owned."""
    count = 1 if stop == "cancel" else 2
    transport = BlockingTransport(count)
    clients = install_sdk(monkeypatch, transport)
    active_settings = settings()
    later_stages: list[str] = []

    def operation() -> None:
        """Execute a real adapter and record any forbidden later stage."""
        adapter = NVIDIAEmbedder.from_settings(active_settings)
        try:
            adapter.embed_query("private query")
            later_stages.append("generation")
        finally:
            adapter.close()

    async def scenario() -> None:
        """Observe stop signals before releasing the synchronous upstream calls."""
        executor = _ToolExecutor(count, settings=active_settings)
        requests = [asyncio.create_task(executor.run(operation)) for _ in range(count)]
        shutdowns: list[asyncio.Task] = []
        try:
            assert await asyncio.to_thread(transport.started.wait, 2)
            assert len(clients) == 1
            budgets = list(executor._budgets)
            if stop == "cancel":
                requests[0].cancel()
                assert await asyncio.to_thread(budgets[0].cancelled.wait, 2)
            else:
                shutdowns = [asyncio.create_task(executor.close()) for _ in range(2)]
                assert await asyncio.to_thread(budgets[0].cancelled.wait, 2)
                from mcp.server.mcpserver.exceptions import ToolError

                with pytest.raises(ToolError, match="shutting_down"):
                    await executor.run(operation)
            assert not any(request.done() for request in requests)
            assert not clients[0].is_closed()
            assert transport.closes == 0
        finally:
            transport.release.set()
            results = await asyncio.gather(*requests, return_exceptions=True)
            if shutdowns:
                await asyncio.gather(*shutdowns)
            await executor.close()
        assert all(isinstance(result, BaseException) for result in results)
        if stop == "cancel":
            assert isinstance(results[0], asyncio.CancelledError)
        else:
            assert all(
                isinstance(result, ProviderError) and result.failure_class == "cancelled"
                for result in results
            )
        assert not executor._budgets
        assert transport.active == 0
        assert transport.closes == 1
        assert later_stages == []

    asyncio.run(scenario())


def test_server_lifespan_closes_borrowed_clients(monkeypatch) -> None:
    """The production server lifespan owns the actual pool cleanup hook."""
    from policynim.interfaces.mcp import _new_mcp_server

    clients = install_sdk(
        monkeypatch,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
            )
        ),
    )
    active_settings = settings()

    async def scenario() -> None:
        """Run the SDK's configured lifespan around a real borrowed-client operation."""
        runtime = _ToolExecutor(1, settings=active_settings)
        server = _new_mcp_server(runtime)
        assert server.settings.lifespan is not None
        async with server.settings.lifespan(server):
            await runtime.run(
                lambda: NVIDIAEmbedder.from_settings(active_settings).embed_query("private query")
            )
            assert len(clients) == 1
            assert not clients[0].is_closed()
        assert clients[0].is_closed()

    asyncio.run(scenario())


def test_close_attempts_every_resource_when_one_cleanup_fails() -> None:
    """A failed compiler close can't leak the generator in a CLI-owned graph."""
    from policynim.operations import close_components

    closed = []

    class Resource:
        """Expose a controlled failing or successful cleanup hook."""

        def __init__(self, name: str, fail: bool = False) -> None:
            """Store the resource's expected cleanup outcome."""
            self.name, self.fail = name, fail

        def close(self) -> None:
            """Record cleanup before injecting its failure."""
            closed.append(self.name)
            if self.fail:
                raise RuntimeError("close failed")

    with pytest.raises(RuntimeError, match="close failed"):
        close_components(Resource("compiler", True), Resource("generator"))
    assert closed == ["compiler", "generator"]


@pytest.mark.parametrize(
    "module_name, first, second",
    [
        ("compiler", "create_policy_router_service", "_create_default_policy_compiler"),
        ("preflight", "create_policy_compiler_service", "_create_default_generator"),
    ],
)
def test_factory_cleans_up_graph_when_provider_constructor_fails(
    monkeypatch, module_name, first, second
) -> None:
    """Provider construction failure closes resources already assigned to the factory."""
    from importlib import import_module
    from types import SimpleNamespace

    module = import_module("policynim.services." + module_name)
    closed = []
    monkeypatch.setattr(
        module, first, lambda active: SimpleNamespace(close=lambda: closed.append(1))
    )

    def fail(active):
        """Fail the later constructor after the earlier graph exists."""
        raise RuntimeError("constructor failed")

    monkeypatch.setattr(module, second, fail)
    factory = getattr(
        module,
        "create_policy_compiler_service"
        if module_name == "compiler"
        else "create_preflight_service",
    )
    with pytest.raises(RuntimeError, match="constructor failed"):
        factory(settings())
    assert closed == [1]


def test_cli_deadline_failure_exits_nonzero_and_closes_service(monkeypatch) -> None:
    """Run the real CLI and search service through a deadline rejection and cleanup."""
    from test_search_service import MockEmbedder, MockIndexStore, MockReranker
    from typer.testing import CliRunner

    import policynim.interfaces.cli as cli
    from policynim.operations import OperationBudget, operation_scope
    from policynim.services.search import SearchService

    active_settings = settings()
    budget = OperationBudget(5)
    embedder = MockEmbedder()
    reranker = MockReranker()
    index = MockIndexStore([])
    index.count = lambda: 1

    def expire(text: str) -> list[float]:
        """End the outer request budget while returning an otherwise valid embedding."""
        budget.deadline = budget.clock()
        return [1.0, 0.0]

    monkeypatch.setattr(embedder, "embed_query", expire)
    service = SearchService(embedder=embedder, index_store=index, reranker=reranker)
    monkeypatch.setattr(cli, "_load_setup_dependent_settings", lambda: active_settings)
    monkeypatch.setattr(cli, "create_search_service", lambda active: service)
    result = None
    # The root scope also rejects its late result after the CLI consumes the service failure.
    with pytest.raises(ProviderError), operation_scope(budget):
        result = CliRunner().invoke(cli.app, ["search", "--query", "backend logs"])
    assert result is not None
    assert result.exit_code == 1
    assert "deadline" in result.output.lower()
    assert "Retry later" in result.output
    assert embedder.closed and reranker.closed
    assert index.last_query_embedding is None


def test_pool_close_failure_still_attempts_all_owned_clients() -> None:
    """One broken SDK close can't prevent another pooled transport from closing."""
    closed = []

    class RecordingTransport(httpx.MockTransport):
        """Record native HTTP transport cleanup and optionally fail it."""

        def __init__(self, name: str, fail: bool = False) -> None:
            """Use a network-free transport with an explicit cleanup outcome."""
            super().__init__(lambda request: httpx.Response(200))
            self.name, self.fail = name, fail

        def close(self) -> None:
            """Record cleanup before reporting a controlled close failure."""
            closed.append(self.name)
            if self.fail:
                raise RuntimeError("close failure")

    pool = lifecycle.NVIDIAClients()
    first = httpx.Client(transport=RecordingTransport("sdk", True))
    second = httpx.Client(transport=RecordingTransport("retrieval"))
    pool._clients = {
        ("sdk", "https://a.invalid", 1, "test"): first,
        ("retrieval", "https://b.invalid", 1, "test"): second,
    }
    with pytest.raises(RuntimeError, match="close failure"):
        pool.close()
    assert closed == ["sdk", "retrieval"]
    assert first.is_closed and second.is_closed
    assert pool._closed and not pool._clients


def test_cancelling_shutdown_still_closes_pool_after_drain(monkeypatch) -> None:
    """Direct task cancellation during shutdown can't skip the final pool close."""
    transport = BlockingTransport(1)
    clients = install_sdk(monkeypatch, transport)
    active_settings = settings()

    async def scenario() -> None:
        """Cancel shutdown while a request holds a shared transport open."""
        executor = _ToolExecutor(1, settings=active_settings)
        request = asyncio.create_task(
            executor.run(
                lambda: NVIDIAEmbedder.from_settings(active_settings).embed_query("private")
            )
        )
        assert await asyncio.to_thread(transport.started.wait, 2)
        budget = next(iter(executor._budgets))
        shutdown = asyncio.create_task(executor.close())
        assert await asyncio.to_thread(budget.cancelled.wait, 2)
        shutdown.cancel()
        transport.release.set()
        await asyncio.gather(request, shutdown, return_exceptions=True)
        assert not executor._budgets
        assert clients[0].is_closed()
        assert transport.closes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["search", "preflight", "regeneration"])
def test_live_eval_factories_close_partial_provider_graphs(monkeypatch, kind: str) -> None:
    """Evaluation factory failure closes every provider created before the failure."""
    from types import SimpleNamespace

    import policynim.providers as providers
    import policynim.services.eval as evaluation

    closed = []

    def resource(name):
        """Build a constructor result that records deterministic optional cleanup."""
        return SimpleNamespace(close=lambda: closed.append(name))

    for adapter, name in [
        (providers.NVIDIAEmbedder, "embedder"),
        (providers.NVIDIAReranker, "reranker"),
        (providers.NVIDIAGenerator, "generator"),
        (providers.NVIDIAPolicyCompiler, "compiler"),
    ]:
        monkeypatch.setattr(adapter, "from_settings", lambda active, name=name: resource(name))

    def fail(*args, **kwargs):
        """Reject a later construction after earlier resources already exist."""
        raise RuntimeError("construction failed")

    monkeypatch.setattr(evaluation, "create_index_store", lambda active: object())
    if kind == "search":
        monkeypatch.setattr(evaluation, "create_index_store", fail)
    elif kind == "preflight":
        monkeypatch.setattr(providers.NVIDIAPolicyCompiler, "from_settings", fail)
    else:
        monkeypatch.setattr(evaluation, "_create_live_conformance_service", fail)
    factory = getattr(evaluation, "_create_live_" + kind + "_service")
    kwargs = {"backend": "nemo"} if kind == "regeneration" else {}
    with pytest.raises(RuntimeError, match="construction failed"):
        factory(settings(), rerank_enabled=True, **kwargs)
    expected = {
        "search": {"embedder", "reranker"},
        "preflight": {"embedder", "reranker", "generator"},
        "regeneration": {"embedder", "reranker", "generator", "compiler"},
    }[kind]
    assert set(closed) == expected
    assert len(closed) == len(expected)


@pytest.mark.parametrize(
    "module_name, first, second",
    [
        ("compiler", "create_policy_router_service", "_create_default_policy_compiler"),
        ("preflight", "create_policy_compiler_service", "_create_default_generator"),
    ],
)
def test_setup_error_remains_primary_when_cleanup_also_fails(
    monkeypatch, module_name, first, second
) -> None:
    """A double failure must retain the actionable provider configuration error."""
    from importlib import import_module
    from types import SimpleNamespace

    from policynim.errors import ConfigurationError

    module = import_module("policynim.services." + module_name)

    def close():
        """Inject a cleanup failure after provider setup already failed."""
        raise RuntimeError("cleanup failed")

    def fail(active):
        """Preserve the primary configuration error callers can act on."""
        raise ConfigurationError("provider setup failed", failure_class="auth")

    monkeypatch.setattr(module, first, lambda active: SimpleNamespace(close=close))
    monkeypatch.setattr(module, second, fail)
    factory = getattr(
        module,
        "create_policy_compiler_service"
        if module_name == "compiler"
        else "create_preflight_service",
    )
    with pytest.raises(ConfigurationError, match="provider setup failed") as caught:
        factory(settings())
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert caught.value.failure_class == "auth"


def test_anyio_cancellation_observes_a_worker_failure_before_checkpoint(monkeypatch) -> None:
    """A worker that finishes during cancellation must have its exception retrieved."""
    import anyio

    from policynim.interfaces.mcp import _run_sync_until_complete

    async def scenario() -> None:
        """Inject the completed-worker cancellation race at the await boundary."""

        async def fail():
            """Complete with a failure before the cancellation drain checks task state."""
            raise RuntimeError("worker failed")

        worker = asyncio.create_task(fail())
        await asyncio.sleep(0)
        assert worker.done() and getattr(worker, "_log_traceback")
        original_create = asyncio.create_task
        original_shield = asyncio.shield

        def create(coroutine):
            """Return the completed worker while closing the unused coroutine object."""
            coroutine.close()
            return worker

        def shield(task):
            """Model direct cancellation of the first shielded await."""
            cancelled = asyncio.get_running_loop().create_future()
            cancelled.cancel()
            return cancelled

        monkeypatch.setattr(asyncio, "create_task", create)
        monkeypatch.setattr(asyncio, "shield", shield)
        try:
            with anyio.CancelScope() as scope:
                scope.cancel()
                await _run_sync_until_complete(lambda: None)
            assert not getattr(worker, "_log_traceback")
        finally:
            worker.exception()
            monkeypatch.setattr(asyncio, "create_task", original_create)
            monkeypatch.setattr(asyncio, "shield", original_shield)

    asyncio.run(scenario())


def test_resource_stack_retains_original_error_and_attempts_every_close() -> None:
    """Live-evaluation ownership must preserve setup errors when multiple closes fail."""
    from policynim.errors import ConfigurationError
    from policynim.operations import ResourceStack

    closed = []

    def close(name):
        """Record cleanup before injecting an additional failure."""
        closed.append(name)
        raise RuntimeError("cleanup failed")

    with pytest.raises(ConfigurationError, match="setup failed") as caught:
        with ResourceStack() as cleanup:
            cleanup.callback(close, "one")
            cleanup.callback(close, "two")
            raise ConfigurationError("setup failed", failure_class="auth")
    assert closed == ["two", "one"]
    assert isinstance(caught.value.__cause__, RuntimeError)
    # Failure-class traversal must terminate even when cleanup retains a causal chain.
    from policynim.interfaces.mcp import _failure_class_from_error

    assert _failure_class_from_error(caught.value) == "auth"
