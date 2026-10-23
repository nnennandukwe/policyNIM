"""Explicit vector connectivity, publication, and generation verification commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from typing import Annotated, Literal

import typer

from policynim.errors import PolicyNIMError
from policynim.services.index_store import create_index_store, pinned_generation
from policynim.services.vector_storage import prepare_vector_generation, probe_vector_backend
from policynim.settings import Settings, get_settings
from policynim.storage.vectors import vector_namespace

vector_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Probe, publish, and verify the configured vector backend without model calls.",
)
OutputFormat = Annotated[Literal["text", "json"], typer.Option("--format")]


def _run(action: Callable[[Settings], dict[str, object]], output_format: str) -> None:
    """Keep output machine-readable and raw database credentials outside diagnostics."""
    try:
        result = action(get_settings())
    except (PolicyNIMError, ValueError, OSError) as exc:
        message = (
            str(exc)
            if isinstance(exc, PolicyNIMError)
            else (
                "Vector configuration couldn't be validated. "
                "Check settings and access before retrying."
            )
        )
        result = {"status": "error", "message": message}
        typer.echo(json.dumps(result) if output_format == "json" else message)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2) if output_format == "json" else str(result))


@vector_app.command("probe")
def probe(output_format: OutputFormat = "text") -> None:
    """Check read-only connectivity; a connected database isn't yet a verified generation."""
    _run(probe_vector_backend, output_format)


@vector_app.command("sync")
def sync(output_format: OutputFormat = "text") -> None:
    """Publish the current generation to a fresh namespace without overwriting remote data."""

    def publish(settings: Settings) -> dict[str, object]:
        """Hold the canonical lease through preparation and read-back validation."""
        local = settings.model_copy(update={"vector_backend": "sqlite", "vector_url": None})
        with closing(create_index_store(local)) as store:
            snapshot = pinned_generation(store)
            assert snapshot is not None
            prepare_vector_generation(settings, snapshot)
            return {
                "status": "ready",
                "backend": settings.vector_backend,
                "generation_id": snapshot.generation_id,
                "namespace": vector_namespace(settings.vector_namespace, snapshot.generation_id)
                if settings.vector_backend != "sqlite"
                else None,
            }

    _run(publish, output_format)


@vector_app.command("inspect")
def inspect(output_format: OutputFormat = "text") -> None:
    """Verify the selected source generation and configured vector replica together."""

    def verify(settings: Settings) -> dict[str, object]:
        """Exercise the same store and identity validator used by retrieval services."""
        with closing(create_index_store(settings)) as store:
            store.validate_identity()
            return {
                "status": "ready",
                "backend": settings.vector_backend,
                "generation_id": store.generation_id,
                "chunk_count": store.count(),
            }

    _run(verify, output_format)
