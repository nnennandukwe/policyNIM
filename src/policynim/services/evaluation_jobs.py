"""Durable evaluation admission, immutable input capture and one-host worker lifecycle."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from functools import wraps
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from typing import ParamSpec, Protocol, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from policynim.errors import PolicyNIMError, ProviderError
from policynim.job_types import (
    EvaluationJobAuthorization,
    EvaluationJobConfiguration,
    EvaluationJobError,
    EvaluationJobInfo,
    EvaluationJobLimits,
    EvaluationJobSnapshot,
    EvaluationJobState,
    EvaluationJobUsage,
    StoredEvaluationJobReport,
)
from policynim.operations import OperationBudget, operation_correlation, operation_scope
from policynim.providers.lifecycle import NVIDIAClients, borrow_clients
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.authorization import AuthenticatedPrincipal, authorize_operation
from policynim.services.eval import EvalService, validate_suite_generation
from policynim.services.generations import GenerationStore
from policynim.services.operation_admission import (
    OperationAdmissionError,
    OperationAdmissionService,
    principal_identity,
)
from policynim.settings import Settings
from policynim.storage.evaluation_jobs import (
    EvaluationJobStore,
    archive_digest,
    payload_digest,
)
from policynim.storage.file_lock import try_exclusive_file_lock
from policynim.types import CompileRequest, EmbeddingIdentity, EvalCaseResult, EvalSuite

_LOG = logging.getLogger("policynim.jobs")
_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_P = ParamSpec("_P")
_R = TypeVar("_R")


def _job_storage_boundary(operation: Callable[_P, _R]) -> Callable[_P, _R]:
    """Preserve uncertain accepted state while returning bounded storage recovery guidance."""

    @wraps(operation)
    def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        """Keep backend failures separate from a rejected or completed job operation."""
        try:
            return operation(*args, **kwargs)
        except (OSError, sqlite3.Error) as exc:
            raise EvaluationJobError(
                "job_storage_unavailable: The job operation couldn't be confirmed. Preserve "
                "storage and inspect its state after the operator checks the volume. "
                "For submissions, retry the same idempotency key."
            ) from exc

    return run


class CaseEvaluator(Protocol):
    """Keep the worker's model boundary injectable while using the public evaluation API."""

    def evaluate_case(
        self, suite: EvalSuite, case_id: str, *, rerank_enabled: bool = True
    ) -> EvalCaseResult:
        """Evaluate one captured live case against the configured immutable generation."""
        ...

    def close(self) -> None:
        """Release evaluator-owned resources after each job."""
        ...


class _DefaultCaseEvaluator:
    """Own the existing synchronous evaluator without a second scoring implementation."""

    def __init__(self, settings: Settings) -> None:
        """Compose the public evaluator from the captured provider configuration."""
        self.service = EvalService(settings=settings)

    def evaluate_case(
        self, suite: EvalSuite, case_id: str, *, rerank_enabled: bool = True
    ) -> EvalCaseResult:
        """Run one validated live case without producing another report artifact."""
        return self.service.evaluate_case(suite, case_id, rerank_enabled=rerank_enabled)

    def close(self) -> None:
        """The base evaluator holds no clients between its public case calls."""
        return None


def _configuration(settings: Settings, rerank_enabled: bool) -> EvaluationJobConfiguration:
    """Capture only the provider names, endpoints and bounded execution settings."""
    names = set(EvaluationJobConfiguration.model_fields) - {"provider", "rerank_enabled"}
    values = {name: getattr(settings, name) for name in names}
    return EvaluationJobConfiguration(**values, rerank_enabled=rerank_enabled)


def _authorization(principal: AuthenticatedPrincipal) -> EvaluationJobAuthorization:
    """Persist accepted claims without retaining an access token or NVIDIA key."""
    return EvaluationJobAuthorization(
        issuer=principal.issuer,
        subject=principal.subject,
        client_id=principal.client_id,
        scopes=sorted(principal.scopes),
        source=principal.source,
        expires_at=principal.expires_at,
        credential_id=principal.credential_id,
    )


def _principal(snapshot: EvaluationJobAuthorization) -> AuthenticatedPrincipal:
    """Reconstruct bounded accepted authority for worker admission and revocation checks."""
    return AuthenticatedPrincipal(
        issuer=snapshot.issuer,
        subject=snapshot.subject,
        client_id=snapshot.client_id,
        scopes=frozenset(snapshot.scopes),
        source=snapshot.source,
        expires_at=snapshot.expires_at,
        credential_id=snapshot.credential_id,
    )


class EvaluationJobService:
    """Own caller authority and accept durable snapshots before any provider execution."""

    @_job_storage_boundary
    def __init__(self, settings: Settings, *, store: EvaluationJobStore | None = None) -> None:
        """Use the existing evaluation workspace for private, restart-stable job storage."""
        self.settings = settings.model_copy(deep=True)
        self.store = store or EvaluationJobStore(
            resolve_runtime_path(settings.eval_workspace_dir) / "jobs" / "jobs.sqlite3"
        )

    def _owner(self, principal: AuthenticatedPrincipal, operation: str) -> str:
        """Authorize every operation before storage can disclose caller-specific evidence."""
        authorize_operation(principal, operation)
        return principal_identity(principal.issuer, principal.subject)

    @_job_storage_boundary
    def submit(
        self,
        suite: EvalSuite,
        *,
        idempotency_key: str,
        principal: AuthenticatedPrincipal,
        limits: EvaluationJobLimits | None = None,
        rerank_enabled: bool = True,
    ) -> EvaluationJobInfo:
        """Accept the suite and complete generation archive atomically, with no model calls."""
        owner = self._owner(principal, "eval_submit")
        if not _KEY_RE.fullmatch(idempotency_key):
            raise EvaluationJobError(
                "invalid_idempotency_key: Use 1-128 ASCII letters, digits or ._:-."
            )
        limits = limits or EvaluationJobLimits()
        suite = EvalSuite.model_validate_json(suite.model_dump_json())
        if (
            len(suite.cases) > limits.max_cases
            or len(suite.model_dump_json().encode()) > 1024 * 1024
        ):
            raise EvaluationJobError(
                "suite_too_large: Reduce the case count or suite payload before retrying."
            )
        try:
            for case in suite.cases:
                if len(case.case_id.encode("utf-8")) > 256:
                    raise EvaluationJobError(
                        "invalid_case_id: Limit each case ID to 256 UTF-8 bytes."
                    )
                CompileRequest(task=case.input, domain=case.domain, top_k=case.top_k)
        except ValidationError as exc:
            raise EvaluationJobError(
                "invalid_case_request: Provide a nonblank case input and domain, "
                "with top_k between 1 and 20."
            ) from exc
        configuration = _configuration(self.settings, rerank_enabled)
        request_digest = payload_digest(
            {
                "suite": suite.model_dump(mode="json"),
                "configuration": configuration.model_dump(mode="json"),
                "limits": limits.model_dump(mode="json"),
                "requested_generation_id": self.settings.generation_id,
            }
        )
        existing = self.store.find_submission(owner, idempotency_key, request_digest)
        if existing is not None:
            return existing
        capture_lock = self.store.path.with_name(
            self.store.path.name + "." + owner + ".submit.lock"
        )
        descriptor = os.open(
            capture_lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        os.close(descriptor)
        with try_exclusive_file_lock(capture_lock) as acquired:
            if not acquired:
                raise EvaluationJobError(
                    "principal_busy: Another submission is capturing this principal's inputs. "
                    "Retry later with the same idempotency key."
                )
            existing = self.store.find_submission(owner, idempotency_key, request_digest)
            if existing is not None:
                return existing
            self.store.ensure_capacity(owner)
            generation_store = GenerationStore(
                root=resolve_runtime_path(self.settings.generations_dir),
                embedding=EmbeddingIdentity(
                    model=configuration.nvidia_embed_model, endpoint=configuration.nvidia_base_url
                ),
            )
            with generation_store.pin(self.settings.generation_id) as snapshot:
                validate_suite_generation(suite, snapshot)
                with TemporaryDirectory(
                    prefix="job-capture-", dir=self.store.path.parent
                ) as directory:
                    path = Path(directory) / "generation.zip"
                    digest = generation_store.export_bundle(path, snapshot.generation_id)
                    archive = path.read_bytes()
                captured = EvaluationJobSnapshot(
                    generation_id=snapshot.generation_id,
                    generation_archive_digest=digest,
                    suite_digest=payload_digest(suite.model_dump(mode="json")),
                    configuration_digest=payload_digest(configuration.model_dump(mode="json")),
                    suite=suite,
                    configuration=configuration,
                    authorization=_authorization(principal),
                    limits=limits,
                )
            return self.store.submit(
                owner_id=owner,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                snapshot=captured,
                archive=archive,
            )

    @_job_storage_boundary
    def inspect(self, job_id: str, *, principal: AuthenticatedPrincipal) -> EvaluationJobInfo:
        """Inspect the accepted generation and progress rather than current policy sources."""
        return self.store.inspect(job_id, self._owner(principal, "eval_inspect"))

    @_job_storage_boundary
    def cancel(self, job_id: str, *, principal: AuthenticatedPrincipal) -> EvaluationJobInfo:
        """Keep cancellation requests separate from drained terminal cancellation."""
        return self.store.cancel(job_id, self._owner(principal, "eval_cancel"))

    @_job_storage_boundary
    def result(
        self, job_id: str, *, principal: AuthenticatedPrincipal
    ) -> StoredEvaluationJobReport:
        """Return only the caller's verified report during its retained lifetime."""
        return self.store.result(job_id, self._owner(principal, "eval_result"))


class _CancellationSignal(Event):
    """Observe cancellation and shutdown across processes at every provider budget check."""

    def __init__(self, store: EvaluationJobStore, job_id: str, stop: Event) -> None:
        """Bind one accepted job to its persistent cancellation flag and worker stop event."""
        super().__init__()
        self.store, self.job_id, self.stop = store, job_id, stop

    def is_set(self) -> bool:
        """Don't let a request continue after the persisted caller cancellation becomes visible."""
        return (
            super().is_set() or self.stop.is_set() or self.store.cancellation_requested(self.job_id)
        )

    def wait(self, timeout: float | None = None) -> bool:
        """Keep provider retry waits responsive to cancellation from a different process."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            super().wait(0.1 if remaining is None else min(remaining, 0.1))
        return True


class EvaluationJobWorker:
    """Run one local worker; kernel lock ownership survives PID reuse and process interruption."""

    def __init__(
        self,
        service: EvaluationJobService,
        *,
        evaluator_factory: Callable[[Settings], CaseEvaluator] = _DefaultCaseEvaluator,
    ) -> None:
        """Create no thread or provider work until start() or run_once() is invoked."""
        self.service, self.store = service, service.store
        self.evaluator_factory = evaluator_factory
        self._stop, self._lock = Event(), Lock()
        self._admission_paused = Event()
        self._thread: Thread | None = None
        self.worker_token = uuid4().hex
        self.lock_path = self.store.path.with_suffix(".worker.lock")
        descriptor = os.open(
            self.lock_path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        os.close(descriptor)

    def start(self) -> None:
        """Start an owned background worker that doesn't depend on an MCP request connection."""
        with self._lock:
            self._require_admission_ready()
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stop.is_set():
                raise EvaluationJobError(
                    "worker_closed: Create a new worker before accepting more work."
                )
            self._thread = Thread(
                target=self._loop, name="policynim-evaluation-worker", daemon=True
            )
            self._thread.start()

    def close(self) -> None:
        """Stop admitting cases and drain active provider work before releasing clients."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join()

    def _loop(self) -> None:
        """Recover only after acquiring ownership and keep storage failures observable."""
        while not self._stop.is_set() and not self._admission_paused.is_set():
            try:
                result = self.run_once()
                self.store.purge_expired()
                if result is None:
                    self._stop.wait(0.5)
            except Exception as exc:
                # No request, provider body, caller identity or source text is logged.
                _LOG.error("Evaluation worker needs attention: %s", type(exc).__name__)
                self._stop.wait(1)

    def _require_admission_ready(self) -> None:
        """Keep queued jobs untouched after this worker loses a confirmed accounting outcome."""
        if self._admission_paused.is_set():
            raise EvaluationJobError(
                "accounting_incomplete: This worker has paused after an unconfirmed quota write. "
                "Preserve storage, inspect accounting and restart the hosted process."
            )

    @_job_storage_boundary
    def run_once(self) -> EvaluationJobInfo | None:
        """Execute at most one accepted job while holding the stable worker kernel lock."""
        self._require_admission_ready()
        with try_exclusive_file_lock(self.lock_path) as acquired:
            if not acquired:
                raise EvaluationJobError("worker_busy: Another worker owns this job database.")
            self.store.recover_interrupted()
            if self._stop.is_set():
                return None
            job = self.store.claim(self.worker_token)
            if job is None or job.state != "running":
                return job
            return self._execute(job)

    def _execute(self, job: EvaluationJobInfo) -> EvaluationJobInfo:
        """Keep input restoration, admission, provider work and cleanup in one owned lifecycle."""
        signal = _CancellationSignal(self.store, job.job_id, self._stop)
        try:
            snapshot, archive = self.store.captured_inputs(job.job_id, self.worker_token)
            settings = self._restore(snapshot, archive)
            remaining = job.deadline_at - self.store.clock()
            if remaining <= 0:
                return self.store.finish(
                    job.job_id,
                    self.worker_token,
                    "deadline_exceeded",
                    failure_code="deadline_exhausted",
                )

            def record_usage(values: dict[str, int]) -> None:
                """Persist reservations and measured usage before another upstream effect."""
                attempts = values["provider_attempts"]
                missing = values["unavailable_usage_attempts"]
                usage_status = (
                    "unavailable"
                    if missing == attempts
                    else "measured"
                    if missing == 0
                    else "partially_measured"
                )
                self.store.save_usage(
                    job.job_id,
                    self.worker_token,
                    EvaluationJobUsage(**values, usage_status=usage_status),
                )

            budget = OperationBudget(
                remaining,
                cancelled=signal,
                max_provider_calls=snapshot.limits.max_provider_calls,
                usage_callback=record_usage,
            )

            def run_owned() -> None:
                """Hold shared quota admission through every case and provider cleanup."""
                clients = NVIDIAClients()
                try:
                    with borrow_clients(clients), operation_scope(budget):
                        with closing(self.evaluator_factory(settings)) as evaluator:
                            for case in snapshot.suite.cases:
                                budget.remaining()
                                self.store.begin_case(job.job_id, self.worker_token, case.case_id)
                                result = evaluator.evaluate_case(
                                    snapshot.suite,
                                    case.case_id,
                                    rerank_enabled=snapshot.configuration.rerank_enabled,
                                )
                                self.store.save_case(job.job_id, self.worker_token, result)
                                budget.remaining()
                finally:
                    clients.close()

            admission = OperationAdmissionService(self.service.settings)
            try:
                with operation_correlation():
                    admission.run(
                        run_owned,
                        principal=_principal(snapshot.authorization),
                        operation_name="eval_submit",
                        cancelled=signal.is_set,
                        check_ready=budget.remaining,
                    )
            finally:
                admission.store.close()
            return self.store.finish(job.job_id, self.worker_token, "completed")
        except Exception as exc:
            if self._stop.is_set():
                state: EvaluationJobState = "interrupted"
                failure = "worker_stopped"
            elif isinstance(exc, ProviderError):
                failure = exc.failure_class or "provider_failure"
                states: dict[str, EvaluationJobState] = {
                    "cancelled": "cancelled",
                    "deadline_exhausted": "deadline_exceeded",
                    "budget_exhausted": "budget_exhausted",
                }
                state = states.get(failure, "failed")
            elif isinstance(exc, (OSError, sqlite3.Error)):
                state, failure = "interrupted", "storage_failure"
            elif isinstance(exc, OperationAdmissionError):
                state, failure = "failed", str(exc).partition(":")[0]
                if failure == "accounting_incomplete":
                    self._admission_paused.set()
            elif isinstance(exc, PolicyNIMError):
                state, failure = (
                    "failed",
                    getattr(exc, "code", None) or exc.failure_class or type(exc).__name__,
                )
            else:
                state, failure = "failed", "evaluation_failure"
            return self.store.finish(
                job.job_id, self.worker_token, state, failure_code=failure, uncertain=True
            )

    def _restore(self, snapshot: EvaluationJobSnapshot, archive: bytes) -> Settings:
        """Restore the accepted archive without reading current policy sources."""
        if archive_digest(archive) != snapshot.generation_archive_digest:
            raise EvaluationJobError("archive_invalid: Preserve job storage for inspection.")
        values = snapshot.configuration.model_dump(exclude={"provider", "rerank_enabled"})
        settings = self.service.settings.model_copy(
            update={
                **values,
                "generations_dir": self.store.path.parent / "worker-generations",
                "generation_id": snapshot.generation_id,
                "profile": None,
            },
            deep=True,
        )
        generations = GenerationStore(
            root=settings.generations_dir,
            embedding=EmbeddingIdentity(
                model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
            ),
        )
        with TemporaryDirectory(prefix="job-restore-", dir=self.store.path.parent) as directory:
            path = Path(directory) / "generation.zip"
            path.write_bytes(archive)
            restored = generations.import_bundle(
                path, expected_digest=snapshot.generation_archive_digest
            )
        if restored.generation_id != snapshot.generation_id:
            raise EvaluationJobError(
                "generation_mismatch: Restored inputs don't match the accepted job."
            )
        return settings
