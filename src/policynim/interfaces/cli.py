"""CLI surface for the Day 1 PolicyNIM scaffold."""

from __future__ import annotations

from typing import Annotated

import typer

from policynim.errors import NotImplementedYetError
from policynim.interfaces.mcp import SUPPORTED_TRANSPORTS, run_server
from policynim.settings import DEFAULT_TOP_K

NOT_IMPLEMENTED = (
    "PolicyNIM Day 1 only locks the public surface. Retrieval and answer generation "
    "arrive in later commits."
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Policy-aware preflight tooling for AI coding agents.",
)


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
        int,
        typer.Option("--top-k", min=1, help="Reserved retrieval depth for later implementation."),
    ] = DEFAULT_TOP_K,
) -> None:
    """Return policy guidance for a coding task."""
    _ = (task, domain, top_k)
    typer.secho(NOT_IMPLEMENTED, fg=typer.colors.YELLOW)
    raise NotImplementedYetError(NOT_IMPLEMENTED)


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
        int,
        typer.Option("--top-k", min=1, help="Reserved retrieval depth for later implementation."),
    ] = DEFAULT_TOP_K,
) -> None:
    """Search the policy corpus."""
    _ = (query, domain, top_k)
    typer.secho(NOT_IMPLEMENTED, fg=typer.colors.YELLOW)
    raise NotImplementedYetError(NOT_IMPLEMENTED)


@app.command()
def mcp(
    transport: Annotated[
        str,
        typer.Option(
            "--transport",
            help="MCP transport. Supported values: stdio, streamable-http.",
        ),
    ] = "stdio",
) -> None:
    """Run the MCP server."""
    if transport not in SUPPORTED_TRANSPORTS:
        allowed = ", ".join(SUPPORTED_TRANSPORTS)
        raise typer.BadParameter(f"Transport must be one of: {allowed}.")
    run_server(transport=transport)


def main() -> None:
    """Run the PolicyNIM CLI."""
    try:
        app()
    except NotImplementedYetError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    main()
