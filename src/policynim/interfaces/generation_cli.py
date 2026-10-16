"""Offline inspection, migration, activation, and transport of complete generations."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

import typer

from policynim.errors import GenerationError, PolicyNIMError
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage.generations import GenerationStore
from policynim.types import EmbeddingIdentity, StrictModel

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage complete policy generations offline; these commands never call NVIDIA.",
)
OutputFormat = Annotated[Literal["text", "json"], typer.Option("--format")]
SelectedID = Annotated[str | None, typer.Option("--id", help="Retained ID; defaults to active.")]
ExpectedActive = Annotated[
    str, typer.Option("--expect-active", help="Reviewed active generation ID.")
]


def _run(
    action: Callable[[Settings, GenerationStore], StrictModel | dict[str, object]],
    output_format: str,
) -> None:
    """Run one offline action with safe errors and consistent machine output."""
    store: GenerationStore | None = None
    try:
        settings = get_settings().model_copy(deep=True)
        store = GenerationStore(
            root=resolve_runtime_path(settings.generations_dir),
            embedding=EmbeddingIdentity(
                model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
            ),
        )
        result = action(settings, store)
        payload = result.model_dump(mode="json") if isinstance(result, StrictModel) else result
    except (PolicyNIMError, OSError, ValueError) as exc:
        message = (
            str(exc)
            if isinstance(exc, PolicyNIMError)
            else (
                "Generation inputs couldn't be read or validated. Check configuration, paths, "
                "permissions, and the expected digest. Existing data is preserved."
            )
        )
        failure: dict[str, object] = {"status": "error", "message": message}
        if isinstance(exc, GenerationError) and store is not None:
            try:
                pointer = store.read_activation()
            except (PolicyNIMError, OSError, ValueError):
                pass
            else:
                failure["activation"] = pointer.model_dump(mode="json") if pointer else None
        if output_format == "json":
            typer.echo(json.dumps(failure))
        else:
            typer.echo(message, err=True)
            if failure.get("activation") is not None:
                typer.echo("Activation: " + json.dumps(failure["activation"]), err=True)
        raise typer.Exit(code=1) from exc
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2))
    else:
        for name, value in payload.items():
            typer.echo(f"{name}: {json.dumps(value) if isinstance(value, (dict, list)) else value}")


@app.command("inspect")
def inspect_generation(
    generation_id: SelectedID = None, output_format: OutputFormat = "text"
) -> None:
    """Validate every artifact and report the selected identity without changing activation."""

    def inspect(settings: Settings, store: GenerationStore) -> dict[str, object]:
        """Hold a reader lease while assembling one validated report."""
        with store.pin(generation_id or settings.generation_id) as snapshot:
            return {
                "status": "ok",
                "generation_id": snapshot.generation_id,
                "activation": snapshot.activation.model_dump(mode="json")
                if snapshot.activation
                else None,
                "manifest": snapshot.manifest.model_dump(mode="json"),
                "catalog": snapshot.indexed_catalog.model_dump(mode="json"),
            }

    _run(inspect, output_format)


@app.command("migrate")
def migrate_generation(output_format: OutputFormat = "text") -> None:
    """Copy a matching standalone SQLite/rules pair; preserve both originals."""
    _run(
        lambda settings, store: store.migrate(
            settings,
            index_path=resolve_runtime_path(settings.index_db_path),
            rules_path=resolve_runtime_path(settings.runtime_rules_artifact_path),
        ),
        output_format,
    )


@app.command("import")
def import_generation(
    bundle: Annotated[Path, typer.Argument(help="Verified generation ZIP bundle.")],
    expected_digest: Annotated[
        str, typer.Option("--expect-digest", help="Trusted sha256: digest.")
    ],
    require_source_match: Annotated[bool, typer.Option("--require-source-match")] = False,
    output_format: OutputFormat = "text",
) -> None:
    """Validate and activate a complete bundle without embedding or credentials."""
    _run(
        lambda settings, store: store.import_bundle(
            bundle,
            expected_digest=expected_digest,
            source_settings=settings if require_source_match else None,
        ),
        output_format,
    )


@app.command("export")
def export_generation(
    destination: Annotated[
        Path, typer.Argument(help="New ZIP path; existing files are preserved.")
    ],
    generation_id: SelectedID = None,
    output_format: OutputFormat = "text",
) -> None:
    """Export a retained complete generation with a verified transport checksum."""

    def export(settings: Settings, store: GenerationStore) -> dict[str, object]:
        """Pin the selected object through export and checksum readback."""
        with store.pin(generation_id or settings.generation_id) as snapshot:
            digest = store.export_bundle(destination, snapshot.generation_id)
            return {"status": "ok", "generation_id": snapshot.generation_id, "digest": digest}

    _run(export, output_format)


@app.command("activate")
def activate_generation(
    generation_id: Annotated[str, typer.Argument()],
    expected_active: ExpectedActive,
    output_format: OutputFormat = "text",
) -> None:
    """Activate a retained valid object only if the reviewed pointer still matches."""
    _run(
        lambda settings, store: store.activate(generation_id, expected_active=expected_active),
        output_format,
    )


@app.command("rollback")
def rollback_generation(
    expected_active: ExpectedActive, output_format: OutputFormat = "text"
) -> None:
    """Atomically reactivate the previous valid object after checking the reviewed pointer."""
    _run(lambda settings, store: store.rollback(expected_active=expected_active), output_format)


@app.command("prune")
def prune_generations(
    keep_recent: Annotated[int, typer.Option("--keep-recent", min=0, max=20)] = 2,
    output_format: OutputFormat = "text",
) -> None:
    """Remove verified unreferenced objects; retain active, previous, and leased readers."""
    _run(lambda settings, store: store.prune(keep_recent=keep_recent), output_format)
