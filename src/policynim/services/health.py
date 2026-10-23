"""Runtime health service for hosted HTTP readiness checks."""

from __future__ import annotations

import logging
import traceback
from contextlib import closing
from pathlib import Path

from policynim.contracts import IndexStore
from policynim.errors import ConfigurationError, GenerationError, IndexCompatibilityError
from policynim.public_urls import public_route_url
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.generations import GenerationStore
from policynim.services.index_store import create_index_store, pinned_generation
from policynim.settings import Settings, get_settings
from policynim.storage.index_readiness import inspect_index_readiness
from policynim.storage.sqlite_vec import POLICY_CHUNKS_TABLE
from policynim.types import EmbeddingIdentity, HealthCheckResult

LOGGER = logging.getLogger(__name__)


class RuntimeHealthService:
    """Inspect generation and selected vector storage without calling NVIDIA."""

    def __init__(
        self,
        *,
        index_store: IndexStore | None = None,
        settings: Settings | None = None,
        table_name: str,
        mcp_url: str | None,
    ) -> None:
        """Borrow an injected store or inspect validated generation metadata for default checks."""
        if (index_store is None) == (settings is None):
            raise ValueError("Provide one injected store or settings for generation health.")
        self._settings = settings.model_copy(deep=True) if settings is not None else None
        self._index_store = index_store
        self._table_name = table_name
        self._mcp_url = mcp_url

    def check(self) -> HealthCheckResult:
        """Return a readiness payload for the hosted HTTP runtime."""
        try:
            if self._settings is not None:
                if self._settings.vector_backend != "sqlite":
                    with closing(create_index_store(self._settings)) as external:
                        return self._check_store(external)
                generations = GenerationStore(
                    root=resolve_runtime_path(self._settings.generations_dir),
                    embedding=EmbeddingIdentity(
                        model=self._settings.nvidia_embed_model,
                        endpoint=self._settings.nvidia_base_url,
                    ),
                )
                generation = generations.readiness(self._settings.generation_id)
                return HealthCheckResult(
                    status="ok",
                    ready=True,
                    table_name=self._table_name,
                    row_count=generation.chunk_count,
                    mcp_url=self._mcp_url,
                    generation_id=generation.generation_id,
                )
            store = self._index_store
            assert store is not None
            return self._check_store(store)
        except GenerationError as exc:
            return self._not_ready(str(exc))
        except IndexCompatibilityError:
            return self._not_ready(
                "Local index embedding identity is incompatible or ingestion is incomplete."
            )
        except Exception as exc:
            log_health_failure("Runtime health check failed.", exc)
            return self._not_ready(format_health_failure_reason(exc))

    def _check_store(self, store: IndexStore) -> HealthCheckResult:
        """Use the configured retrieval consumer for external or explicitly injected stores."""
        probe_readiness = getattr(store, "probe_readiness", None)
        readiness = (
            probe_readiness() if callable(probe_readiness) else inspect_index_readiness(store)
        )
        if readiness.state == "missing":
            return self._not_ready(f"Local index table {self._table_name!r} doesn't exist.")
        if readiness.state == "empty":
            return self._not_ready(f"Local index table {self._table_name!r} contains no rows.")
        if readiness.state != "ready":
            return self._not_ready(readiness.reason)
        snapshot = pinned_generation(store)
        return HealthCheckResult(
            status="ok",
            ready=True,
            table_name=self._table_name,
            row_count=readiness.chunk_count or 0,
            mcp_url=self._mcp_url,
            reason=None,
            generation_id=snapshot.generation_id if snapshot else None,
        )

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
    """Inspect current generation metadata per check without retaining a startup reader."""
    active_settings = (settings or get_settings()).model_copy(deep=True)
    return RuntimeHealthService(
        settings=active_settings,
        table_name=POLICY_CHUNKS_TABLE,
        mcp_url=_derive_mcp_url(active_settings),
    )


def ensure_hosted_runtime_ready(
    settings: Settings | None = None,
    *,
    rebuild_if_missing: bool = False,
) -> None:
    """Require a complete generation; the legacy rebuild argument never ingests."""
    active_settings = (settings or get_settings()).model_copy(deep=True)
    root = resolve_runtime_path(active_settings.generations_dir)
    result = _check_hosted_runtime_health(
        active_settings, index_path=root, table_name=POLICY_CHUNKS_TABLE
    )
    if not result.ready:
        raise ConfigurationError(
            _format_hosted_runtime_error(
                index_path=root,
                table_name=POLICY_CHUNKS_TABLE,
                reason=result.reason or "Generation readiness couldn't be inspected.",
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


def _derive_mcp_url(settings: Settings) -> str | None:
    """Return the optional public MCP route included in readiness responses."""
    if settings.mcp_public_base_url is None:
        return None
    return public_route_url(settings.mcp_public_base_url, "/mcp")


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
        "Hosted streamable-http startup needs a verified complete policy generation at "
        f"{index_path_text} (table: {table_name}). {reason} "
        "Import a verified bundle or run `policynim generation migrate` before serving traffic. "
        "Configure the store with POLICYNIM_GENERATIONS_DIR. Startup doesn't call NVIDIA."
    )
