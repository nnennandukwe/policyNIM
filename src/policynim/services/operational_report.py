"""Read-only operational summaries for the local hosted operator."""

from __future__ import annotations

import sqlite3

from policynim.errors import GenerationError, PolicyNIMError
from policynim.providers.lifecycle import close_components
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store
from policynim.settings import Settings
from policynim.storage.evaluation_jobs import inspect_job_backlog
from policynim.storage.index_readiness import IndexReadinessReport, inspect_index_readiness
from policynim.storage.operation_store import OperationStore
from policynim.types import EmbeddingIdentity


def inspect_operational_report(settings: Settings) -> dict[str, object]:
    """Inspect bounded aggregate evidence without creating state or calling providers."""
    try:
        report = OperationStore.report(resolve_runtime_path(settings.beta_auth_db_path))
    except (sqlite3.Error, OSError) as exc:
        raise PolicyNIMError(
            "Operation state couldn't be read. Check the hosted state volume and database locks. "
            "No database was created or changed."
        ) from exc
    readiness, active_generation = _generation_readiness(settings)
    try:
        backlog = inspect_job_backlog(
            resolve_runtime_path(settings.eval_workspace_dir) / "jobs" / "jobs.sqlite3"
        )
    except (sqlite3.Error, OSError) as exc:
        raise PolicyNIMError(
            "Job state couldn't be read. Preserve the evaluation workspace and check volume access."
        ) from exc
    return {
        **report.model_dump(mode="json"),
        "index_state": readiness.state,
        "index_build_id": readiness.identity.build_id if readiness.identity else None,
        "indexed_chunks": readiness.chunk_count,
        "active_generation": active_generation,
        "job_backlog": backlog,
        "retention_days": settings.mcp_operation_retention_days,
        "global_active_limit": settings.mcp_max_concurrent_operations,
        "principal_active_limit": settings.mcp_max_concurrent_operations_per_principal,
        "provider_telemetry_source": "policynim.operations structured stage/attempt logs",
        "job_telemetry_status": "available" if backlog is not None else "unavailable_no_job_store",
    }


def _generation_readiness(settings: Settings) -> tuple[IndexReadinessReport, str | None]:
    """Report the actual active generation while keeping damaged storage unavailable."""
    root = resolve_runtime_path(settings.generations_dir)
    if not root.exists() and not root.is_symlink():
        return IndexReadinessReport(state="missing"), None
    try:
        generations = GenerationStore(
            root=root,
            embedding=EmbeddingIdentity(
                model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
            ),
        )
        if generations.read_activation() is None:
            return IndexReadinessReport(state="missing"), None
        active_settings = settings.model_copy(update={"generation_id": None})
        index_store = create_index_store(active_settings)
        try:
            readiness = inspect_index_readiness(index_store)
            return readiness, index_store.generation_id if readiness.state == "ready" else None
        finally:
            close_components(index_store)
    except GenerationError:
        return IndexReadinessReport(state="invalid"), None
    except OSError:
        return IndexReadinessReport(state="unreadable"), None
