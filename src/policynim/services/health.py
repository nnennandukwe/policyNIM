"""Runtime health service for hosted HTTP readiness checks."""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from policynim.contracts import IndexStore
from policynim.errors import ConfigurationError, IndexCompatibilityError
from policynim.services.ingest import create_ingest_service
from policynim.settings import Settings, get_settings
from policynim.storage import create_index_store
from policynim.storage.index_readiness import inspect_index_readiness
from policynim.types import HealthCheckResult

LOGGER = logging.getLogger(__name__)


class RuntimeHealthService:
    """Inspect local runtime readiness without calling NVIDIA."""

    def __init__(
        self,
        *,
        index_store: IndexStore,
        table_name: str,
        mcp_url: str | None,
    ) -> None:
        self._index_store = index_store
        self._table_name = table_name
        self._mcp_url = mcp_url

    def check(self) -> HealthCheckResult:
        """Return a readiness payload for the hosted HTTP runtime."""
        try:
            readiness = inspect_index_readiness(self._index_store)
            if readiness.state == "missing":
                return self._not_ready(f"Local index table {self._table_name!r} does not exist.")
            if readiness.state == "empty":
                return self._not_ready(
                    f"Local index table {self._table_name!r} exists but contains no rows."
                )
            if readiness.state != "ready":
                return self._not_ready(readiness.reason)
            return HealthCheckResult(
                status="ok",
                ready=True,
                table_name=self._table_name,
                row_count=readiness.chunk_count or 0,
                mcp_url=self._mcp_url,
                reason=None,
            )
        except IndexCompatibilityError:
            return self._not_ready(
                "Local index embedding identity is incompatible or ingestion is incomplete."
            )
        except Exception as exc:
            log_health_failure("Runtime health check failed.", exc)
            return self._not_ready(format_health_failure_reason(exc))

    def _not_ready(self, reason: str) -> HealthCheckResult:
        return HealthCheckResult(
            status="error",
            ready=False,
            table_name=self._table_name,
            row_count=0,
            mcp_url=self._mcp_url,
            reason=reason,
        )


def create_runtime_health_service(settings: Settings | None = None) -> RuntimeHealthService:
    """Build the default runtime health service from application settings."""
    active_settings = settings or get_settings()
    index_store = create_index_store(active_settings)
    return RuntimeHealthService(
        index_store=index_store,
        table_name=index_store.table_name,
        mcp_url=_derive_mcp_url(active_settings),
    )


def ensure_hosted_runtime_ready(
    settings: Settings | None = None,
    *,
    rebuild_if_missing: bool = False,
) -> None:
    """Fail fast when hosted HTTP startup points at a missing or empty local index."""
    active_settings = settings or get_settings()
    index_store = create_index_store(active_settings)

    result = _check_hosted_runtime_health(
        active_settings,
        index_path=index_store.path,
        table_name=index_store.table_name,
    )
    if result.ready:
        return

    index_exists = index_store.path.exists()
    if rebuild_if_missing and index_exists:
        # Existing unknown/mismatched data must never trigger a paid implicit migration.
        index_store.validate_identity()

    if rebuild_if_missing and not index_exists:
        _rebuild_hosted_runtime_index(
            active_settings,
            index_path=index_store.path,
            table_name=index_store.table_name,
            reason=result.reason,
        )
        result = _check_hosted_runtime_health(
            active_settings,
            index_path=index_store.path,
            table_name=index_store.table_name,
        )
        if result.ready:
            return

        reason = result.reason or "Local index readiness could not be inspected after rebuild."
        raise ConfigurationError(
            _format_hosted_runtime_error(
                index_path=index_store.path,
                table_name=index_store.table_name,
                reason=reason,
            )
        )

    reason = result.reason or "Local index readiness could not be inspected."
    raise ConfigurationError(
        _format_hosted_runtime_error(
            index_path=index_store.path,
            table_name=index_store.table_name,
            reason=reason,
        )
    )


def _check_hosted_runtime_health(
    settings: Settings,
    *,
    index_path: Path,
    table_name: str,
) -> HealthCheckResult:
    """Inspect hosted runtime readiness and wrap constructor failures."""
    try:
        return create_runtime_health_service(settings).check()
    except Exception as exc:
        reason = format_health_failure_reason(exc)
        raise ConfigurationError(
            _format_hosted_runtime_error(
                index_path=index_path,
                table_name=table_name,
                reason=reason,
            )
        ) from exc


def _rebuild_hosted_runtime_index(
    settings: Settings,
    *,
    index_path: Path,
    table_name: str,
    reason: str | None,
) -> None:
    """Rebuild the local hosted-runtime index and report controlled failures."""
    summary = reason or "Local index readiness could not be inspected."
    LOGGER.warning(
        "Hosted runtime index at %s is not ready. Rebuilding before serving traffic. Reason: %s",
        index_path,
        summary,
    )
    try:
        with create_ingest_service(settings) as service:
            result = service.run()
    except Exception as exc:
        rebuild_reason = f"Automatic hosted-index rebuild failed: {type(exc).__name__}: {exc}."
        raise ConfigurationError(
            _format_hosted_runtime_error(
                index_path=index_path,
                table_name=table_name,
                reason=rebuild_reason,
            )
        ) from exc

    LOGGER.info(
        "Hosted runtime index rebuilt at %s with %s chunks across %s documents.",
        result.index_uri,
        result.chunk_count,
        result.document_count,
    )


def _derive_mcp_url(settings: Settings) -> str | None:
    if settings.mcp_public_base_url is None:
        return None
    return str(settings.mcp_public_base_url).rstrip("/") + "/mcp"


def format_health_failure_reason(exc: Exception) -> str:
    """Return only an error class and numeric errno, never untrusted exception text."""
    detail = type(exc).__name__
    if isinstance(exc, OSError) and isinstance(exc.errno, int):
        detail += f" (errno {exc.errno})"
    return f"Local index readiness couldn't be inspected: {detail}."


def log_health_failure(message: str, exc: Exception) -> None:
    """Keep diagnostic call sites while excluding source lines and exception payloads."""
    locations = " -> ".join(
        f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
    )
    LOGGER.error(
        "%s %s Traceback locations: %s",
        message,
        format_health_failure_reason(exc),
        locations or "unavailable",
    )


def _format_hosted_runtime_error(*, index_path: Path | str, table_name: str, reason: str) -> str:
    """Format hosted startup recovery guidance for operators."""
    index_path_text = str(index_path)
    return (
        "Hosted streamable-http startup requires a populated local SQLite index at "
        f"{index_path_text} (table: {table_name}). "
        f"{reason} Run `policynim ingest` before serving traffic, or bake that command "
        "during Docker build. Configure the path with `POLICYNIM_INDEX_DB_PATH`; "
        "`POLICYNIM_LANCEDB_URI` is only a deprecated alias for that path."
    )
