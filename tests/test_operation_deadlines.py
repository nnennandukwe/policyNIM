"""Integration regressions for shared operation deadlines and NVIDIA retries."""

from __future__ import annotations

from datetime import UTC
from typing import Any

import httpx
import pytest
from openai import OpenAI

from policynim.errors import ProviderError
from policynim.providers.nvidia import NVIDIAEmbedder

NO_ENV: dict[str, Any] = {"_env_file": None}


def test_retry_after_larger_than_budget_never_retries() -> None:
    """A real SDK response can't trigger a second request beyond the budget."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Return a provider rate limit through the SDK's HTTP boundary."""
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "600"}, json={"error": "secret"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        with OpenAI(
            api_key="test",
            base_url="https://provider.invalid/v1",
            http_client=http_client,
            max_retries=0,
        ) as client:
            embedder = NVIDIAEmbedder(
                api_key="test",
                model="test",
                base_url="https://provider.invalid/v1",
                batch_size=2,
                timeout_seconds=1,
                max_retries=2,
                client=client,
            )
            with pytest.raises(ProviderError) as caught:
                embedder.embed_query("private task")
    assert len(requests) == 1
    assert caught.value.failure_class == "deadline_exhausted"


class Clock:
    """Advance simulated time only when a test or backoff requests it."""

    def __init__(self) -> None:
        """Start the clock with no elapsed time or recorded waits."""
        self.now = 0.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        """Read the simulated monotonic clock."""
        return self.now

    def wait(self, seconds: float) -> bool:
        """Advance through an interruptible wait without sleeping."""
        self.waits.append(seconds)
        self.now += seconds
        return False


@pytest.mark.parametrize("retry_after", ["2", "Thu, 01 Jan 2026 00:00:02 GMT"])
def test_retry_after_and_remaining_timeout(monkeypatch, retry_after: str) -> None:
    """Real SDK retries honor both header forms and shrink the next timeout."""
    import policynim.providers.nvidia as provider
    from policynim.operations import OperationBudget, operation_scope

    clock = Clock()
    budget = OperationBudget(5, clock=clock)
    monkeypatch.setattr(budget.cancelled, "wait", clock.wait)
    monkeypatch.setattr(provider.random, "uniform", lambda low, high: high)

    class FixedDateTime:
        """Freeze wall time for the HTTP-date header without altering monotonic time."""

        @staticmethod
        def now(zone):
            """Return the reference wall time used by the HTTP-date fixture."""
            from datetime import datetime

            return datetime(2026, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(provider, "datetime", FixedDateTime)
    timeouts: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Return one rate limit followed by valid embedding output."""
        timeouts.append(request.extensions["timeout"]["read"])
        if len(timeouts) == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after}, json={})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with OpenAI(
            api_key="test", base_url="https://provider.invalid", http_client=http, max_retries=0
        ) as client:
            adapter = NVIDIAEmbedder(
                api_key="test",
                model="test",
                base_url="https://provider.invalid",
                batch_size=2,
                timeout_seconds=30,
                max_retries=2,
                client=client,
            )
            with operation_scope(budget):
                assert adapter.embed_query("private") == [1.0, 0.0]
    assert clock.waits == [2.0]
    assert timeouts == [5.0, 3.0]


@pytest.mark.parametrize("header", ["invalid", "NaN", "inf", "-5"])
def test_invalid_retry_after_uses_bounded_jitter(monkeypatch, header: str) -> None:
    """Malformed and past delays can't cause unbounded or negative sleeps."""
    import policynim.providers.nvidia as provider
    from policynim.operations import OperationBudget, operation_scope

    clock = Clock()
    budget = OperationBudget(10, clock=clock)
    monkeypatch.setattr(budget.cancelled, "wait", clock.wait)
    monkeypatch.setattr(provider.random, "uniform", lambda low, high: high)
    calls = 0

    def send(timeout: float):
        """Raise a real HTTP status exception for each attempted request."""
        nonlocal calls
        calls += 1
        response = httpx.Response(
            503,
            headers={"Retry-After": header},
            request=httpx.Request("POST", "https://provider.invalid"),
        )
        response.raise_for_status()

    with pytest.raises(ProviderError) as caught, operation_scope(budget):
        provider._request_with_retry(
            send,
            operation="test",
            model_setting="CHAT",
            endpoint_setting="BASE_URL",
            max_retries=3,
            timeout_seconds=30,
            retry_initial_seconds=1,
            retry_max_seconds=2,
        )
    assert caught.value.failure_class == "http_status"
    assert calls == 4
    assert clock.waits == [1, 2, 2]


@pytest.mark.parametrize("failure", ["deadline", "cancelled"])
def test_no_index_or_rerank_starts_after_embedding_stops_budget(failure: str) -> None:
    """The real search service blocks downstream I/O even with non-cooperative doubles."""
    from test_search_service import MockEmbedder, MockIndexStore, MockReranker

    from policynim.operations import OperationBudget, operation_scope
    from policynim.services.search import SearchService
    from policynim.types import SearchRequest

    clock = Clock()
    budget = OperationBudget(1, clock=clock)

    class ExpiringEmbedder(MockEmbedder):
        """Exhaust or cancel the shared budget during a successful embedding call."""

        def embed_query(self, text: str) -> list[float]:
            """Return data after consuming all permitted operation work."""
            if failure == "deadline":
                clock.now = 1
            else:
                budget.cancelled.set()
            return [1.0, 0.0]

    index = MockIndexStore([])
    # Supply an existing nonempty index so the readiness guard is a valid control.
    index.count = lambda: 1
    reranker = MockReranker()
    service = SearchService(embedder=ExpiringEmbedder(), index_store=index, reranker=reranker)
    with pytest.raises(ProviderError) as caught, operation_scope(budget):
        service.search(SearchRequest(query="backend logs"))
    assert caught.value.failure_class == (
        "deadline_exhausted" if failure == "deadline" else failure
    )
    assert index.last_query_embedding is None
    assert reranker.last_query is None


def test_cancellation_interrupts_backoff_without_another_request(monkeypatch) -> None:
    """Cancellation during a retry wait blocks the next attempt."""
    from policynim.operations import OperationBudget, operation_scope
    from policynim.providers.nvidia import _request_with_retry

    budget = OperationBudget(10)
    attempts = []

    def wait(seconds: float) -> bool:
        """Simulate cancellation arriving while the retry wait is active."""
        budget.cancelled.set()
        return True

    def send(timeout: float):
        """Fail once with a retryable network error."""
        attempts.append(timeout)
        raise httpx.ConnectError("private endpoint error")

    monkeypatch.setattr(budget.cancelled, "wait", wait)
    with pytest.raises(ProviderError) as caught, operation_scope(budget):
        _request_with_retry(
            send,
            operation="test",
            model_setting="CHAT",
            endpoint_setting="BASE_URL",
            max_retries=2,
            timeout_seconds=1,
            retry_initial_seconds=0.5,
            retry_max_seconds=8,
        )
    assert len(attempts) == 1
    assert caught.value.failure_class == "cancelled"


def test_provider_diagnostics_report_measured_and_unknown_usage(caplog, monkeypatch) -> None:
    """Safe metadata never contains request content, credentials, or upstream errors."""
    import json
    import logging
    from types import SimpleNamespace

    from policynim.operations import operation_scope
    from policynim.providers.nvidia import _request_with_retry

    monkeypatch.setattr(logging.getLogger("policynim.operations"), "propagate", True)
    caplog.set_level(logging.INFO, logger="policynim.operations")
    with operation_scope():
        response = _request_with_retry(
            lambda timeout: SimpleNamespace(usage=SimpleNamespace(total_tokens=12)),
            operation="generation",
            model_setting="CHAT",
            endpoint_setting="BASE_URL",
            max_retries=0,
            timeout_seconds=1,
            retry_initial_seconds=0.5,
            retry_max_seconds=8,
        )
        assert response.usage.total_tokens == 12
        with pytest.raises(ProviderError):
            _request_with_retry(
                lambda timeout: (_ for _ in ()).throw(
                    httpx.ConnectError("secret-key private-task private-policy")
                ),
                operation="generation",
                model_setting="CHAT",
                endpoint_setting="BASE_URL",
                max_retries=0,
                timeout_seconds=1,
                retry_initial_seconds=0.5,
                retry_max_seconds=8,
            )
    events = [json.loads(record.message) for record in caplog.records]
    assert events[0]["usage_status"] == "measured"
    assert events[0]["usage_tokens"] == 12
    assert events[1]["usage_status"] == "unknown"
    assert events[1]["usage_tokens"] is None
    assert not any(
        secret in caplog.text for secret in ["secret-key", "private-task", "private-policy"]
    )


@pytest.mark.parametrize(
    "field",
    [
        "operation_timeout_seconds",
        "nvidia_timeout_seconds",
        "nvidia_retry_initial_seconds",
        "nvidia_retry_max_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), "invalid"])
def test_budget_settings_reject_invalid_values(field: str, value: object) -> None:
    """Every budget setting rejects malformed and nonfinite values before client creation."""
    from pydantic import ValidationError

    from policynim.settings import Settings

    with pytest.raises(ValidationError):
        Settings(**{field: value, "_env_file": None})


@pytest.mark.parametrize(
    "field, default",
    [
        ("operation_timeout_seconds", 120),
        ("nvidia_retry_initial_seconds", 0.5),
        ("nvidia_retry_max_seconds", 8),
    ],
)
def test_budget_settings_load_defaults_constructor_and_environment(
    monkeypatch, field, default
) -> None:
    """Documented defaults and environment names preserve direct-construction precedence."""
    from policynim.settings import Settings

    env = "POLICYNIM_" + field.upper()
    monkeypatch.delenv(env, raising=False)
    assert getattr(Settings(**NO_ENV), field) == default
    monkeypatch.setenv(env, "3")
    assert getattr(Settings(**NO_ENV), field) == 3
    overrides: dict[str, Any] = {field: 4}
    assert getattr(Settings(**NO_ENV, **overrides), field) == 4


@pytest.mark.parametrize("stop_stage", [None, "embedding", "index", "reranking", "compilation"])
@pytest.mark.parametrize("stop_kind", ["deadline", "cancelled"])
def test_preflight_has_one_budget_and_never_starts_a_later_stage(
    monkeypatch, stop_stage, stop_kind
) -> None:
    """A valid full pipeline provides the control for each deliberately stopped stage."""
    from test_preflight_service import (
        MockEmbedder,
        MockGenerator,
        MockIndexStore,
        MockPolicyCompiler,
        MockReranker,
        make_chunk,
    )

    from policynim.operations import OperationBudget, current_budget, operation_scope
    from policynim.services.preflight import PreflightService
    from policynim.types import GeneratedPolicyGuidance, GeneratedPreflightDraft, PreflightRequest

    clock = Clock()
    budget = OperationBudget(10, clock=clock)
    chunk = make_chunk(
        chunk_id="BACKEND-1", policy_id="BACKEND-LOG-001", domain="backend", score=0.98
    )
    embedder = MockEmbedder()
    index = MockIndexStore([chunk])
    reranker = MockReranker()
    compiler = MockPolicyCompiler()
    generator = MockGenerator(
        GeneratedPreflightDraft(
            summary="Use logging.",
            citation_ids=["BACKEND-1"],
            applicable_policies=[
                GeneratedPolicyGuidance(
                    policy_id="BACKEND-LOG-001",
                    title="Logging",
                    rationale="Apply logging rules.",
                    citation_ids=["BACKEND-1"],
                )
            ],
        )
    )
    stages = []
    seen_budgets = []

    def wrap(owner, method, stage) -> None:
        """Record a stage, then optionally stop the shared budget after valid work."""
        original = getattr(owner, method)

        def invoke(*args, **kwargs):
            """Execute the real fixture behavior before injecting exactly one stop condition."""
            stages.append(stage)
            seen_budgets.append(current_budget())
            result = original(*args, **kwargs)
            if stop_stage == stage:
                if stop_kind == "deadline":
                    clock.now = 10
                else:
                    budget.cancelled.set()
            return result

        monkeypatch.setattr(owner, method, invoke)

    for owner, method, stage in [
        (embedder, "embed_query", "embedding"),
        (index, "search", "index"),
        (reranker, "rerank", "reranking"),
        (compiler, "compile_policy_packet", "compilation"),
        (generator, "generate_preflight", "generation"),
    ]:
        wrap(owner, method, stage)
    service = PreflightService(
        embedder=embedder,
        index_store=index,
        reranker=reranker,
        compiler=compiler,
        generator=generator,
    )
    request = PreflightRequest(task="refresh token cleanup")
    if stop_stage is None:
        with operation_scope(budget):
            assert not service.preflight(request).insufficient_context
        assert stages == ["embedding", "index", "reranking", "compilation", "generation"]
    else:
        with pytest.raises(ProviderError) as caught, operation_scope(budget):
            service.preflight(request)
        assert caught.value.failure_class == (
            "deadline_exhausted" if stop_kind == "deadline" else "cancelled"
        )
        assert stages[-1] == stop_stage
        assert "generation" not in stages
    assert seen_budgets and all(seen is budget for seen in seen_budgets)


@pytest.mark.parametrize("stop_after", [None, "generation", "conformance"])
def test_regeneration_conformance_and_retries_share_deadline(monkeypatch, stop_after) -> None:
    """A stopped generation or conformance call can't start another judge or generation."""
    from test_policy_regeneration_service import (
        MockCompilerService,
        MockConformanceService,
        MockGenerator,
        make_chunk,
        make_compiled_packet,
        make_conformance_result,
        make_draft,
    )

    from policynim.operations import OperationBudget, current_budget, operation_scope
    from policynim.services.regeneration import PolicyRegenerationService
    from policynim.types import PreflightRegenerationRequest

    clock = Clock()
    budget = OperationBudget(1, clock=clock)
    generator = MockGenerator([make_draft(), make_draft()])
    conformance = MockConformanceService(
        [make_conformance_result(passed=False), make_conformance_result(passed=True)]
    )
    seen = []

    def wrap(owner, method, stage):
        """Let a valid stage complete, then exhaust the inherited deadline."""
        original = getattr(owner, method)

        def invoke(*args, **kwargs):
            """Record shared budget identity and optionally exhaust it."""
            assert current_budget() is budget
            seen.append(stage)
            result = original(*args, **kwargs)
            if stage == stop_after:
                clock.now = 1
            return result

        monkeypatch.setattr(owner, method, invoke)

    wrap(generator, "generate_preflight", "generation")
    wrap(conformance, "evaluate", "conformance")
    service = PolicyRegenerationService(
        compiler_service=MockCompilerService(packet=make_compiled_packet(), context=[make_chunk()]),
        generator=generator,
        conformance_service=conformance,
    )
    request = PreflightRegenerationRequest(task="fix backend logging", max_regenerations=1)
    if stop_after is None:
        with operation_scope(budget):
            assert service.regenerate(request).passed
        assert seen == ["generation", "conformance", "generation", "conformance"]
    else:
        with pytest.raises(ProviderError) as caught, operation_scope(budget):
            service.regenerate(request)
        assert caught.value.failure_class == "deadline_exhausted"
        assert seen == (
            ["generation"] if stop_after == "generation" else ["generation", "conformance"]
        )


@pytest.mark.parametrize("kind", ["timeout", "connection"])
def test_real_sdk_transport_failures_have_only_configured_attempts(monkeypatch, kind: str) -> None:
    """SDK internal retries stay disabled while bounded adapter retries retain failure classes."""
    from policynim.operations import OperationBudget, operation_scope

    clock = Clock()
    budget = OperationBudget(10, clock=clock)
    monkeypatch.setattr(budget.cancelled, "wait", clock.wait)
    attempts = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Raise the same HTTP failure the SDK receives from a live transport."""
        attempts.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("secret response detail", request=request)
        raise httpx.ConnectError("secret endpoint", request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with OpenAI(
            api_key="test", base_url="https://provider.invalid", http_client=http, max_retries=0
        ) as client:
            adapter = NVIDIAEmbedder(
                api_key="test",
                model="test",
                base_url="https://provider.invalid",
                batch_size=2,
                timeout_seconds=1,
                max_retries=2,
                client=client,
            )
            with pytest.raises(ProviderError) as caught, operation_scope(budget):
                adapter.embed_query("private")
    assert len(attempts) == 3
    assert len(clock.waits) == 2
    assert caught.value.failure_class == kind
    assert "secret" not in str(caught.value)


def test_injected_sdk_retries_dont_multiply_adapter_attempts(monkeypatch) -> None:
    """An injected SDK with default retries can't bypass the adapter's attempt budget."""
    from policynim.operations import OperationBudget, operation_scope

    clock = Clock()
    budget = OperationBudget(10, clock=clock)
    monkeypatch.setattr(budget.cancelled, "wait", clock.wait)
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Return retryable failures through a default-configured SDK."""
        requests.append(request)
        return httpx.Response(503, json={})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with OpenAI(
            api_key="test", base_url="https://provider.invalid", http_client=http
        ) as client:
            assert client.max_retries == 2
            adapter = NVIDIAEmbedder(
                api_key="test",
                model="test",
                base_url="https://provider.invalid",
                batch_size=2,
                timeout_seconds=1,
                max_retries=1,
                client=client,
            )
            with pytest.raises(ProviderError), operation_scope(budget):
                adapter.embed_query("private")
            assert client.max_retries == 2
            adapter.close()
            assert not client.is_closed()
    assert len(requests) == 2
