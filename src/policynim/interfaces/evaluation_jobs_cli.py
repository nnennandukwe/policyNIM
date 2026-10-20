"""Local durable evaluation commands with JSON output and explicit worker ownership."""

from __future__ import annotations

import getpass
import os
import sqlite3
import time
from pathlib import Path
from typing import Annotated, TypeVar

import typer
from pydantic import BaseModel, ValidationError

from policynim.errors import PolicyNIMError
from policynim.job_types import EvaluationJobLimits
from policynim.services.authorization import AuthenticatedPrincipal
from policynim.services.evaluation_jobs import EvaluationJobService, EvaluationJobWorker
from policynim.settings import get_settings
from policynim.types import EvalSuite

job_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Submit, inspect and run durable generation-pinned evaluations. Outputs JSON.",
)
_Result = TypeVar("_Result", bound=BaseModel)


def local_job_principal() -> AuthenticatedPrincipal:
    """Identify the current local operator without taking an arbitrary caller-supplied owner."""
    identity = str(os.getuid()) if hasattr(os, "getuid") else getpass.getuser()
    return AuthenticatedPrincipal(
        issuer="urn:policynim:local",
        subject=identity,
        client_id="policynim-cli",
        scopes=frozenset({"policies:read", "policies:evaluate"}),
        source="operator_key",
    )


def _service() -> EvaluationJobService:
    """Compose the local job service from the existing runtime configuration."""
    return EvaluationJobService(get_settings())


def _failure(error: Exception) -> None:
    """Keep local failures off stdout and return a nonzero command status."""
    message = (
        "job_storage_unavailable: The job operation couldn't be confirmed. "
        "Preserve storage and inspect its state after checking the volume. "
        "Retry submissions with the same idempotency key."
        if isinstance(error, sqlite3.Error)
        else str(error)
    )
    typer.echo(message, err=True)
    raise typer.Exit(1) from error


@job_app.command("submit")
def submit_job(
    suite: Annotated[
        Path, typer.Argument(help="Path to a catalog/revision-pinned EvalSuite JSON file.")
    ],
    idempotency_key: Annotated[
        str, typer.Option("--idempotency-key", help="Stable key for this exact submission.")
    ],
    deadline_seconds: Annotated[
        float, typer.Option("--deadline-seconds", min=0.001, max=86400)
    ] = 3600,
    max_provider_calls: Annotated[
        int, typer.Option("--max-provider-calls", min=1, max=10000)
    ] = 512,
    max_cases: Annotated[int, typer.Option("--max-cases", min=1, max=1000)] = 100,
    retention_seconds: Annotated[
        float, typer.Option("--retention-seconds", min=60, max=2592000)
    ] = 604800,
    rerank: Annotated[bool, typer.Option("--rerank/--no-rerank")] = True,
) -> None:
    """Accept immutable inputs; run `eval-jobs worker` separately to execute them."""
    try:
        captured = EvalSuite.model_validate_json(suite.read_text(encoding="utf-8"))
        result = _service().submit(
            captured,
            idempotency_key=idempotency_key,
            principal=local_job_principal(),
            limits=EvaluationJobLimits(
                deadline_seconds=deadline_seconds,
                max_provider_calls=max_provider_calls,
                max_cases=max_cases,
                retention_seconds=retention_seconds,
            ),
            rerank_enabled=rerank,
        )
        typer.echo(result.model_dump_json(indent=2))
    except (PolicyNIMError, OSError, ValidationError, ValueError) as exc:
        _failure(exc)


@job_app.command("inspect")
def inspect_job(job_id: str) -> None:
    """Read the accepted generation, progress and final-state identity."""
    try:
        typer.echo(
            _service().inspect(job_id, principal=local_job_principal()).model_dump_json(indent=2)
        )
    except (PolicyNIMError, OSError, sqlite3.Error) as exc:
        _failure(exc)


@job_app.command("cancel")
def cancel_job(job_id: str) -> None:
    """Request cancellation; active provider work drains before terminal cancellation."""
    try:
        typer.echo(
            _service().cancel(job_id, principal=local_job_principal()).model_dump_json(indent=2)
        )
    except (PolicyNIMError, OSError, sqlite3.Error) as exc:
        _failure(exc)


@job_app.command("result")
def job_result(job_id: str) -> None:
    """Retrieve the retained terminal report and its canonical JSON digest."""
    try:
        typer.echo(
            _service().result(job_id, principal=local_job_principal()).model_dump_json(indent=2)
        )
    except (PolicyNIMError, OSError, sqlite3.Error) as exc:
        _failure(exc)


@job_app.command("worker")
def run_worker(
    once: Annotated[
        bool, typer.Option("--once", help="Run at most one queued job and exit.")
    ] = False,
) -> None:
    """Run the single supported worker for this local database; Ctrl-C drains active work."""
    worker: EvaluationJobWorker | None = None
    try:
        worker = EvaluationJobWorker(_service())
        if once:
            result = worker.run_once()
            typer.echo(
                result.model_dump_json(indent=2) if result is not None else '{"state":"idle"}'
            )
            return
        worker.start()
        typer.echo(
            "Evaluation worker running. Ctrl-C stops new cases and drains active work.", err=True
        )
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return
    except (PolicyNIMError, OSError, sqlite3.Error) as exc:
        _failure(exc)
    finally:
        if worker is not None:
            worker.close()


@job_app.command("prune")
def prune_jobs() -> None:
    """Delete expired report/input payloads while preserving job summaries and digests."""
    try:
        count = _service().store.purge_expired()
        typer.echo('{"expired_reports_removed":' + str(count) + "}")
    except (PolicyNIMError, OSError, sqlite3.Error) as exc:
        _failure(exc)
