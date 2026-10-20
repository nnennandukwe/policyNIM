"""Request-scoped deadlines, cooperative cancellation, and content-free stage events."""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import Event
from types import TracebackType
from typing import Any, Literal, ParamSpec, TypeVar

from policynim.errors import ProviderError

_LOG = logging.getLogger("policynim.operations")
_P = ParamSpec("_P")
_R = TypeVar("_R")


_CORRELATION: ContextVar[str | None] = ContextVar("policynim_correlation", default=None)


def current_correlation_id() -> str | None:
    """Read an internally generated operation ID rather than a client-supplied label."""
    return _CORRELATION.get()


@contextmanager
def operation_correlation() -> Iterator[str]:
    """Correlate HTTP/worker/provider events without retaining request content."""
    identity = uuid.uuid4().hex
    token = _CORRELATION.set(identity)
    try:
        yield identity
    finally:
        _CORRELATION.reset(token)


class OperationBudget:
    """Share one monotonic deadline and cancellation event across nested stages."""

    def __init__(
        self,
        seconds: float = 120.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        cancelled: Event | None = None,
        max_provider_calls: int | None = None,
        usage_callback: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        """Create a finite budget without starting any worker or network request."""
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Operation timeout must be finite and positive.")
        self.clock = clock
        self.deadline = clock() + seconds
        self.cancelled = cancelled if cancelled is not None else Event()
        if max_provider_calls is not None and max_provider_calls < 1:
            raise ValueError("Provider-call budgets must be positive.")
        self.max_provider_calls = max_provider_calls
        self.provider_attempts = 0
        self.measured_tokens = 0
        self.unavailable_usage_attempts = 0
        self._usage_callback = usage_callback

    def reserve_provider_call(self) -> None:
        """Persist an attempt reservation before dispatch; never exceed the accepted limit."""
        self.remaining()
        if (
            self.max_provider_calls is not None
            and self.provider_attempts >= self.max_provider_calls
        ):
            raise ProviderError(
                "The accepted provider-call budget is exhausted.", failure_class="budget_exhausted"
            )
        self.provider_attempts += 1
        self.unavailable_usage_attempts += 1
        self._record_usage()

    def record_provider_usage(self, total_tokens: int | None) -> None:
        """Keep returned measured usage separate from attempts with unknown upstream cost."""
        if total_tokens is not None:
            self.measured_tokens += total_tokens
            self.unavailable_usage_attempts -= 1
            self._record_usage()

    def _record_usage(self) -> None:
        """Make durable job evidence available before another provider attempt starts."""
        if self._usage_callback is not None:
            self._usage_callback(
                {
                    "provider_attempts": self.provider_attempts,
                    "measured_tokens": self.measured_tokens,
                    "unavailable_usage_attempts": self.unavailable_usage_attempts,
                }
            )

    def remaining(self) -> float:
        """Reject cancelled or expired work before it can enter another stage."""
        if self.cancelled.is_set():
            raise ProviderError(
                "Operation cancelled; upstream usage may already exist.", failure_class="cancelled"
            )
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ProviderError(
                "Operation deadline exhausted. Retry later or check the timeout.",
                failure_class="deadline_exhausted",
            )
        return remaining

    def wait(self, seconds: float) -> None:
        """Wait interruptibly, refusing delays that leave no budget for another attempt."""
        if seconds >= self.remaining():
            raise ProviderError(
                "Retry delay exceeds the remaining operation deadline. Retry later.",
                failure_class="deadline_exhausted",
            )
        self.cancelled.wait(seconds)
        self.remaining()


_CURRENT: ContextVar[OperationBudget | None] = ContextVar("policynim_operation", default=None)


@contextmanager
def operation_scope(
    budget: OperationBudget | None = None, *, seconds: float = 120.0
) -> Iterator[OperationBudget]:
    """Inherit the caller's budget; create one only at an operation boundary."""
    active = _CURRENT.get()
    token = _CURRENT.set(active or budget or OperationBudget(seconds))
    try:
        current = _CURRENT.get()
        assert current is not None
        current.remaining()
        yield current
        current.remaining()
    finally:
        _CURRENT.reset(token)


def current_budget() -> OperationBudget:
    """Return the budget inside an operation or stage scope."""
    current = _CURRENT.get()
    if current is None:
        raise RuntimeError("No active operation budget.")
    return current


def check_budget() -> None:
    """Check an active operation without manufacturing a nested deadline."""
    current = _CURRENT.get()
    if current is not None:
        current.remaining()


def emit_operation_event(*, level: int = logging.INFO, **fields: Any) -> None:
    """Emit only internally selected metadata, never requests or upstream bodies."""
    _LOG.log(
        level,
        json.dumps(
            {"event": "provider.operation", "correlation_id": current_correlation_id(), **fields},
            sort_keys=True,
        ),
    )


def bounded_stage(stage: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Wrap a service or provider boundary with inherited deadline checks and timing."""

    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        """Preserve signatures for callers and command/tool registration."""

        @wraps(function)
        def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            """Measure a stage without retaining its arguments or result content."""
            owner = args[0] if args else None
            seconds = getattr(owner, "_operation_timeout_seconds", 120.0)
            start = time.monotonic()
            failure: str | None = None
            try:
                with operation_scope(seconds=seconds):
                    return function(*args, **kwargs)
            except BaseException as exc:
                failure = getattr(exc, "failure_class", None) or type(exc).__name__
                raise
            finally:
                emit_operation_event(
                    kind="stage",
                    stage=stage,
                    latency_ms=round((time.monotonic() - start) * 1000, 3),
                    failure_class=failure,
                )

        return run

    return decorate


def close_components(
    *components: object | None, primary_error: BaseException | None = None
) -> None:
    """Attempt all optional close hooks, then propagate the first cleanup failure."""
    failure: BaseException | None = None
    interruption: BaseException | None = None
    for component in components:
        close = getattr(component, "close", None)
        if callable(close):
            try:
                close()
            except BaseException as exc:
                failure = failure or exc
                if not isinstance(exc, Exception):
                    interruption = interruption or exc
    if interruption is not None:
        if primary_error is not None and not isinstance(primary_error, Exception):
            raise primary_error from interruption
        raise interruption from primary_error
    if failure is not None:
        if primary_error is not None:
            raise primary_error from failure
        raise failure


class ResourceStack(ExitStack):
    """Close partial graphs while retaining the error that caused their teardown."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Attempt every registered close and keep the work failure primary."""
        try:
            super().__exit__(exc_type, exc_value, traceback)
            return False
        except BaseException as cleanup_error:
            if exc_value is not None and not isinstance(exc_value, Exception):
                raise exc_value from cleanup_error
            interruption = _interruption_in_chain(cleanup_error)
            if interruption is not None:
                if interruption is cleanup_error:
                    raise
                raise interruption from cleanup_error
            if exc_value is not None and cleanup_error is not exc_value:
                raise exc_value from cleanup_error
            raise


def _interruption_in_chain(error: BaseException) -> BaseException | None:
    """Find an interruption retained by a later close failure without looping on cycles."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if not isinstance(current, Exception):
            return current
        pending.extend(
            item for item in (current.__cause__, current.__context__) if item is not None
        )
    return None
