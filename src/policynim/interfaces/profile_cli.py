"""Offline local profile commands; source updates never publish an index."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer

from policynim.errors import PolicyNIMError
from policynim.runtime_paths import resolve_corpus_root
from policynim.services.profiles import (
    diff_profile,
    initialize_profile,
    resolve_profile,
    update_profile,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Pin and compose explicit local packs without model calls or Git changes.",
)
ProfilePath = Annotated[Path, typer.Argument(help="Local profile TOML file.")]
OutputFormat = Annotated[
    Literal["text", "json"], typer.Option("--format", help="Output: text or json.")
]


def _error(exc: Exception, output_format: str) -> NoReturn:
    """Return a nonzero structured error without echoing OS paths or private inputs."""
    message = (
        str(exc)
        if isinstance(exc, PolicyNIMError)
        else (
            "Couldn't access profile inputs or output. Check paths, permissions, and UTF-8 files."
        )
    )
    if output_format == "json":
        typer.echo(json.dumps({"status": "error", "message": message}))
    else:
        typer.echo(message, err=True)
    raise typer.Exit(code=1)


@app.command("init")
def init_profile(
    path: ProfilePath,
    packs: Annotated[
        list[Path] | None,
        typer.Option(
            "--pack",
            help="Explicit local pack directory; repeat for baseline plus additions. "
            "Defaults to the bundled baseline when omitted.",
        ),
    ] = None,
    profile_id: Annotated[str, typer.Option("--id", help="Stable profile name.")] = "local",
    output_format: OutputFormat = "text",
) -> None:
    """Create a new pinned TOML profile; existing files are preserved."""
    try:
        profile = initialize_profile(
            path.absolute(), packs or [resolve_corpus_root()], profile_id=profile_id
        )
    except (PolicyNIMError, OSError, ValueError, RuntimeError) as exc:
        _error(exc, output_format)
    if output_format == "json":
        typer.echo(
            json.dumps(
                {"status": "ok", "path": str(path), "profile": profile.model_dump(mode="json")}
            )
        )
    else:
        typer.echo(f"Created pinned profile: {path}")
        typer.echo(f"Next: policynim profile preview {shlex.quote(str(path))}")


@app.command("preview")
def preview_profile(path: ProfilePath, output_format: OutputFormat = "text") -> None:
    """Show the effective catalog and retained excluded/replaced policy provenance."""
    try:
        catalog = resolve_profile(path.absolute()).catalog
    except (PolicyNIMError, OSError, ValueError, RuntimeError) as exc:
        _error(exc, output_format)
    if output_format == "json":
        typer.echo(catalog.model_dump_json(indent=2))
    else:
        typer.echo(f"Profile {catalog.profile_id}: {catalog.effective_count} effective policies")
        typer.echo(f"Source catalog: {catalog.digest}; indexed generation unknown")
        for entry in catalog.policies:
            typer.echo(f"{entry.policy.policy_id}  {entry.state}  {entry.reason or ''}")


@app.command("validate")
def validate_profile(path: ProfilePath, output_format: OutputFormat = "text") -> None:
    """Validate pins, IDs, decisions, declared conflicts, and exact runtime matchers."""
    try:
        catalog = resolve_profile(path.absolute()).catalog
    except (PolicyNIMError, OSError, ValueError, RuntimeError) as exc:
        _error(exc, output_format)
    result = {
        "status": "ok",
        "catalog_digest": catalog.digest,
        "effective_count": catalog.effective_count,
        "limits": "Structure and declared conflicts only; prose compatibility isn't certified.",
    }
    if output_format == "json":
        typer.echo(json.dumps(result, indent=2))
    else:
        typer.echo(f"Valid profile: {catalog.effective_count} effective policies; {catalog.digest}")
        typer.echo(result["limits"])


@app.command("diff")
def show_profile_diff(path: ProfilePath, output_format: OutputFormat = "text") -> None:
    """Compare selected local inputs against pins without updating or publishing them."""
    try:
        report, _, _ = diff_profile(path.absolute())
    except (PolicyNIMError, OSError, ValueError, RuntimeError) as exc:
        _error(exc, output_format)
    if output_format == "json":
        typer.echo(report.model_dump_json(indent=2))
    else:
        for name in ("added", "changed", "removed", "changed_packs", "invalidated_decisions"):
            typer.echo(f"{name}: {', '.join(getattr(report, name)) or '(none)'}")
        typer.echo(f"Review digest: {report.review_digest}")
        if report.validation_error:
            typer.echo(f"Update blocked: {report.validation_error}")
            typer.echo(
                "Review current_policies with --format json for new text and target digests."
            )
        else:
            typer.echo(
                f"Apply: policynim profile update {shlex.quote(str(path))} "
                f"--expect-digest {report.review_digest}"
            )


@app.command("update")
def apply_profile_update(
    path: ProfilePath,
    expected_digest: Annotated[
        str,
        typer.Option(
            "--expect-digest", help="Exact review_digest from profile diff after reviewing changes."
        ),
    ],
    output_format: OutputFormat = "text",
) -> None:
    """Explicitly repin reviewed sources; this doesn't activate or rebuild an index."""
    try:
        report = update_profile(path.absolute(), expected_digest=expected_digest)
    except (PolicyNIMError, OSError, ValueError, RuntimeError) as exc:
        _error(exc, output_format)
    if output_format == "json":
        typer.echo(
            json.dumps({"status": "ok", "path": str(path), "diff": report.model_dump(mode="json")})
        )
    else:
        typer.echo(f"Updated source pins: {path}; the index hasn't changed.")
        for warning in report.warnings:
            typer.echo(f"Warning: {warning}")
        typer.echo(f"Next: policynim profile preview {shlex.quote(str(path))}")
