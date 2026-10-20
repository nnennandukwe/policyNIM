"""Read-only operational summaries for the local hosted operator."""

from __future__ import annotations

import sqlite3

from policynim.errors import PolicyNIMError
from policynim.providers.lifecycle import close_components
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.index_store import create_index_store
from policynim.settings import Settings
from policynim.storage.index_readiness import inspect_index_readiness
from policynim.storage.operation_store import OperationStore


def inspect_operational_report(settings: Settings) -> dict[str, object]:
    """Inspect bounded aggregate evidence without creating state or calling providers."""
    try:
        report = OperationStore.report(resolve_runtime_path(settings.beta_auth_db_path))
    except (sqlite3.Error, OSError) as exc:
        raise PolicyNIMError(
            "Operation state couldn't be read. Check the hosted state volume and database locks. "
            "No database was created or changed."
        ) from exc
    index_store = create_index_store(settings)
    try:
        readiness = inspect_index_readiness(index_store)
    finally:
        close_components(index_store)
    return {
        **report.model_dump(mode="json"),
        "index_state": readiness.state,
        "index_build_id": readiness.identity.build_id if readiness.identity else None,
        "indexed_chunks": readiness.chunk_count,
        "active_generation": None,
        "retention_days": settings.mcp_operation_retention_days,
        "global_active_limit": settings.mcp_max_concurrent_operations,
        "principal_active_limit": settings.mcp_max_concurrent_operations_per_principal,
        "provider_telemetry_source": "policynim.operations structured stage/attempt logs",
        "job_telemetry_status": "unavailable_until_durable_job_integration",
    }
