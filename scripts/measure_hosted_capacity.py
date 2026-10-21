"""Measure real-process MCP HTTP admission/retrieval with synthetic providers only."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import httpx
from mcp.types import CallToolResult, JSONRPCError, JSONRPCResponse, ListToolsResult

from policynim.contracts import Embedder
from policynim.types import HealthCheckResult, SearchResult

if TYPE_CHECKING:
    from policynim.settings import Settings


def _bounded_count(minimum: int, maximum: int) -> Callable[[str], int]:
    """Validate bounded experiment sizes without expanding every integer in help text."""

    def parse(value: str) -> int:
        """Reject out-of-range values before any fixture process or network work."""
        number = int(value)
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"Must be between {minimum} and {maximum}.")
        return number

    return parse


def _parser() -> argparse.ArgumentParser:
    """Keep the experiment bounded and its synthetic settings explicit."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Destination JSON evidence report"
    )
    parser.add_argument("--users", type=_bounded_count(1, 25), default=25)
    parser.add_argument("--chunks", type=_bounded_count(1, 10000), default=10000)
    parser.add_argument("--requests-per-user", type=_bounded_count(1, 10), default=2)
    parser.add_argument("--provider-delay", type=float, default=0.1)
    parser.add_argument("--distribution", choices=["uniform", "skewed"], default="uniform")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=[
            "baseline",
            "saturation",
            "slow-provider",
            "locked-database",
            "disconnect",
            "shutdown",
        ],
        choices=[
            "baseline",
            "saturation",
            "slow-provider",
            "locked-database",
            "disconnect",
            "shutdown",
        ],
    )
    parser.add_argument("--serve-config", type=Path, help=argparse.SUPPRESS)
    return parser


def _vector(domain: int) -> list[float]:
    """Use deterministic orthogonal vectors, not a model-quality approximation."""
    return [float(index == domain) for index in range(4)]


def _prepare_managed_fixture(
    root: Path, config: dict[str, Any], settings: Settings, embedder: Embedder
) -> None:
    """Publish exact synthetic sections through normal source capture and generation sealing."""
    from policynim.services.generations import GenerationStore
    from policynim.types import EmbeddingIdentity

    sources = settings.corpus_dir
    if sources is None:
        raise RuntimeError("Synthetic managed fixture needs its owned source directory.")
    sources.mkdir()
    domains: dict[int, list[int]] = {}
    for index in range(config["chunks"]):
        domain = (
            index % 4
            if config["distribution"] == "uniform"
            else (0 if index % 10 else 1 + (index // 10) % 3)
        )
        domains.setdefault(domain, []).append(index)
    for domain, indices in domains.items():
        content = (
            f"---\npolicy_id: SYNTHETIC-DOMAIN-{domain}\ntitle: Synthetic fixture\n"
            f"domain: domain-{domain}\ndoc_type: policy\n---\n"
        )
        content += "".join(
            f"## Chunk {index}\nSynthetic synthetic-domain-{domain} policy fixture "
            "for retrieval capacity only.\n\n"
            for index in indices
        )
        (sources / f"domain-{domain}.md").write_text(content, encoding="utf-8")
    published = GenerationStore(
        root=settings.generations_dir,
        embedding=EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        ),
    ).build(settings, embedder=embedder)
    if published.manifest.chunk_count != config["chunks"]:
        raise RuntimeError("Synthetic managed fixture has an unexpected chunk count.")
    (root / "fixture-state.json").write_text(
        json.dumps(
            {
                "generation_id": published.generation_id,
                "chunk_count": published.manifest.chunk_count,
                "document_count": published.manifest.document_count,
                "expected_domain_chunk_counts": {
                    f"domain-{domain}": len(indices) for domain, indices in domains.items()
                },
            }
        ),
        encoding="utf-8",
    )


def _serve(config_path: Path) -> None:
    """Run the actual MCP app, SQLite vectors and admission against delayed provider doubles."""
    import resource
    from collections.abc import Sequence

    import uvicorn
    from starlette.types import Message, Receive, Scope, Send

    from policynim.errors import ProviderError
    from policynim.interfaces import mcp
    from policynim.operations import bounded_stage, check_budget
    from policynim.settings import Settings, get_settings
    from policynim.types import EmbeddingIdentity

    config = json.loads(config_path.read_text())
    root = config_path.resolve().parent
    identity = EmbeddingIdentity(
        model="synthetic-orthogonal-4", endpoint="https://synthetic.invalid"
    )
    index_path = root / "index.sqlite3"
    settings_values = {
        "nvidia_api_key": None,
        "nvidia_embed_model": identity.model,
        "nvidia_base_url": identity.endpoint,
        "corpus_dir": root / "sources",
        "generations_dir": root / "generations",
        "generation_id": None,
        "profile": None,
        "runtime_rules_artifact_path": root / "unused-legacy-rules.json",
        "runtime_evidence_db_path": root / "runtime-evidence.sqlite3",
        "eval_workspace_dir": root / "eval-workspace",
        "mcp_require_auth": True,
        "mcp_bearer_tokens": ",".join(config["tokens"]),
        "mcp_host": "127.0.0.1",
        "mcp_port": config["port"],
        "mcp_public_base_url": f"http://127.0.0.1:{config['port']}",
        "mcp_oauth_issuer_url": None,
        "mcp_oauth_client_id": None,
        "mcp_oauth_client_secret": None,
        "mcp_oauth_allow_legacy_keys": False,
        "beta_signup_enabled": False,
        "beta_auth_db_path": root / "auth.sqlite3",
        "index_db_path": index_path,
        "mcp_max_concurrent_operations": 10,
        "mcp_max_concurrent_operations_per_principal": 2,
        "mcp_principal_daily_operation_quota": 500,
        "operation_timeout_seconds": 10,
    }
    # This runs only in the owned server child, never in the load-generator process.
    child_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("POLICYNIM_") and key != "NVIDIA_API_KEY"
    }
    child_home = root / "home"
    child_home.mkdir()
    child_environment.update(
        HOME=str(child_home),
        USERPROFILE=str(child_home),
        XDG_CONFIG_HOME=str(child_home / "config"),
        XDG_DATA_HOME=str(child_home / "data"),
        NVIDIA_API_KEY="",
    )
    for name, value in settings_values.items():
        if value is not None:
            child_environment[f"POLICYNIM_{name.upper()}"] = str(value)
    os.chdir(root)
    os.environ.clear()
    os.environ.update(child_environment)
    Settings.model_config["env_file"] = None
    get_settings.cache_clear()
    settings = get_settings()

    class SyntheticEmbedder:
        """Delay deterministically and keep caller text out of telemetry."""

        @bounded_stage("embed_query")
        def embed_query(self, text: str) -> list[float]:
            """Return the requested synthetic domain's vector after a bounded delay."""
            time.sleep(config["delay"])
            check_budget()
            if text == "injected-provider-failure":
                raise ProviderError("Synthetic provider failure.", failure_class="connection")
            return _vector(int(text.rsplit("-", 1)[-1]))

        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            """Preserve domain allocation through actual parsing/chunking before measurement."""
            vectors = []
            for text in texts:
                match = re.search(r"synthetic-domain-([0-3])", text)
                if match is None:
                    raise RuntimeError("Synthetic source section is missing its domain marker.")
                vectors.append(_vector(int(match[1])))
            return vectors

        def close(self) -> None:
            """The double owns no network resources."""

    _prepare_managed_fixture(root, config, settings, SyntheticEmbedder())
    mcp._configure_hosted_logger()
    application = mcp._build_streamable_http_app(settings)
    emitted = False

    def emit_peak(phase: str) -> None:
        """Capture process maximum RSS before Uvicorn restores the terminating signal."""
        nonlocal emitted
        if emitted:
            return
        emitted = True
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(
            json.dumps(
                {
                    "fixture_peak_rss_bytes": peak if sys.platform == "darwin" else peak * 1024,
                    "fixture_peak_rss_scope": phase,
                }
            ),
            flush=True,
        )

    async def measured_application(scope: Scope, receive: Receive, send: Send) -> None:
        """Observe application shutdown completion without replacing the real lifespan."""

        async def measured_send(message: Message) -> None:
            """Record the process high-water mark after application cleanup finishes."""
            if message["type"] == "lifespan.shutdown.complete":
                emit_peak("process_max_at_application_shutdown")
            await send(message)

        await application(scope, receive, measured_send)

    try:
        with patch(
            "policynim.services.search._create_default_search_components",
            side_effect=lambda settings: (SyntheticEmbedder(), None),
        ):
            uvicorn.run(
                measured_application,
                host="127.0.0.1",
                port=config["port"],
                log_level="warning",
                access_log=False,
                timeout_graceful_shutdown=15,
            )
    finally:
        emit_peak("process_max_at_server_return")


def _sample_rss(process_id: int) -> int | None:
    """Sample POSIX process RSS without reading environment or requiring an optional library."""
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(process_id)],
            text=True,
            capture_output=True,
            timeout=1,
            check=True,
        )
        return int(result.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _percentiles(values: list[float]) -> dict[str, float | None]:
    """Report nearest-rank observed percentiles without manufacturing empty samples."""
    ordered = sorted(values)
    return {
        name: round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3) if ordered else None
        for name, fraction in [("p50", 0.5), ("p95", 0.95), ("p99", 0.99)]
    }


def _server_failure_diagnostics(events: list[dict[str, Any]]) -> dict[str, object]:
    """Retain bounded static categories and observed tool latency without private log payloads."""
    failure_codes = {
        "server_busy",
        "principal_busy",
        "quota_exceeded",
        "admission_unavailable",
        "admission_busy",
        "accounting_incomplete",
        "acceptance_conflict",
        "acceptance_finalized",
        "acceptance_not_found",
        "server_shutting_down",
        "cancelled",
        "deadline_exhausted",
        "timeout",
        "connection",
        "rate_limit",
        "retry_after_limit",
        "budget_exhausted",
        "invalid_response",
        "auth",
        "endpoint_not_found",
        "endpoint_unavailable",
        "shutdown",
        "bad_request",
        "http_status",
        "unexpected",
        "interrupted",
    }
    phases = {"initialization", "reservation", "finish", "beta_auth_initialization"}
    classes = {
        "OperationalError",
        "DatabaseError",
        "IntegrityError",
        "ProgrammingError",
        "AuthDatabaseError",
        "OSError",
        "PermissionError",
        "ValueError",
        "TimeoutError",
    }
    failures: Counter[str] = Counter()
    storage: Counter[tuple[str, str, int | None, int | None]] = Counter()
    latencies: list[float] = []
    for event in events:
        if event.get("event") == "operation.storage_failure":
            phase = event.get("phase")
            category = event.get("exception_class")
            code, number = event.get("sqlite_code"), event.get("os_errno")
            storage[
                (
                    phase if isinstance(phase, str) and phase in phases else "other",
                    category if isinstance(category, str) and category in classes else "OtherError",
                    code if type(code) is int and 0 <= code <= 65535 else None,
                    number if type(number) is int and 0 <= number <= 4095 else None,
                )
            ] += 1
        elif event.get("event") == "mcp.tool":
            failure = event.get("upstream_failure_class")
            if failure is None:
                continue
            failures[
                failure if isinstance(failure, str) and failure in failure_codes else "other"
            ] += 1
            latency = event.get("latency_ms")
            if type(latency) in {int, float} and math.isfinite(latency) and latency >= 0:
                latencies.append(float(latency))
    selected = storage.most_common(16)
    return {
        "tool_failure_counts": dict(failures),
        "tool_failure_latency_ms": _percentiles(latencies),
        "storage_failures": [
            {
                "phase": phase,
                "exception_class": category,
                "sqlite_code": code,
                "os_errno": number,
                "count": count,
            }
            for (phase, category, code, number), count in selected
        ],
        "additional_storage_failure_events": sum(storage.values())
        - sum(count for _, count in selected),
        "database_lock_timing": "not_sampled",
    }


async def _rpc(
    client: httpx.AsyncClient,
    token: str,
    *,
    index: int = 0,
    discovery: bool = False,
    expected_generation_id: str | None = None,
) -> tuple[float, str, int]:
    """Send one actual JSON-RPC request and retain only timing/classification/hit count."""
    method = "tools/list" if discovery else "tools/call"
    domain = index % 4
    params: dict[str, object] = {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    }
    if not discovery:
        params.update(
            name="policy_search",
            arguments={
                "query": f"synthetic-domain-{domain}",
                "domain": f"domain-{domain}",
                "top_k": 5,
            },
        )
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }
    if not discovery:
        headers["Mcp-Name"] = "policy_search"
    started = time.perf_counter()
    try:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": index + 1, "method": method, "params": params},
            headers=headers,
        )
        if response.status_code != 200:
            return (time.perf_counter() - started) * 1000, f"http_{response.status_code}", 0
        envelope = response.json()
        if (
            not isinstance(envelope, dict)
            or envelope.get("id") != index + 1
            or ("error" in envelope and "result" in envelope)
        ):
            return (time.perf_counter() - started) * 1000, "invalid_rpc_response", 0
        if "error" in envelope:
            JSONRPCError.model_validate(envelope)
            return (time.perf_counter() - started) * 1000, "jsonrpc_error", 0
        result = JSONRPCResponse.model_validate(envelope).result
        if discovery:
            ListToolsResult.model_validate(result)
            return (time.perf_counter() - started) * 1000, "ok", 0
        tool_result = CallToolResult.model_validate(result)
        if tool_result.is_error:
            payload = tool_result.model_dump_json()
            code = next(
                (
                    name
                    for name in [
                        "server_busy",
                        "principal_busy",
                        "quota_exceeded",
                        "admission_unavailable",
                        "admission_busy",
                    ]
                    if name in payload
                ),
                "tool_error",
            )
            return (time.perf_counter() - started) * 1000, code, 0
        content = tool_result.structured_content
        if content is None:
            blocks = tool_result.content
            if not blocks or blocks[0].type != "text":
                return (time.perf_counter() - started) * 1000, "invalid_rpc_response", 0
            content = json.loads(blocks[0].text)
        search = SearchResult.model_validate(content)
        if expected_generation_id is not None and search.generation_id != expected_generation_id:
            return (time.perf_counter() - started) * 1000, "generation_identity_mismatch", 0
        return (time.perf_counter() - started) * 1000, "ok", len(search.hits)
    except ValueError:
        return (time.perf_counter() - started) * 1000, "invalid_rpc_response", 0
    except httpx.HTTPError:
        return (time.perf_counter() - started) * 1000, "transport_failure", 0


def _inspect_health(
    response: httpx.Response, fixture: dict[str, Any] | None
) -> tuple[str, HealthCheckResult | None]:
    """Require managed readiness identity/count, leaving unrelated mocked probe tests usable."""
    outcome = f"health_{response.status_code}"
    if response.status_code != 200 or fixture is None:
        return outcome, None
    try:
        health = HealthCheckResult.model_validate(response.json())
    except ValueError:
        return "health_invalid_response", None
    if (
        not health.ready
        or health.generation_id != fixture["generation_id"]
        or health.row_count != fixture["chunk_count"]
    ):
        return "health_identity_mismatch", health
    return outcome, health


def _read_fixture_state(root: Path, chunks: int) -> dict[str, Any]:
    """Read only bounded expected publication fields from the owned child setup receipt."""
    try:
        state_path = root / "fixture-state.json"
        if state_path.stat().st_size > 4096:
            raise ValueError("Oversized synthetic fixture receipt.")
        value = json.loads(state_path.read_text(encoding="utf-8"))
        generation_id = value["generation_id"]
        counts = value["expected_domain_chunk_counts"]
        if (
            not isinstance(generation_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", generation_id) is None
            or type(value["chunk_count"]) is not int
            or value["chunk_count"] != chunks
            or type(value["document_count"]) is not int
            or not isinstance(counts, dict)
            or not 1 <= len(counts) <= 4
            or value["document_count"] != len(counts)
            or any(
                re.fullmatch(r"domain-[0-3]", domain) is None
                or type(count) is not int
                or not 1 <= count <= chunks
                for domain, count in counts.items()
            )
            or sum(counts.values()) != chunks
        ):
            raise ValueError("Invalid synthetic fixture receipt.")
        return {
            "generation_id": generation_id,
            "chunk_count": chunks,
            "document_count": value["document_count"],
            "expected_domain_chunk_counts": counts,
        }
    except (ValueError, OSError, KeyError, TypeError):
        raise RuntimeError(
            "Synthetic managed fixture publication identity is unavailable."
        ) from None


async def _measure(
    config: dict[str, Any], root: Path, process: subprocess.Popen[str], scenario: str
) -> dict[str, object]:
    """Measure cheap probes concurrently with bounded expensive application requests."""
    from policynim.storage.operation_store import HostedOperationReport, OperationStore

    base_url = f"http://127.0.0.1:{config['port']}"
    async with httpx.AsyncClient(
        base_url=base_url, timeout=20, trust_env=False, limits=httpx.Limits(max_connections=100)
    ) as client:
        deadline = time.monotonic() + 30
        fixture: dict[str, Any] | None = None
        observed_health_ids: set[str] = set()
        observed_health_counts: Counter[int] = Counter()
        while True:
            try:
                response = await client.get("/healthz")
                if response.status_code == 200:
                    if config.get("fixture_mode") == "managed_generation":
                        fixture = _read_fixture_state(root, config["chunks"])
                        outcome, health = _inspect_health(response, fixture)
                        if outcome != "health_200" or health is None:
                            raise RuntimeError(
                                "Synthetic managed readiness identity doesn't match."
                            )
                        assert health.generation_id is not None
                        observed_health_ids.add(health.generation_id)
                        observed_health_counts[health.row_count] += 1
                    break
            except httpx.HTTPError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Synthetic HTTP server failed its readiness deadline.")
            await asyncio.sleep(0.05)
        expected_generation_id = fixture["generation_id"] if fixture is not None else None
        warmup = await _rpc(
            client, config["tokens"][0], expected_generation_id=expected_generation_id
        )
        if warmup[1] != "ok":
            raise RuntimeError("Synthetic HTTP warmup failed.")
        cheap_latencies: list[float] = []
        health_latencies: list[float] = []
        cheap_outcomes: Counter[str] = Counter()
        active_peak = 0
        sampled_peak_rss = _sample_rss(process.pid)
        stop = asyncio.Event()
        active_probe_observed = asyncio.Event()

        state_failures: Counter[str] = Counter()
        state_observations: Counter[str] = Counter()

        async def observe_state(phase: str, *, attempts: int = 1) -> HostedOperationReport | None:
            """Keep bounded failed reads visible without discarding HTTP measurements."""
            for attempt in range(attempts):
                try:
                    observed = await asyncio.to_thread(OperationStore.report, root / "auth.sqlite3")
                except sqlite3.Error as exc:
                    code = getattr(exc, "sqlite_errorcode", None)
                    busy = isinstance(code, int) and code & 0xFF in {
                        sqlite3.SQLITE_BUSY,
                        sqlite3.SQLITE_LOCKED,
                    }
                    failure = "database_busy" if busy else "database_unavailable"
                    state_failures[phase + ":" + failure] += 1
                    if phase == "probe":
                        cheap_outcomes["state_read_busy" if busy else "state_read_unavailable"] += 1
                    if attempt + 1 < attempts:
                        await asyncio.sleep(0.02)
                else:
                    state_observations[phase] += 1
                    return observed
            return None

        async def probes() -> None:
            """Measure discovery/readiness while expensive operations are active."""
            nonlocal active_peak, sampled_peak_rss
            while not stop.is_set():
                sampled = await asyncio.to_thread(_sample_rss, process.pid)
                if sampled is not None:
                    sampled_peak_rss = max(sampled_peak_rss or 0, sampled)
                before_probe = await observe_state("probe")
                if before_probe is not None:
                    active_peak = max(active_peak, before_probe.active_operations)
                latency, outcome, _ = await _rpc(client, config["tokens"][0], discovery=True)
                cheap_latencies.append(latency)
                cheap_outcomes[outcome] += 1
                start = time.perf_counter()
                health_ok = False
                try:
                    health = await client.get("/healthz")
                except httpx.HTTPError:
                    cheap_outcomes["health_transport_failure"] += 1
                else:
                    health_outcome, observed_health = _inspect_health(health, fixture)
                    cheap_outcomes[health_outcome] += 1
                    health_ok = health_outcome == "health_200"
                    if observed_health is not None:
                        if observed_health.generation_id is not None:
                            observed_health_ids.add(observed_health.generation_id)
                        observed_health_counts[observed_health.row_count] += 1
                health_latencies.append((time.perf_counter() - start) * 1000)
                observed = await observe_state("probe")
                if observed is not None:
                    active_peak = max(active_peak, observed.active_operations)
                if (
                    before_probe is not None
                    and before_probe.active_operations > 0
                    and observed is not None
                    and observed.active_operations > 0
                    and outcome == "ok"
                    and health_ok
                ):
                    active_probe_observed.set()
                await asyncio.sleep(0.01)

        samples: list[tuple[float, str, int]] = []
        probe_task = asyncio.create_task(probes())
        lock: sqlite3.Connection | None = None
        if scenario == "locked-database":
            lock = sqlite3.connect(root / "auth.sqlite3", timeout=0.1)
            lock.execute("BEGIN IMMEDIATE")
        count = config["users"] * config["requests_per_user"]
        attempted = 1 if scenario in {"disconnect", "shutdown"} else count
        concurrency = min(
            attempted,
            config["users"] if scenario == "saturation" else min(10, config["users"]),
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def request(index: int) -> None:
            """Apply the reported client concurrency and rotate synthetic identities."""
            async with semaphore:
                samples.append(
                    await _rpc(
                        client,
                        config["tokens"][index % config["users"]],
                        index=index,
                        expected_generation_id=expected_generation_id,
                    )
                )

        started = time.perf_counter()
        intervention: str | None = None
        drain_observation: str | None = None
        intervention_started_with_active_work: bool | None = None
        active_work_observed_before_intervention: bool | None = None
        intervention_probe_observed: bool | None = None
        client_task_completed_before_intervention: bool | None = None
        try:
            if scenario in {"disconnect", "shutdown"}:
                pending = asyncio.create_task(request(0))
                deadline = time.monotonic() + 5.0
                while True:
                    observed = await observe_state("intervention")
                    if observed is not None:
                        intervention_started_with_active_work = observed.active_operations > 0
                        active_work_observed_before_intervention = bool(
                            active_work_observed_before_intervention
                            or intervention_started_with_active_work
                        )
                        active_peak = max(active_peak, observed.active_operations)
                    intervention_probe_observed = active_probe_observed.is_set()
                    if (
                        intervention_started_with_active_work and intervention_probe_observed
                    ) or pending.done():
                        break
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(0.01)
                observed = await observe_state("intervention_final")
                intervention_started_with_active_work = (
                    observed.active_operations > 0 if observed is not None else None
                )
                if observed is not None:
                    active_work_observed_before_intervention = bool(
                        active_work_observed_before_intervention
                        or intervention_started_with_active_work
                    )
                    active_peak = max(active_peak, observed.active_operations)
                client_task_completed_before_intervention = pending.done()
                if scenario == "disconnect":
                    intervention = (
                        "client_task_cancelled"
                        if pending.cancel()
                        else "client_task_already_completed"
                    )
                    await asyncio.gather(pending, return_exceptions=True)
                    deadline = time.monotonic() + 10.0
                    while True:
                        observed = await observe_state("drain")
                        if observed is not None and observed.active_operations == 0:
                            drain_observation = "terminal_outcome_observed"
                            break
                        if time.monotonic() >= deadline:
                            drain_observation = (
                                "deadline_expired_with_active_work"
                                if observed is not None
                                else "deadline_expired_without_state_observation"
                            )
                            break
                        await asyncio.sleep(0.02)
                else:
                    if intervention_started_with_active_work is None:
                        intervention = "SIGTERM_without_active_work_observation"
                    elif client_task_completed_before_intervention:
                        intervention = "SIGTERM_after_client_task_completed"
                    elif intervention_started_with_active_work:
                        intervention = "SIGTERM_during_active_work"
                    else:
                        intervention = "SIGTERM_without_active_work_at_intervention"
                    process.send_signal(signal.SIGTERM)
                    await asyncio.gather(pending, return_exceptions=True)
            else:
                await asyncio.gather(*(request(index) for index in range(count)))
        finally:
            if lock is not None:
                lock.rollback()
                lock.close()
            stop.set()
            probe_results = await asyncio.gather(probe_task, return_exceptions=True)
            for failure in probe_results:
                if isinstance(failure, BaseException):
                    raise RuntimeError(
                        "Synthetic probe sampling failed: " + type(failure).__name__
                    ) from None
        seconds = time.perf_counter() - started
        report = await observe_state("final", attempts=3)
        workload_observations = state_observations["probe"] + state_observations["intervention"]
        measurement_complete = report is not None and workload_observations > 0
        if scenario in {"baseline", "saturation", "slow-provider"}:
            measurement_complete = measurement_complete and active_probe_observed.is_set()
        if scenario in {"disconnect", "shutdown"}:
            measurement_complete = (
                measurement_complete
                and intervention_started_with_active_work is True
                and intervention_probe_observed is True
                and client_task_completed_before_intervention is False
            )
        if scenario == "disconnect":
            measurement_complete = (
                measurement_complete and drain_observation == "terminal_outcome_observed"
            )
        outcomes = Counter(sample[1] for sample in samples)
        managed_identity_verified = (
            fixture is not None
            and observed_health_ids == {fixture["generation_id"]}
            and set(observed_health_counts) == {fixture["chunk_count"]}
            and not outcomes["generation_identity_mismatch"]
            and not cheap_outcomes["health_identity_mismatch"]
            and not cheap_outcomes["health_invalid_response"]
        )
        if fixture is not None:
            measurement_complete = measurement_complete and managed_identity_verified
        return {
            "measurement_complete": measurement_complete,
            "state_read_failure_counts": dict(state_failures),
            "successful_state_observations": dict(state_observations),
            "scenario": scenario,
            "duration_seconds": round(seconds, 3),
            "attempted_requests": attempted,
            "completed_client_responses": len(samples),
            "outcomes": dict(outcomes),
            "responses_per_second": round(len(samples) / seconds, 3),
            "successful_operations_per_second": round(outcomes["ok"] / seconds, 3),
            "response_latency_ms": _percentiles([sample[0] for sample in samples]),
            "accepted_operation_latency_ms": _percentiles(
                [sample[0] for sample in samples if sample[1] == "ok"]
            ),
            "rejected_response_latency_ms": _percentiles(
                [sample[0] for sample in samples if sample[1] != "ok"]
            ),
            "discovery_latency_ms": _percentiles(cheap_latencies),
            "health_latency_ms": _percentiles(health_latencies),
            "cheap_probe_outcomes": dict(cheap_outcomes),
            "fixture_publication": fixture,
            "managed_identity_verified": managed_identity_verified,
            "observed_health_generation_ids": sorted(observed_health_ids),
            "observed_health_row_counts": dict(observed_health_counts),
            "successful_search_identity_validation": (
                "published_generation" if fixture is not None else "not_exercised"
            ),
            "paired_active_work_probe_observed": active_probe_observed.is_set(),
            "observed_peak_active_operations": active_peak if workload_observations else None,
            "sampled_peak_server_rss_bytes": sampled_peak_rss,
            "memory_sampling": "POSIX ps RSS during readiness/discovery probes",
            "successful_search_hit_counts": dict(
                Counter(str(sample[2]) for sample in samples if sample[1] == "ok")
            ),
            "operator_report": report.model_dump(mode="json") if report is not None else None,
            "warmup_operations": 1,
            "intervention": intervention,
            "drain_observation": drain_observation,
            "intervention_started_with_active_work": intervention_started_with_active_work,
            "active_work_observed_before_intervention": active_work_observed_before_intervention,
            "intervention_probe_observed": intervention_probe_observed,
            "client_task_completed_before_intervention": client_task_completed_before_intervention,
            "client_concurrency": concurrency,
        }


def main() -> int:
    """Write evidence labeled as synthetic; never connect to a production host/provider."""
    args = _parser().parse_args()
    if args.serve_config:
        _serve(args.serve_config)
        return 0
    if not 0 <= args.provider_delay <= 2:
        raise SystemExit("--provider-delay must be between 0 and 2 seconds.")
    repository = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=repository, text=True)
    )
    scenarios = []
    for scenario in args.scenarios:
        with tempfile.TemporaryDirectory(prefix="policynim-synthetic-capacity-") as directory:
            root = Path(directory)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            config = {
                "port": port,
                "users": args.users,
                "chunks": args.chunks,
                "requests_per_user": args.requests_per_user,
                "distribution": args.distribution,
                "fixture_mode": "managed_generation",
                "delay": max(args.provider_delay, 0.5)
                if scenario in {"slow-provider", "disconnect", "shutdown"}
                else args.provider_delay,
                "tokens": [f"synthetic-user-{index}" for index in range(args.users)],
            }
            config_path = root / "fixture.json"
            config_path.write_text(json.dumps(config))
            with (root / "server.log").open("w+") as logs:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--output",
                        str(args.output),
                        "--serve-config",
                        str(config_path),
                    ],
                    cwd=repository,
                    stdout=logs,
                    stderr=logs,
                    text=True,
                )
                try:
                    result = asyncio.run(_measure(config, root, process, scenario))
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        raise RuntimeError("Synthetic server failed bounded shutdown.") from None
                logs.seek(0)
                events = []
                for line in logs:
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):
                            events.append(value)
                    except ValueError:
                        pass
                result["peak_server_rss_bytes"] = next(
                    (
                        item["fixture_peak_rss_bytes"]
                        for item in events
                        if "fixture_peak_rss_bytes" in item
                    ),
                    None,
                )
                result["peak_server_rss_scope"] = next(
                    (
                        item["fixture_peak_rss_scope"]
                        for item in events
                        if "fixture_peak_rss_scope" in item
                    ),
                    None,
                )
                result["provider_delay_seconds"] = config["delay"]
                result["server_exit_code"] = process.returncode
                result["provider_stage_event_count"] = sum(
                    item.get("event") == "provider.operation" for item in events
                )
                result["provider_stage_counts"] = dict(
                    Counter(
                        item.get("stage")
                        for item in events
                        if item.get("event") == "provider.operation" and item.get("kind") == "stage"
                    )
                )
                result["server_failure_diagnostics"] = _server_failure_diagnostics(events)
                result["live_provider_usage"] = None
                scenarios.append(result)
    measurement_complete = all(item["measurement_complete"] for item in scenarios)
    report = {
        "measurement_complete": measurement_complete,
        "evidence_kind": "synthetic_provider_real_http_and_sqlite_vec",
        "production_capacity_verified": False,
        "live_nvidia_calls": 0,
        "source_revision": revision,
        "working_tree_dirty": dirty,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "configuration": {
            "users": args.users,
            "chunks": args.chunks,
            "distribution": args.distribution,
            "embedding_dimensions": 4,
            "embedding_model": "synthetic-orthogonal-4",
            "index_scope": "managed_generation",
            "tool": "policy_search",
            "reranker": None,
            "global_active_limit": 10,
            "principal_active_limit": 2,
        },
        "scenarios": scenarios,
        "limits": [
            "Only search is measured; other tools require separate workload evidence.",
            "Synthetic vectors don't measure model quality or NVIDIA latency.",
            "Short local runs don't establish a production capacity envelope.",
            "Disconnect/server-shutdown outcomes depend on observed worker drain.",
            "A managed generation is published before measurement; concurrent publication, "
            "retention and durable job backlog aren't exercised.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "report": str(args.output.resolve()),
                "scenarios": len(scenarios),
                "measurement_complete": measurement_complete,
                "production_capacity_verified": False,
            }
        )
    )
    return 0 if measurement_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
