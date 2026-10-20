"""Measure real-process MCP HTTP admission/retrieval with synthetic providers only."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
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
from typing import Any

import httpx


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


def _serve(config_path: Path) -> None:
    """Run the actual MCP app, SQLite vectors and admission against delayed provider doubles."""
    import resource
    from collections.abc import Sequence

    import uvicorn
    from starlette.types import Message, Receive, Scope, Send

    from policynim.errors import ProviderError
    from policynim.interfaces import mcp
    from policynim.operations import bounded_stage, check_budget
    from policynim.services.health import RuntimeHealthService
    from policynim.services.search import SearchService
    from policynim.settings import Settings
    from policynim.storage.sqlite_vec import SQLiteVecIndexStore
    from policynim.types import EmbeddedChunk, EmbeddingIdentity, PolicyMetadata

    config = json.loads(config_path.read_text())
    root = config_path.parent
    identity = EmbeddingIdentity(
        model="synthetic-orthogonal-4", endpoint="https://synthetic.invalid"
    )
    index_path = root / "index.sqlite3"
    store = SQLiteVecIndexStore(path=index_path, embedding_identity=identity)
    chunks = []
    for index in range(config["chunks"]):
        domain = (
            index % 4
            if config["distribution"] == "uniform"
            else (0 if index % 10 else 1 + (index // 10) % 3)
        )
        chunks.append(
            EmbeddedChunk(
                chunk_id=f"synthetic-{index}",
                path=f"synthetic/domain-{domain}.md",
                section="Fixture",
                lines="1-1",
                text="Synthetic policy fixture for retrieval capacity only.",
                policy=PolicyMetadata(
                    policy_id=f"SYNTHETIC-{index}",
                    title="Synthetic fixture",
                    domain=f"domain-{domain}",
                    doc_type="policy",
                ),
                vector=_vector(domain),
            )
        )
    store.replace(chunks)
    settings = Settings.model_validate(
        {
            "mcp_require_auth": True,
            "mcp_bearer_tokens": config["tokens"],
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
    )

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
            """Document vectors are generated before the HTTP measurement."""
            return [_vector(0) for _ in texts]

        def close(self) -> None:
            """The double owns no network resources."""

    def search_service(*args: Any, **kwargs: Any) -> SearchService:
        """Use real SQLite retrieval with an operation-local provider double."""
        return SearchService(
            embedder=SyntheticEmbedder(),
            index_store=SQLiteVecIndexStore(path=index_path, embedding_identity=identity),
        )

    mcp.create_search_service = search_service
    mcp.create_runtime_health_service = lambda settings: RuntimeHealthService(
        index_store=SQLiteVecIndexStore(path=index_path, embedding_identity=identity),
        table_name=store.table_name,
        mcp_url=None,
    )
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


async def _rpc(
    client: httpx.AsyncClient, token: str, *, index: int = 0, discovery: bool = False
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
        result = response.json().get("result", {})
        if result.get("isError"):
            payload = json.dumps(result)
            code = next(
                (
                    name
                    for name in [
                        "server_busy",
                        "principal_busy",
                        "quota_exceeded",
                        "admission_unavailable",
                    ]
                    if name in payload
                ),
                "tool_error",
            )
            return (time.perf_counter() - started) * 1000, code, 0
        content = result.get("structuredContent")
        if not isinstance(content, dict):
            blocks = result.get("content", [])
            content = json.loads(blocks[0]["text"]) if blocks else {}
        return (time.perf_counter() - started) * 1000, "ok", len(content.get("hits", []))
    except httpx.HTTPError:
        return (time.perf_counter() - started) * 1000, "transport_failure", 0


async def _measure(
    config: dict[str, Any], root: Path, process: subprocess.Popen[str], scenario: str
) -> dict[str, object]:
    """Measure cheap probes concurrently with bounded expensive application requests."""
    from policynim.storage.operation_store import OperationStore

    base_url = f"http://127.0.0.1:{config['port']}"
    async with httpx.AsyncClient(
        base_url=base_url, timeout=20, trust_env=False, limits=httpx.Limits(max_connections=100)
    ) as client:
        deadline = time.monotonic() + 30
        while True:
            try:
                response = await client.get("/healthz")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Synthetic HTTP server failed its readiness deadline.")
            await asyncio.sleep(0.05)
        warmup = await _rpc(client, config["tokens"][0])
        if warmup[1] != "ok":
            raise RuntimeError("Synthetic HTTP warmup failed.")
        cheap_latencies: list[float] = []
        health_latencies: list[float] = []
        cheap_outcomes: Counter[str] = Counter()
        active_peak = 0
        sampled_peak_rss = _sample_rss(process.pid)
        stop = asyncio.Event()

        async def probes() -> None:
            """Measure discovery/readiness while expensive operations are active."""
            nonlocal active_peak, sampled_peak_rss
            while not stop.is_set():
                sampled = await asyncio.to_thread(_sample_rss, process.pid)
                if sampled is not None:
                    sampled_peak_rss = max(sampled_peak_rss or 0, sampled)
                latency, outcome, _ = await _rpc(client, config["tokens"][0], discovery=True)
                cheap_latencies.append(latency)
                cheap_outcomes[outcome] += 1
                start = time.perf_counter()
                try:
                    health = await client.get("/healthz")
                except httpx.HTTPError:
                    cheap_outcomes["health_transport_failure"] += 1
                else:
                    cheap_outcomes[f"health_{health.status_code}"] += 1
                health_latencies.append((time.perf_counter() - start) * 1000)
                try:
                    active_peak = max(
                        active_peak, OperationStore.report(root / "auth.sqlite3").active_operations
                    )
                except sqlite3.Error:
                    cheap_outcomes["state_read_busy"] += 1
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
                    await _rpc(client, config["tokens"][index % config["users"]], index=index)
                )

        started = time.perf_counter()
        intervention: str | None = None
        drain_observation: str | None = None
        intervention_started_with_active_work: bool | None = None
        try:
            if scenario in {"disconnect", "shutdown"}:
                pending = asyncio.create_task(request(0))
                deadline = time.monotonic() + 5.0
                while True:
                    observed = OperationStore.report(root / "auth.sqlite3")
                    intervention_started_with_active_work = observed.active_operations > 0
                    active_peak = max(active_peak, observed.active_operations)
                    if intervention_started_with_active_work or pending.done():
                        break
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(0.01)
                if scenario == "disconnect":
                    intervention = (
                        "client_task_cancelled"
                        if pending.cancel()
                        else "client_task_already_completed"
                    )
                    await asyncio.gather(pending, return_exceptions=True)
                    deadline = time.monotonic() + 10.0
                    while True:
                        observed = OperationStore.report(root / "auth.sqlite3")
                        if observed.active_operations == 0:
                            drain_observation = "terminal_outcome_observed"
                            break
                        if time.monotonic() >= deadline:
                            drain_observation = "deadline_expired_with_active_work"
                            break
                        await asyncio.sleep(0.02)
                else:
                    intervention = (
                        "SIGTERM_during_active_work"
                        if intervention_started_with_active_work
                        else "SIGTERM_before_active_work_observed"
                    )
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
        report = OperationStore.report(root / "auth.sqlite3")
        outcomes = Counter(sample[1] for sample in samples)
        return {
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
            "observed_peak_active_operations": active_peak,
            "sampled_peak_server_rss_bytes": sampled_peak_rss,
            "memory_sampling": "POSIX ps RSS during readiness/discovery probes",
            "successful_search_hit_counts": dict(
                Counter(str(sample[2]) for sample in samples if sample[1] == "ok")
            ),
            "operator_report": report.model_dump(mode="json"),
            "warmup_operations": 1,
            "intervention": intervention,
            "drain_observation": drain_observation,
            "intervention_started_with_active_work": intervention_started_with_active_work,
            "client_concurrency": concurrency,
        }


def main() -> None:
    """Write evidence labeled as synthetic; never connect to a production host/provider."""
    args = _parser().parse_args()
    if args.serve_config:
        _serve(args.serve_config)
        return
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
                result["live_provider_usage"] = None
                scenarios.append(result)
    report = {
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
            "Generation publication and durable job backlog aren't exercised.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "report": str(args.output.resolve()),
                "scenarios": len(scenarios),
                "production_capacity_verified": False,
            }
        )
    )


if __name__ == "__main__":
    main()
