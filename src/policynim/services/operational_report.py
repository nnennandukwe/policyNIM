"""Operational summaries with read-only records and coordinated generation leases."""

from __future__ import annotations

import errno
import sqlite3

from policynim.errors import ConfigurationError, GenerationError, PolicyNIMError
from policynim.job_types import EvaluationJobError
from policynim.operations import close_components
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store, pinned_generation
from policynim.settings import Settings
from policynim.storage.evaluation_jobs import inspect_job_backlog
from policynim.storage.index_readiness import (
    IndexReadinessReport,
    inspect_index_readiness,
    is_sqlite_access_error,
)
from policynim.storage.operation_store import OperationStore
from policynim.types import EmbeddingIdentity


def inspect_operational_report(settings: Settings) -> dict[str, object]:
    """Inspect records without providers; generation validation needs writable lease metadata."""
    try:
        report = OperationStore.report(resolve_runtime_path(settings.beta_auth_db_path))
    except (sqlite3.Error, OSError) as exc:
        raise PolicyNIMError(
            "Operation state couldn't be read. Check the hosted state volume and database locks. "
            "No database was created or changed."
        ) from exc
    readiness, active_generation, served_generation = _generation_readiness(settings)
    telemetry_reason = None
    try:
        backlog = inspect_job_backlog(
            resolve_runtime_path(settings.eval_workspace_dir) / "jobs" / "jobs.sqlite3"
        )
    except EvaluationJobError as exc:
        backlog, telemetry_reason = None, exc.code
    except (sqlite3.Error, OSError):
        backlog, telemetry_reason = None, "job_store_unreadable"
    return {
        **report.model_dump(mode="json"),
        "index_state": readiness.state,
        "generation_state": readiness.state,
        "index_build_id": readiness.identity.build_id if readiness.identity else None,
        "indexed_chunks": readiness.chunk_count,
        "active_generation": active_generation,
        "served_generation": served_generation,
        "generation_selection": "pinned" if settings.generation_id is not None else "active",
        "job_backlog": backlog,
        "retention_days": settings.mcp_operation_retention_days,
        "global_active_limit": settings.mcp_max_concurrent_operations,
        "principal_active_limit": settings.mcp_max_concurrent_operations_per_principal,
        "provider_telemetry_source": "policynim.operations structured stage/attempt logs",
        "job_telemetry_status": (
            "unavailable"
            if telemetry_reason
            else "available"
            if backlog is not None
            else "unavailable_no_job_store"
        ),
        "job_telemetry_reason": telemetry_reason,
    }


def _generation_readiness(
    settings: Settings,
) -> tuple[IndexReadinessReport, str | None, str | None]:
    """Validate configured retrieval while keeping its selection separate from activation."""
    root = resolve_runtime_path(settings.generations_dir)
    try:
        if not root.exists() and not root.is_symlink():
            return IndexReadinessReport(state="missing"), None, None
        try:
            embedding = EmbeddingIdentity(
                model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
            )
        except ValueError as exc:
            raise ConfigurationError(
                "Configure a nonempty POLICYNIM_NVIDIA_EMBED_MODEL and an HTTPS "
                "POLICYNIM_NVIDIA_BASE_URL without credentials, query parameters, or fragments."
            ) from exc
        generations = GenerationStore(root=root, embedding=embedding)
        activation = generations.read_activation()
        if activation is None and settings.generation_id is None:
            return IndexReadinessReport(state="missing"), None, None
        index_store = create_index_store(settings)
        try:
            snapshot = pinned_generation(index_store)
            if snapshot is not None:
                activation = snapshot.activation
            readiness = inspect_index_readiness(index_store)
            return (
                readiness,
                activation.active
                if activation is not None and readiness.state == "ready"
                else None,
                index_store.generation_id if readiness.state == "ready" else None,
            )
        finally:
            close_components(index_store)
    except GenerationError as exc:
        cause: BaseException | None = exc
        unavailable = False
        seen: set[int] = set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if isinstance(cause, sqlite3.Error) and is_sqlite_access_error(cause):
                unavailable = True
            elif isinstance(cause, OSError) and cause.errno in {
                errno.EACCES,
                errno.EPERM,
                errno.EROFS,
                errno.ENOSPC,
                errno.EIO,
                errno.EDQUOT,
            }:
                unavailable = True
            cause = cause.__cause__
        return IndexReadinessReport(state="unreadable" if unavailable else "invalid"), None, None
    except OSError:
        return IndexReadinessReport(state="unreadable"), None, None
