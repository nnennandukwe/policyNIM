"""CLI surface for the PolicyNIM public workflow."""

from __future__ import annotations

from typing import Annotated, Literal, NoReturn

import typer

from policynim.errors import PolicyNIMError
from policynim.interfaces.mcp import run_server
from policynim.services import (
    create_eval_service,
    create_index_dump_service,
    create_ingest_service,
    create_preflight_service,
    create_search_service,
)
from policynim.settings import get_settings
from policynim.types import MAX_TOP_K, EvalExecutionMode, PreflightRequest, SearchRequest

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
def dump_index(
    count_only: Annotated[
        bool,
        typer.Option(
            "--count-only",
            help="Print only the indexed chunk count.",
        ),
    ] = False,
) -> None:
    """Print all indexed chunks in a terminal-friendly format."""
    try:
        service = create_index_dump_service(get_settings())
        chunks = service.list_chunks()
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))

    typer.echo(f"Indexed chunks: {len(chunks)}")
    if count_only:
        return
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
def eval(
    mode: Annotated[
        EvalExecutionMode,
        typer.Option("--mode", help="Eval execution mode. Supported values: offline, live."),
    ] = "offline",
    no_compare_rerank: Annotated[
        bool,
        typer.Option(
            "--no-compare-rerank",
            help="Skip the default rerank on/off comparison and run only rerank-enabled evals.",
        ),
    ] = False,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless",
            help="Run evals without starting the local Evidently UI automatically.",
        ),
    ] = False,
) -> None:
    """Run the PolicyNIM eval suite and persist local reports."""
    service = None
    try:
        service = create_eval_service(get_settings())
        result = service.run(mode=mode, compare_rerank=not no_compare_rerank)
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(result.model_dump_json(indent=2))
    if not headless:
        try:
            service = create_eval_service(get_settings())
            service.start_ui()
        except PolicyNIMError as exc:
            _exit_with_error(str(exc))
        finally:
            _close_service(service)
    if any(run.metrics.passed_count != run.metrics.case_count for run in result.runs):
        raise typer.Exit(code=1)


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
    try:
        run_server(transport=transport)
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))


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
