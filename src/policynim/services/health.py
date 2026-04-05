"""Runtime health service for hosted HTTP readiness checks."""

from __future__ import annotations

import logging

from policynim.contracts import IndexStore
from policynim.errors import ConfigurationError
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.ingest import create_ingest_service
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
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
            if not self._index_store.exists():
                return self._not_ready(f"Local index table {self._table_name!r} does not exist.")

            row_count = self._index_store.count()
            if row_count <= 0:
                return self._not_ready(
                    f"Local index table {self._table_name!r} exists but contains no rows."
                )

            return HealthCheckResult(
                status="ok",
                ready=True,
                table_name=self._table_name,
                row_count=row_count,
                mcp_url=self._mcp_url,
                reason=None,
            )
        except Exception:
            LOGGER.exception("Runtime health check failed.")
            return self._not_ready("Local index readiness could not be inspected.")

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
    index_uri = resolve_runtime_path(active_settings.lancedb_uri)
    return RuntimeHealthService(
        index_store=LanceDBIndexStore(
            uri=index_uri,
            table_name=active_settings.lancedb_table,
        ),
        table_name=active_settings.lancedb_table,
        mcp_url=_derive_mcp_url(active_settings),
    )


def ensure_hosted_runtime_ready(
    settings: Settings | None = None,
    *,
    rebuild_if_missing: bool = False,
) -> None:
    """Fail fast when hosted HTTP startup points at a missing or empty local index."""
    active_settings = settings or get_settings()
    index_uri = resolve_runtime_path(active_settings.lancedb_uri)

    result = _check_hosted_runtime_health(active_settings, index_uri=index_uri)
    if result.ready:
        return

    if rebuild_if_missing:
        _rebuild_hosted_runtime_index(active_settings, index_uri=index_uri, reason=result.reason)
        result = _check_hosted_runtime_health(active_settings, index_uri=index_uri)
        if result.ready:
            return

        reason = result.reason or "Local index readiness could not be inspected after rebuild."
        raise ConfigurationError(
            _format_hosted_runtime_error(
                index_uri=index_uri,
                table_name=active_settings.lancedb_table,
                reason=reason,
            )
        )

    reason = result.reason or "Local index readiness could not be inspected."
    raise ConfigurationError(
        _format_hosted_runtime_error(
            index_uri=index_uri,
            table_name=active_settings.lancedb_table,
            reason=reason,
        )
    )


def _check_hosted_runtime_health(
    settings: Settings,
    *,
    index_uri,
) -> HealthCheckResult:
    try:
        return create_runtime_health_service(settings).check()
    except Exception as exc:
        reason = f"Local index readiness could not be inspected: {type(exc).__name__}: {exc}."
        raise ConfigurationError(
            _format_hosted_runtime_error(
                index_uri=index_uri,
                table_name=settings.lancedb_table,
                reason=reason,
            )
        ) from exc


def _rebuild_hosted_runtime_index(
    settings: Settings,
    *,
    index_uri,
    reason: str | None,
) -> None:
    summary = reason or "Local index readiness could not be inspected."
    LOGGER.warning(
        "Hosted runtime index at %s is not ready. Rebuilding before serving traffic. Reason: %s",
        index_uri,
        summary,
    )
    try:
        result = create_ingest_service(settings).run()
    except Exception as exc:
        rebuild_reason = f"Automatic hosted-index rebuild failed: {type(exc).__name__}: {exc}."
        raise ConfigurationError(
            _format_hosted_runtime_error(
                index_uri=index_uri,
                table_name=settings.lancedb_table,
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


def _format_hosted_runtime_error(*, index_uri: str, table_name: str, reason: str) -> str:
    return (
        "Hosted streamable-http startup requires a populated local index at "
        f"{index_uri} (table: {table_name}). "
        f"{reason} Rebuild the image so `policynim ingest` runs during Docker build "
        "or set `POLICYNIM_LANCEDB_URI` to a populated directory."
    )
