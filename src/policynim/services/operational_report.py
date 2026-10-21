"""Read-only operational summaries for the local hosted operator."""

from __future__ import annotations

import sqlite3

from policynim.errors import PolicyNIMError
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.inventory import inspect_policy_inventory
from policynim.settings import Settings
from policynim.storage.auth_database import AuthDatabaseError
from policynim.storage.operation_store import OperationStore


def inspect_operational_report(settings: Settings) -> dict[str, object]:
    """Inspect bounded aggregate evidence without creating state or calling providers."""
    try:
        report = OperationStore.report(
            resolve_runtime_path(settings.beta_auth_db_path.parent)
            / settings.beta_auth_db_path.name
        )
    except AuthDatabaseError as exc:
        raise PolicyNIMError(str(exc) + " No database was created or changed.") from None
    except (sqlite3.Error, OSError) as exc:
        raise PolicyNIMError(
            "Operation state couldn't be read. Check the hosted state volume and database locks. "
            "No database was created or changed."
        ) from exc
    inventory = inspect_policy_inventory(settings)
    readiness = inventory.index
    return {
        **report.model_dump(mode="json"),
        "index_state": "invalid" if inventory.generation_state == "invalid" else readiness.state,
        "generation_state": inventory.generation_state,
        "index_build_id": readiness.identity.build_id if readiness.identity else None,
        "indexed_chunks": readiness.chunk_count,
        "active_generation": inventory.generation_id,
        "retention_days": settings.mcp_operation_retention_days,
        "global_active_limit": settings.mcp_max_concurrent_operations,
        "principal_active_limit": settings.mcp_max_concurrent_operations_per_principal,
        "provider_telemetry_source": "policynim.operations structured stage/attempt logs",
        "job_telemetry_status": "unavailable_until_durable_job_integration",
    }
