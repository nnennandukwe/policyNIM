"""Cancellation-state failures through real worker, provider and SQLite boundaries."""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from test_evaluation_jobs import case_result, job_setup, principal
from test_evaluation_jobs import generation as generation_fixture

from policynim.providers.nvidia import NVIDIAEmbedder, OpenAI, httpx
from policynim.services.evaluation_jobs import EvaluationJobWorker

generation = generation_fixture


def test_cancellation_storage_failure_after_real_provider_response(
    generation, tmp_path, monkeypatch
):
    """A returned provider response can't turn a cancellation storage fault into provider blame."""
    service, suite, _ = job_setup(generation, tmp_path)
    first = service.submit(suite, idempotency_key="after-response", principal=principal())
    second = service.submit(suite, idempotency_key="following-job", principal=principal())
    deny = [False]
    denied = []
    requests = []
    connect = service.store._connect

    def connection_with_denial():
        """Deny one real cancellation column read after the first response is sent."""
        connection = connect()

        def authorize(action, table, column, database, trigger):
            """Keep accounting and terminal writes available after the read failure."""
            if (
                deny[0]
                and action == sqlite3.SQLITE_READ
                and table == "evaluation_jobs"
                and column == "cancel_requested"
            ):
                deny[0] = False
                denied.append(True)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    class Handler(BaseHTTPRequestHandler):
        """Serve one deterministic real HTTP embedding response per accepted job."""

        def do_POST(self):
            """Arm the read denial only after upstream dispatch has reached the local server."""
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append(self.path)
            if not denied:
                deny[0] = True
            payload = json.dumps(
                {
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "model": "fixture",
                    "usage": {"prompt_tokens": 9, "total_tokens": 9},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            """Keep the fixture free of query or request-header logs."""
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class Evaluator:
        """Replace scoring only; retain the actual SDK, transport, worker and accounting."""

        def __init__(self, settings):
            """Own the provider client for the duration of this accepted job."""
            self.settings = settings
            self.client = OpenAI(
                api_key="synthetic-key",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                max_retries=0,
                http_client=httpx.Client(trust_env=False),
            )
            self.embedder = NVIDIAEmbedder(
                api_key="synthetic-key",
                model="fixture",
                base_url="https://example.invalid/v1",
                batch_size=1,
                timeout_seconds=2,
                max_retries=2,
                client=self.client,
            )

        def evaluate_case(self, suite, case_id, *, rerank_enabled=True):
            """Read a real local response before constructing a successful typed case."""
            self.embedder.embed_query("non-sensitive fixture query")
            return case_result(self.settings, suite, case_id)

        def close(self):
            """Drain the owned client before terminal accounting."""
            self.embedder.close()
            self.client.close()

    monkeypatch.setattr(service.store, "_connect", connection_with_denial)
    worker = EvaluationJobWorker(service, evaluator_factory=Evaluator)
    try:
        result = worker.run_once()
        report = service.result(first.job_id, principal=principal()).report
        following = worker.run_once()
        assert worker.run_once() is None
        assert result is not None and following is not None
        assert result.job_id == first.job_id and following.job_id == second.job_id
        assert len(denied) == 1 and len(requests) == 2
        assert report.usage.provider_attempts == 1 and report.usage.measured_tokens == 9
        assert report.uncertain_case_id == "case-0" and report.completed_cases == []
        assert following.state == "completed"
        assert result.state == "interrupted" and result.failure_code == "storage_failure"
    finally:
        worker.close()
        server.shutdown()
        server.server_close()
        thread.join(3)
