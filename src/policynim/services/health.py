"""Runtime health service for hosted HTTP readiness checks."""

from __future__ import annotations

import logging

from policynim.contracts import IndexStore
from policynim.runtime_paths import resolve_runtime_path
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


def _derive_mcp_url(settings: Settings) -> str | None:
    if settings.mcp_public_base_url is None:
        return None
    return str(settings.mcp_public_base_url).rstrip("/") + "/mcp"
