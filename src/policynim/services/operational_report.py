"""Operational summaries with read-only records and coordinated generation leases."""

from __future__ import annotations

import errno
import sqlite3

from policynim.errors import (
    ConfigurationError,
    GenerationError,
    MissingGenerationError,
    PolicyNIMError,
)
from policynim.job_types import EvaluationJobError
from policynim.operations import close_components
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store, pinned_generation
from policynim.settings import Settings
from policynim.storage.auth_database import AuthDatabaseError
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
    readiness, active_generation, served_generation, generation_state = _generation_readiness(
        settings
    )
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
        "generation_state": generation_state,
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
) -> tuple[IndexReadinessReport, str | None, str | None, str]:
    """Validate configured retrieval while keeping its selection separate from activation."""
    root = resolve_runtime_path(settings.generations_dir)
    active_generation: str | None = None
    try:
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
        activation = generations.read_activation() if root.exists() or root.is_symlink() else None
        active_generation = activation.active if activation is not None else None
        if activation is None and settings.generation_id is None:
            legacy = create_index_store(settings, legacy=True)
            try:
                readiness = inspect_index_readiness(legacy)
                return (
                    readiness,
                    None,
                    None,
                    "migration_required" if readiness.state == "ready" else readiness.state,
                )
            finally:
                close_components(legacy)
        index_store = create_index_store(settings)
        try:
            snapshot = pinned_generation(index_store)
            if snapshot is not None:
                activation = snapshot.activation
                active_generation = activation.active if activation is not None else None
            readiness = inspect_index_readiness(index_store)
            return (
                readiness,
                active_generation,
                index_store.generation_id if readiness.state == "ready" else None,
                readiness.state,
            )
        finally:
            close_components(index_store)
    except MissingGenerationError:
        state = (
            "invalid"
            if active_generation is not None and settings.generation_id in {None, active_generation}
            else "missing"
        )
        return IndexReadinessReport(state=state), active_generation, None, state
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
        return (
            IndexReadinessReport(state="unreadable" if unavailable else "invalid"),
            active_generation,
            None,
            "unreadable" if unavailable else "invalid",
        )
    except OSError:
        return IndexReadinessReport(state="unreadable"), active_generation, None, "unreadable"
