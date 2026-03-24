"""CLI surface for the PolicyNIM public workflow."""

from __future__ import annotations

from typing import Annotated, Literal, NoReturn

import typer

from policynim.errors import PolicyNIMError
from policynim.interfaces.mcp import run_server
from policynim.services import (
    create_index_dump_service,
    create_ingest_service,
    create_preflight_service,
    create_search_service,
)
from policynim.settings import get_settings
from policynim.types import MAX_TOP_K, PreflightRequest, SearchRequest

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Policy-aware preflight tooling for AI coding agents.",
)


@app.command()
def ingest() -> None:
    """Build the local policy index from the shipped corpus."""
    try:
        service = create_ingest_service(get_settings())
        result = service.run()
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))

    typer.echo(f"Indexed {result.chunk_count} chunks from {result.document_count} documents.")
    typer.echo(f"Model: {result.embedding_model}")
    typer.echo(f"Index: {result.index_uri} (table: {result.table_name})")


@app.command(
    name="dump-index",
    help=(
        "Print all indexed chunks in a terminal-friendly format; "
        "add ` | less` to command for paging large output."
    ),
)
def dump_index() -> None:
    """Print all indexed chunks in a terminal-friendly format."""
    try:
        service = create_index_dump_service(get_settings())
        chunks = service.list_chunks()
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))

    typer.echo(f"Indexed chunks: {len(chunks)}")
    for chunk in chunks:
        typer.echo("=" * 100)
        typer.echo(chunk.chunk_id)
        typer.echo(f"{chunk.path} | {chunk.section} | {chunk.lines}")
        typer.echo("")
        typer.echo(chunk.text)


@app.command()
def preflight(
    task: Annotated[
        str,
        typer.Option("--task", help="Describe the coding task that needs policy guidance."),
    ],
    domain: Annotated[
        str | None,
        typer.Option("--domain", help="Optional policy domain such as backend or security."),
    ] = None,
    top_k: Annotated[
        int | None,
        typer.Option(
            "--top-k",
            min=1,
            max=MAX_TOP_K,
            help="Retrieval depth. Allowed range: 1-20.",
        ),
    ] = None,
) -> None:
    """Return policy guidance for a coding task."""
    settings = get_settings()
    resolved_top_k = top_k if top_k is not None else settings.default_top_k
    service = None
    try:
        service = create_preflight_service(settings)
        result = service.preflight(PreflightRequest(task=task, domain=domain, top_k=resolved_top_k))
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(result.model_dump_json(indent=2))


@app.command()
def search(
    query: Annotated[
        str,
        typer.Option("--query", help="Natural-language query for policy search."),
    ],
    domain: Annotated[
        str | None,
        typer.Option("--domain", help="Optional policy domain such as backend or security."),
    ] = None,
    top_k: Annotated[
        int | None,
        typer.Option(
            "--top-k",
            min=1,
            max=MAX_TOP_K,
            help="Retrieval depth. Allowed range: 1-20.",
        ),
    ] = None,
) -> None:
    """Search the policy corpus."""
    settings = get_settings()
    resolved_top_k = top_k if top_k is not None else settings.default_top_k
    service = None
    try:
        service = create_search_service(settings)
        result = service.search(SearchRequest(query=query, domain=domain, top_k=resolved_top_k))
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(result.model_dump_json(indent=2))


@app.command()
def mcp(
    transport: Annotated[
        Literal["stdio", "streamable-http"],
        typer.Option(
            "--transport",
            help="MCP transport. Supported values: stdio, streamable-http.",
        ),
    ] = "stdio",
) -> None:
    """Run the MCP server."""
    run_server(transport=transport)


def main() -> None:
    """Run the PolicyNIM CLI."""
    app()


def _exit_with_error(message: str) -> NoReturn:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _close_service(service: object | None) -> None:
    close = getattr(service, "close", None)
    if callable(close):
        close()


if __name__ == "__main__":
    main()
