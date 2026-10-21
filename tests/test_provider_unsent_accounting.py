"""Real loopback HTTP evidence for known-unsent provider attempt corrections."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from openai import OpenAI

from policynim.errors import ProviderError
from policynim.operations import OperationBudget, operation_scope
from policynim.providers.nvidia import NVIDIAEmbedder


@contextmanager
def provider_server(*, retry_first: bool) -> Iterator[tuple[str, list[str]]]:
    """Serve actual HTTP responses without contacting NVIDIA or retaining input bodies."""
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        """Return one retryable failure or a small valid embedding response."""

        def do_POST(self) -> None:
            """Consume the request and record only the endpoint path."""
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            failed = retry_first and len(calls) == 1
            payload = (
                {"error": {"message": "fixture busy"}}
                if failed
                else {
                    "object": "list",
                    "model": "fixture",
                    "usage": {"prompt_tokens": 7, "total_tokens": 7},
                    "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
                }
            )
            encoded = json.dumps(payload).encode()
            self.send_response(503 if failed else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            """Suppress request logging in this offline fixture."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def invoke(base_url: str, budget: OperationBudget) -> list[float]:
    """Use the real SDK and adapter with a caller-owned loopback HTTP client."""
    with OpenAI(api_key="fixture", base_url=base_url, max_retries=0) as client:
        adapter = NVIDIAEmbedder(
            api_key="fixture",
            model="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            batch_size=32,
            max_retries=1,
            timeout_seconds=1,
            retry_initial_seconds=0.001,
            retry_max_seconds=0.001,
        )
        with operation_scope(budget):
            return adapter.embed_query("fixture")


@pytest.mark.parametrize("failure_class", ["cancelled", "deadline_exhausted"])
@pytest.mark.parametrize("reservation_number", [1, 2])
def test_reservation_rejected_before_send_doesnt_claim_provider_usage(
    tmp_path: Path,
    failure_class: str,
    reservation_number: int,
) -> None:
    """Cancellation or expiry during durable reservation corrects only the known-unsent call."""
    cancelled = Event()
    clock = [0.0]
    injected = False
    snapshots: list[dict[str, int]] = []
    with sqlite3.connect(tmp_path / "usage.sqlite3") as database:
        database.execute("CREATE TABLE usage (snapshot TEXT)")
        database.execute("INSERT INTO usage VALUES ('{}')")
        database.commit()

        def persist(snapshot: dict[str, int]) -> None:
            """Commit actual usage evidence, then expire or cancel before HTTP dispatch."""
            nonlocal injected
            snapshots.append(snapshot)
            database.execute("UPDATE usage SET snapshot=?", (json.dumps(snapshot),))
            database.commit()
            if snapshot["provider_attempts"] == reservation_number and not injected:
                injected = True
                if failure_class == "cancelled":
                    cancelled.set()
                else:
                    clock[0] = 10.0

        budget = OperationBudget(
            5,
            clock=lambda: clock[0],
            cancelled=cancelled,
            max_provider_calls=2,
            usage_callback=persist,
        )
        with provider_server(retry_first=reservation_number == 2) as (base_url, calls):
            with pytest.raises(ProviderError) as caught:
                invoke(base_url, budget)
            assert caught.value.failure_class == failure_class
            assert len(calls) == reservation_number - 1
        expected = {
            "provider_attempts": reservation_number - 1,
            "measured_tokens": 0,
            "unavailable_usage_attempts": reservation_number - 1,
        }
        assert snapshots[-1] == expected
        assert json.loads(database.execute("SELECT snapshot FROM usage").fetchone()[0]) == expected
        assert budget.provider_attempts == reservation_number - 1


@pytest.mark.parametrize("retry_first", [False, True])
def test_sent_calls_keep_measured_and_unknown_usage_separate(retry_first: bool) -> None:
    """A real sent retry remains unknown while the successful response supplies measured tokens."""
    snapshots: list[dict[str, int]] = []
    budget = OperationBudget(5, max_provider_calls=2, usage_callback=snapshots.append)
    with provider_server(retry_first=retry_first) as (base_url, calls):
        assert invoke(base_url, budget) == [1.0, 0.0]
        assert len(calls) == (2 if retry_first else 1)
    assert snapshots[-1] == {
        "provider_attempts": 2 if retry_first else 1,
        "measured_tokens": 7,
        "unavailable_usage_attempts": 1 if retry_first else 0,
    }


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_reservation_persistence_never_sends_and_corrects_counters(
    interrupted: bool,
) -> None:
    """A callback failure after its write also corrects the known-unsent reservation."""
    snapshots: list[dict[str, int]] = []
    failure = KeyboardInterrupt() if interrupted else sqlite3.OperationalError("fixture failure")

    def persist(snapshot: dict[str, int]) -> None:
        """Model an uncertain write result followed by successful compensation."""
        snapshots.append(snapshot)
        if len(snapshots) == 1:
            raise failure

    budget = OperationBudget(5, max_provider_calls=2, usage_callback=persist)
    with provider_server(retry_first=False) as (base_url, calls):
        with pytest.raises(type(failure)) as caught:
            invoke(base_url, budget)
        assert caught.value is failure
        assert calls == []
    assert snapshots[-1] == {
        "provider_attempts": 0,
        "measured_tokens": 0,
        "unavailable_usage_attempts": 0,
    }
    assert budget.provider_attempts == 0
