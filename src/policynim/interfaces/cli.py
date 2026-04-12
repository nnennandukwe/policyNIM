"""CLI surface for the PolicyNIM public workflow."""

from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer
from pydantic import TypeAdapter, ValidationError

import policynim.config_discovery as config_discovery
from policynim.errors import ConfigurationError, MissingIndexError, PolicyNIMError
from policynim.interfaces.mcp import run_server
from policynim.services import (
    create_beta_auth_service,
    create_eval_service,
    create_index_dump_service,
    create_ingest_service,
    create_policy_compiler_service,
    create_policy_evidence_trace_service,
    create_policy_regeneration_service,
    create_policy_router_service,
    create_preflight_service,
    create_runtime_decision_service,
    create_runtime_evidence_report_service,
    create_runtime_execution_service,
    create_search_service,
)
from policynim.settings import Settings, get_settings
from policynim.types import (
    MAX_TOP_K,
    CompileRequest,
    EvalBackend,
    EvalExecutionMode,
    PreflightEvidenceTraceResult,
    PreflightRegenerationRequest,
    PreflightRequest,
    RegenerationBackend,
    RouteRequest,
    RuntimeActionRequest,
    RuntimeDecisionResult,
    RuntimeExecutionOutcome,
    SearchRequest,
    TaskType,
)

_RUNTIME_REQUEST_ADAPTER = TypeAdapter(RuntimeActionRequest)
_STANDALONE_MISSING_INDEX_MESSAGE = (
    "Local PolicyNIM data is not built yet. Run `policynim ingest` to build the local policy index."
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Policy-aware preflight tooling for AI coding agents.",
)
beta_admin_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Hosted beta operator commands.",
)
runtime_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Deterministic runtime decision and execution commands.",
)
evidence_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Runtime evidence reporting commands.",
)
app.add_typer(beta_admin_app, name="beta-admin")
app.add_typer(runtime_app, name="runtime")
app.add_typer(evidence_app, name="evidence")


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Print the installed PolicyNIM version and exit.",
            callback=lambda value: _version_option_callback(value),
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Run the PolicyNIM CLI."""
    del version


@app.command(
    help=(
        "Run interactive standalone setup, prompt for NVIDIA_API_KEY and an optional "
        "custom corpus directory, and write the local PolicyNIM config file."
    ),
)
def init() -> None:
    """Prompt for standalone local CLI settings and write them to an env file."""
    destination = config_discovery.resolve_init_config_file()
    api_key = typer.prompt(
        "NVIDIA_API_KEY",
        default="",
        show_default=False,
        hide_input=True,
    ).strip()
    if not api_key:
        _exit_with_error("NVIDIA_API_KEY is required.")

    corpus_input = typer.prompt(
        "Optional custom corpus directory",
        default="",
        show_default=False,
    )
    try:
        resolved_corpus_dir = config_discovery.normalize_init_corpus_dir(corpus_input)
        config_path = config_discovery.write_init_config_file(
            destination=destination,
            api_key=api_key,
            corpus_dir=resolved_corpus_dir,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))
    except OSError as exc:
        _exit_with_error(f"Could not write config file {destination.expanduser()}: {exc}.")

    corpus_message = (
        resolved_corpus_dir.as_posix()
        if resolved_corpus_dir is not None
        else "bundled PolicyNIM corpus"
    )
    typer.echo(f"Wrote PolicyNIM config to {config_path}.")
    typer.echo(f"Corpus: {corpus_message}")
    typer.echo("Next step: run `policynim ingest`.")


@app.command()
def ingest() -> None:
    """Build the local policy index from the shipped corpus."""
    try:
        settings = _load_setup_dependent_settings()
        service = create_ingest_service(settings)
        result = service.run()
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
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
        settings = _load_setup_dependent_settings()
        service = create_index_dump_service(settings)
        chunks = service.list_chunks()
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))

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
    trace: Annotated[
        bool,
        typer.Option(
            "--trace",
            help="Include a replay-free evidence trace with the preflight result.",
        ),
    ] = False,
    regenerate: Annotated[
        bool,
        typer.Option(
            "--regenerate",
            help="Run the opt-in policy-backed regeneration loop.",
        ),
    ] = False,
    max_regenerations: Annotated[
        int,
        typer.Option(
            "--max-regenerations",
            min=1,
            max=3,
            help="Maximum regeneration attempts after the initial generation. Allowed range: 1-3.",
        ),
    ] = 1,
    backend: Annotated[
        RegenerationBackend,
        typer.Option(
            "--backend",
            help="Regeneration backend. Supported values: nemo, nemo_evaluator, nat.",
        ),
    ] = "nemo",
) -> None:
    """Return policy guidance for a coding task."""
    service = None
    try:
        settings = _load_setup_dependent_settings()
        resolved_top_k = top_k if top_k is not None else settings.default_top_k
        request = PreflightRequest(task=task, domain=domain, top_k=resolved_top_k)
        if regenerate:
            service = create_policy_regeneration_service(settings, backend=backend)
            result = service.regenerate(
                PreflightRegenerationRequest(
                    task=task,
                    domain=domain,
                    top_k=resolved_top_k,
                    backend=backend,
                    max_regenerations=max_regenerations,
                )
            )
        else:
            preflight_service = create_preflight_service(settings)
            service = preflight_service
            if trace:
                trace_result = preflight_service.preflight_with_trace(request)
                evidence_trace = create_policy_evidence_trace_service().build(trace_result)
                result = PreflightEvidenceTraceResult(
                    result=trace_result.result,
                    evidence_trace=evidence_trace,
                )
            else:
                result = preflight_service.preflight(request)
    except ValidationError as exc:
        _exit_with_error(_format_validation_error("Preflight request", exc))
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
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
    service = None
    try:
        settings = _load_setup_dependent_settings()
        resolved_top_k = top_k if top_k is not None else settings.default_top_k
        service = create_search_service(settings)
        result = service.search(SearchRequest(query=query, domain=domain, top_k=resolved_top_k))
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(result.model_dump_json(indent=2))


@app.command()
def route(
    task: Annotated[
        str,
        typer.Option("--task", help="Describe the coding task that needs policy selection."),
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
            help="Selected evidence depth. Allowed range: 1-20.",
        ),
    ] = None,
    task_type: Annotated[
        TaskType | None,
        typer.Option(
            "--task-type",
            help=(
                "Optional task-type override. Supported values: bug_fix, refactor, "
                "api_change, migration, test_change, feature_work, unknown."
            ),
        ),
    ] = None,
) -> None:
    """Return task-aware selected policy evidence."""
    settings = get_settings()
    resolved_top_k = top_k if top_k is not None else settings.default_top_k
    service = None
    try:
        request = RouteRequest(
            task=task,
            domain=domain,
            top_k=resolved_top_k,
            task_type=task_type,
        )
        service = create_policy_router_service(settings)
        result = service.route(request)
    except ValidationError as exc:
        _exit_with_error(_format_validation_error("Route request", exc))
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(result.packet.model_dump_json(indent=2))


@app.command()
def compile(
    task: Annotated[
        str,
        typer.Option("--task", help="Describe the coding task that needs policy compilation."),
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
            help="Selected evidence depth. Allowed range: 1-20.",
        ),
    ] = None,
    task_type: Annotated[
        TaskType | None,
        typer.Option(
            "--task-type",
            help=(
                "Optional task-type override. Supported values: bug_fix, refactor, "
                "api_change, migration, test_change, feature_work, unknown."
            ),
        ),
    ] = None,
) -> None:
    """Return compiled policy constraints for planning and generation."""
    service = None
    try:
        settings = _load_setup_dependent_settings()
        resolved_top_k = top_k if top_k is not None else settings.default_top_k
        request = CompileRequest(
            task=task,
            domain=domain,
            top_k=resolved_top_k,
            task_type=task_type,
        )
        service = create_policy_compiler_service(settings)
        result = service.compile(request)
    except ValidationError as exc:
        _exit_with_error(_format_validation_error("Compile request", exc))
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(result.packet.model_dump_json(indent=2))


@runtime_app.command("decide")
def runtime_decide(
    input: Annotated[
        str,
        typer.Option(
            "--input",
            help="Path to a runtime request JSON file, or - to read JSON from stdin.",
        ),
    ],
) -> None:
    """Return a deterministic runtime decision for one action request."""
    service = None
    try:
        request = _load_runtime_request_payload(input)
        service = create_runtime_decision_service(_load_setup_dependent_settings())
        result = service.decide(request)
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    finally:
        _close_service(service)

    typer.echo(result.model_dump_json(indent=2))


@runtime_app.command("execute")
def runtime_execute(
    input: Annotated[
        str,
        typer.Option(
            "--input",
            help="Path to a runtime request JSON file, or - to read JSON from stdin.",
        ),
    ],
) -> None:
    """Enforce runtime policy, optionally confirm, and execute one action."""
    service = None
    try:
        request = _load_runtime_request_payload(input)
        service = create_runtime_execution_service(
            _load_setup_dependent_settings(),
            confirmer=_build_cli_confirmer(),
        )
        result = service.execute(request)
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    finally:
        _close_service(service)

    typer.echo(result.model_dump_json(indent=2))
    exit_code = _exit_code_for_runtime_execution(result.execution_outcome)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@evidence_app.command("report")
def evidence_report(
    session_id: Annotated[
        str,
        typer.Option(
            "--session-id",
            help="Runtime evidence session id to summarize.",
        ),
    ],
) -> None:
    """Summarize one runtime evidence session from SQLite-backed storage."""
    service = None
    try:
        service = create_runtime_evidence_report_service(_load_setup_dependent_settings())
        result = service.report_session(session_id)
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    finally:
        _close_service(service)

    typer.echo(result.model_dump_json(indent=2))


@app.command()
def eval(
    mode: Annotated[
        EvalExecutionMode,
        typer.Option("--mode", help="Eval execution mode. Supported values: offline, live."),
    ] = "offline",
    backend: Annotated[
        EvalBackend,
        typer.Option(
            "--backend",
            help="Eval backend. Supported values: default, nemo, nemo_evaluator, nat.",
        ),
    ] = "default",
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
            help="Run evals without starting the local Phoenix UI automatically.",
        ),
    ] = False,
    regenerate: Annotated[
        bool,
        typer.Option(
            "--regenerate",
            help="Run policy-backed regeneration for preflight eval cases.",
        ),
    ] = False,
    max_regenerations: Annotated[
        int,
        typer.Option(
            "--max-regenerations",
            min=1,
            max=3,
            help="Maximum regeneration attempts after the initial generation. Allowed range: 1-3.",
        ),
    ] = 1,
) -> None:
    """Run the PolicyNIM eval suite and persist local reports."""
    service = None
    try:
        settings = _load_setup_dependent_settings()
        service = create_eval_service(settings)
        result = service.run(
            mode=mode,
            backend=backend,
            compare_rerank=not no_compare_rerank,
            regenerate=regenerate,
            max_regenerations=max_regenerations,
        )
        if not headless:
            service.start_ui()
            service.publish_to_ui(result)
        typer.echo(result.model_dump_json(indent=2))
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    except ValueError as exc:
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
        _load_setup_dependent_settings()
        run_server(transport=transport)
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))


@beta_admin_app.command("list-accounts")
def beta_admin_list_accounts() -> None:
    """Print all hosted beta accounts as JSON."""
    service = None
    try:
        service = create_beta_auth_service(get_settings())
        accounts = service.list_accounts()
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(
        json.dumps(
            [account.model_dump(mode="json") for account in accounts],
            indent=2,
        )
    )


@beta_admin_app.command("suspend")
def beta_admin_suspend(
    github_login: Annotated[
        str,
        typer.Option("--github-login", help="GitHub login for the hosted beta account."),
    ],
) -> None:
    """Suspend one hosted beta account."""
    service = None
    try:
        service = create_beta_auth_service(get_settings())
        account = service.suspend_account(github_login=github_login)
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(account.model_dump_json(indent=2))


@beta_admin_app.command("resume")
def beta_admin_resume(
    github_login: Annotated[
        str,
        typer.Option("--github-login", help="GitHub login for the hosted beta account."),
    ],
) -> None:
    """Resume one hosted beta account."""
    service = None
    try:
        service = create_beta_auth_service(get_settings())
        account = service.resume_account(github_login=github_login)
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(account.model_dump_json(indent=2))


@beta_admin_app.command("revoke-key")
def beta_admin_revoke_key(
    github_login: Annotated[
        str,
        typer.Option("--github-login", help="GitHub login for the hosted beta account."),
    ],
) -> None:
    """Revoke the active hosted beta API key for one account."""
    service = None
    try:
        service = create_beta_auth_service(get_settings())
        account = service.revoke_api_key(github_login=github_login)
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(account.model_dump_json(indent=2))


def main() -> None:
    """Run the PolicyNIM CLI."""
    app()


def _version_option_callback(value: bool) -> None:
    if not value:
        return
    try:
        typer.echo(_resolve_installed_version())
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    raise typer.Exit()


def _exit_with_error(message: str) -> NoReturn:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _format_validation_error(label: str, exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "request"
    return f"{label} is invalid at {location}: {error['msg']}."


def _read_json_input(input_value: str) -> object:
    source_label = _describe_runtime_input_source(input_value)
    try:
        if input_value == "-":
            raw_text = sys.stdin.read()
        else:
            raw_text = Path(input_value).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PolicyNIMError(
            f"Could not read runtime input file {input_value}: file not found."
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        if input_value == "-":
            raise PolicyNIMError("Could not read runtime input from stdin.") from exc
        raise PolicyNIMError(f"Could not read runtime input file {input_value}.") from exc

    if not raw_text.strip():
        raise PolicyNIMError(f"Runtime input from {source_label} must not be empty.")

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise PolicyNIMError(f"Runtime input from {source_label} must be valid JSON.") from exc

    if not isinstance(payload, dict):
        raise PolicyNIMError(f"Runtime input from {source_label} must be a JSON object.")
    return payload


def _load_runtime_request_payload(input_value: str) -> RuntimeActionRequest:
    payload = _read_json_input(input_value)
    try:
        return _RUNTIME_REQUEST_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise PolicyNIMError(_format_validation_error("Runtime input", exc)) from exc


def _build_cli_confirmer():
    def confirm(decision_result: RuntimeDecisionResult) -> bool:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise PolicyNIMError(
                "Runtime execution required explicit confirmation, "
                "but no interactive terminal is available.",
                failure_class="confirmation_unavailable",
            )
        return bool(
            typer.confirm(
                f"{decision_result.summary} Continue with runtime execution?",
                default=False,
                err=True,
            )
        )

    return confirm


def _exit_code_for_runtime_execution(outcome: RuntimeExecutionOutcome) -> int:
    if outcome in ("allowed", "confirmed"):
        return 0
    return 1


def _describe_runtime_input_source(input_value: str) -> str:
    if input_value == "-":
        return "stdin"
    return str(Path(input_value))


def _resolve_installed_version() -> str:
    try:
        return installed_version("policynim")
    except PackageNotFoundError as exc:
        raise PolicyNIMError("Installed package metadata for PolicyNIM is unavailable.") from exc


def _load_setup_dependent_settings() -> Settings:
    if config_discovery.standalone_setup_missing():
        _exit_with_error(_missing_setup_message())
    try:
        return get_settings()
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))


def _missing_setup_message() -> str:
    config_path = config_discovery.resolve_init_config_file()
    return f"PolicyNIM is not set up yet. Run `policynim init` to create {config_path}."


def _cli_error_message(error: PolicyNIMError) -> str:
    if config_discovery.standalone_setup_missing() and _looks_like_missing_local_setup_error(error):
        return _missing_setup_message()
    if isinstance(error, MissingIndexError) and _is_standalone_local_runtime():
        return _STANDALONE_MISSING_INDEX_MESSAGE
    return str(error)


def _looks_like_missing_local_setup_error(error: PolicyNIMError) -> bool:
    if not isinstance(error, ConfigurationError):
        return False

    message = str(error)
    lowered = message.lower()
    return "nvidia_api_key" in lowered or "missing nvidia key" in lowered


def _is_standalone_local_runtime() -> bool:
    return (
        not config_discovery.is_source_checkout()
        and not config_discovery.is_hosted_process_environment()
    )


def _close_service(service: object | None) -> None:
    close = getattr(service, "close", None)
    if callable(close):
        close()


if __name__ == "__main__":
    main()
