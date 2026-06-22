"""CLI surface for the PolicyNIM public workflow."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shlex
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile
from typing import Annotated, Literal, NoReturn, cast
from urllib.parse import urlparse

import typer
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import TypeAdapter, ValidationError

import policynim.config_discovery as config_discovery
from policynim.agent_workflows import agent_workflows
from policynim.errors import ConfigurationError, MissingIndexError, PolicyNIMError
from policynim.interfaces.mcp import run_server
from policynim.runtime_paths import resolve_runtime_path
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
from policynim.storage import create_index_store
from policynim.storage.index_readiness import (
    format_index_readiness_detail,
    inspect_index_readiness,
)
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
    RuntimeEvidenceSessionSummary,
    RuntimeExecutionOutcome,
    SearchRequest,
    TaskType,
)

_RUNTIME_REQUEST_ADAPTER = TypeAdapter(RuntimeActionRequest)
_STANDALONE_MISSING_INDEX_MESSAGE = (
    "Local PolicyNIM data is not built yet. Run `policynim ingest` to build the local policy index."
)
_EXPECTED_MCP_TOOLS = ("policy_preflight", "policy_search")
_POSIX_PATH_TOKEN_RE = re.compile(r"(^|[\s\"'`=(:])(/(?!/)[^\s\"'`<>),;]+)")

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
        "Run interactive local setup, prompt for NVIDIA_API_KEY and an optional "
        "custom corpus directory, and write the local PolicyNIM config file."
    ),
)
def init() -> None:
    """Prompt for local CLI settings and write them to an env file."""
    destination = config_discovery.resolve_init_config_file()
    include_data_paths = not (
        config_discovery.is_source_checkout() or config_discovery.is_hosted_process_environment()
    )
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
            include_data_paths=include_data_paths,
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


@app.command(
    help=(
        "Print the first-run path for hosted MCP, local CLI, or local MCP "
        "without calling external services."
    ),
)
def quickstart(
    target: Annotated[
        Literal["hosted-mcp", "local-cli", "local-mcp"],
        typer.Option(
            "--target",
            help="First-run path to show. Supported values: hosted-mcp, local-cli, local-mcp.",
        ),
    ] = "hosted-mcp",
    client: Annotated[
        Literal["codex", "claude-code"],
        typer.Option(
            "--client",
            help="MCP client to show when the target uses MCP.",
        ),
    ] = "codex",
    hosted_url: Annotated[
        str,
        typer.Option(
            "--hosted-url",
            help="Hosted PolicyNIM MCP URL to include in generated setup commands.",
        ),
    ] = "https://<railway-domain>/mcp",
    bearer_token_env_var: Annotated[
        str,
        typer.Option(
            "--bearer-token-env-var",
            help="Environment variable that stores the hosted MCP bearer token.",
        ),
    ] = "POLICYNIM_TOKEN",
    repo_root: Annotated[
        Path | None,
        typer.Option(
            "--repo-root",
            help="PolicyNIM source checkout root to include for local MCP config.",
        ),
    ] = None,
    output_format: Annotated[
        Literal["text", "json"],
        typer.Option(
            "--format",
            help="Quickstart output format. Supported values: text, json.",
        ),
    ] = "text",
) -> None:
    """Print the safest first-run path for the selected user workflow."""
    try:
        payload = _build_quickstart_payload(
            target=target,
            client=client,
            hosted_url=hosted_url,
            bearer_token_env_var=bearer_token_env_var,
            repo_root=repo_root,
        )
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))

    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2))
        return
    for line in _render_quickstart_payload(payload):
        typer.echo(line)


@app.command(
    help=(
        "Inspect local setup, index artifacts, and MCP launch hints without "
        "calling NVIDIA-hosted APIs."
    ),
)
def doctor(
    output_format: Annotated[
        Literal["text", "json"],
        typer.Option(
            "--format",
            help="Diagnostic output format. Supported values: text, json.",
        ),
    ] = "text",
) -> None:
    """Print a safe local setup diagnostic report."""
    report = _build_doctor_report()
    if output_format == "json":
        typer.echo(json.dumps(report, indent=2))
        return
    for line in _render_doctor_report(report):
        typer.echo(line)


@app.command()
def ingest() -> None:
    """Build the local policy index from the shipped corpus."""
    service = None
    try:
        settings = _load_setup_dependent_settings()
        service = create_ingest_service(settings)
        result = service.run()
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

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
    service = None
    try:
        settings = _load_setup_dependent_settings()
        service = create_index_dump_service(settings)
        chunks = service.list_chunks()
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    finally:
        _close_service(service)

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
                    include_chunk_text=trace,
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
    output_format: Annotated[
        Literal["json", "markdown"],
        typer.Option(
            "--format",
            help="Report format. Supported values: json, markdown.",
        ),
    ] = "json",
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            help="Optional file path to write the rendered report.",
        ),
    ] = None,
) -> None:
    """Summarize one runtime evidence session from SQLite-backed storage."""
    service = None
    rendered: str | None = None
    written_path: Path | None = None
    try:
        service = create_runtime_evidence_report_service(_load_setup_dependent_settings())
        result = service.report_session(session_id)
        rendered = _render_runtime_evidence_report(result, output_format=output_format)
        if output is not None:
            written_path = _write_cli_artifact_text(output, rendered)
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    finally:
        _close_service(service)

    if output is not None and written_path is not None:
        typer.echo(f"Wrote runtime evidence report to {written_path}.")
        return
    typer.echo(rendered)


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
        if transport != "stdio":
            _load_setup_dependent_settings()
        run_server(transport=transport)
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))


@app.command("mcp-config")
def mcp_config(
    client: Annotated[
        Literal["codex", "claude-code"],
        typer.Option(
            "--client",
            help="Client config shape to print. Supported values: codex, claude-code.",
        ),
    ] = "codex",
    target: Annotated[
        Literal["local-stdio", "hosted-http"],
        typer.Option(
            "--target",
            help="MCP target to configure. Supported values: local-stdio, hosted-http.",
        ),
    ] = "local-stdio",
    hosted_url: Annotated[
        str | None,
        typer.Option(
            "--hosted-url",
            help=(
                "Hosted PolicyNIM MCP URL, for example https://<host>/mcp. "
                "Required with --target hosted-http."
            ),
        ),
    ] = None,
    bearer_token_env_var: Annotated[
        str,
        typer.Option(
            "--bearer-token-env-var",
            help=(
                "Environment variable that stores the hosted MCP bearer token. "
                "Used with --target hosted-http."
            ),
        ),
    ] = "POLICYNIM_TOKEN",
    repo_root: Annotated[
        Path | None,
        typer.Option(
            "--repo-root",
            help=(
                "Optional PolicyNIM source checkout root. When omitted, local stdio "
                "config uses a discovered checkout or the installed policynim command."
            ),
        ),
    ] = None,
    server_name: Annotated[
        str,
        typer.Option(
            "--server-name",
            help="MCP server name to use in the generated client config.",
        ),
    ] = "policynim",
    uv_command: Annotated[
        str,
        typer.Option(
            "--uv-command",
            help=(
                "uv executable or absolute path to use in generated stdio config. "
                "Used only when local stdio config targets a source checkout."
            ),
        ),
    ] = "uv",
    output_format: Annotated[
        Literal["text", "json"],
        typer.Option(
            "--format",
            help="Config output format. Supported values: text, json.",
        ),
    ] = "text",
) -> None:
    """Print hosted HTTP or local stdio MCP client config without writing client files."""
    try:
        payload = _build_mcp_config_payload(
            client=client,
            target=target,
            hosted_url=hosted_url,
            bearer_token_env_var=bearer_token_env_var,
            repo_root=repo_root,
            server_name=server_name,
            uv_command=uv_command,
        )
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))

    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2))
        return
    for line in _render_mcp_config_payload(payload):
        typer.echo(line)


@app.command("mcp-smoke")
def mcp_smoke(
    output_format: Annotated[
        Literal["text", "json"],
        typer.Option(
            "--format",
            help="Smoke output format. Supported values: text, json.",
        ),
    ] = "text",
    timeout_seconds: Annotated[
        float,
        typer.Option(
            "--timeout-seconds",
            min=1.0,
            help="Read timeout for MCP initialize and list-tools calls.",
        ),
    ] = 5.0,
    mcp_config_file: Annotated[
        Path | None,
        typer.Option(
            "--mcp-config-file",
            help=(
                "Optional JSON output from `policynim mcp-config --format json`. "
                "When set, local stdio smoke launches with that generated config."
            ),
        ),
    ] = None,
) -> None:
    """Launch the local stdio MCP server and verify the public tools are listed."""
    try:
        if mcp_config_file is None:
            report = asyncio.run(_run_mcp_stdio_smoke(timeout_seconds=timeout_seconds))
        else:
            command, cwd = _mcp_stdio_smoke_command_from_config_file(mcp_config_file)
            report = asyncio.run(
                _run_mcp_stdio_smoke(
                    timeout_seconds=timeout_seconds,
                    command=command,
                    cwd=cwd,
                )
            )
            report["config_source"] = str(mcp_config_file)
    except PolicyNIMError as exc:
        _exit_with_error(_cli_error_message(exc))
    except ValueError as exc:
        _exit_with_error(str(exc))

    if output_format == "json":
        typer.echo(json.dumps(report, indent=2))
    else:
        for line in _render_mcp_smoke_report(report):
            typer.echo(line)

    if report["status"] != "ok":
        raise typer.Exit(code=1)


@app.command("support-bundle")
def support_bundle(
    output_format: Annotated[
        Literal["json", "markdown"],
        typer.Option(
            "--format",
            help="Support bundle output format. Supported values: json, markdown.",
        ),
    ] = "json",
    include_mcp_smoke: Annotated[
        bool,
        typer.Option(
            "--include-mcp-smoke",
            help="Also launch local stdio MCP and verify public tool registration.",
        ),
    ] = False,
    mcp_timeout_seconds: Annotated[
        float,
        typer.Option(
            "--mcp-timeout-seconds",
            min=1.0,
            help="Read timeout for the optional MCP smoke check.",
        ),
    ] = 5.0,
    include_local_paths: Annotated[
        bool,
        typer.Option(
            "--include-local-paths",
            help=(
                "Include exact local filesystem paths for private maintainer triage. "
                "By default, local path prefixes are redacted for public issues."
            ),
        ),
    ] = False,
) -> None:
    """Print redacted diagnostics suitable for issues and maintainer triage."""
    try:
        bundle = asyncio.run(
            _build_support_bundle(
                include_mcp_smoke=include_mcp_smoke,
                mcp_timeout_seconds=mcp_timeout_seconds,
                include_local_paths=include_local_paths,
            )
        )
    except ValueError as exc:
        _exit_with_error(str(exc))

    rendered = json.dumps(bundle, indent=2)
    if output_format == "markdown":
        typer.echo(_render_support_bundle_markdown(rendered))
        return
    typer.echo(rendered)


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


@beta_admin_app.command("audit-log")
def beta_admin_audit_log(
    github_login: Annotated[
        str | None,
        typer.Option("--github-login", help="Filter audit events by GitHub login."),
    ] = None,
    event_type: Annotated[
        str | None,
        typer.Option("--event-type", help="Filter audit events by event type."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, help="Maximum audit events to return."),
    ] = 50,
) -> None:
    """Print hosted beta audit events as JSON."""
    service = None
    try:
        service = create_beta_auth_service(get_settings())
        events = service.list_audit_events(
            github_login=github_login,
            event_type=event_type,
            limit=limit,
        )
    except PolicyNIMError as exc:
        _exit_with_error(str(exc))
    finally:
        _close_service(service)

    typer.echo(json.dumps([event.model_dump(mode="json") for event in events], indent=2))


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


def _render_runtime_evidence_report(
    summary: RuntimeEvidenceSessionSummary,
    *,
    output_format: Literal["json", "markdown"],
) -> str:
    """Render one runtime evidence summary in the requested CLI format."""
    if output_format == "json":
        return summary.model_dump_json(indent=2)
    return _runtime_evidence_report_markdown(summary)


def _runtime_evidence_report_markdown(summary: RuntimeEvidenceSessionSummary) -> str:
    """Render one runtime evidence session summary as Markdown."""
    lines = [
        f"# Runtime Evidence Report: {summary.session_id}",
        "",
        f"- Started: {summary.started_at.isoformat()}",
        f"- Completed: {_optional_iso(summary.completed_at)}",
        f"- Events: {summary.event_count}",
        f"- Executions: {summary.execution_count}",
        (
            "- Outcomes: "
            f"allowed={summary.allowed_count}, "
            f"confirmed={summary.confirmed_count}, "
            f"blocked={summary.blocked_count}, "
            f"refused={summary.refused_count}, "
            f"failed={summary.failed_count}, "
            f"incomplete={summary.incomplete_count}"
        ),
        "",
        "## Executions",
        "",
    ]
    if not summary.executions:
        lines.append("No executions recorded.")
        return "\n".join(lines)

    lines.extend(
        [
            (
                "| Execution | Action | Decision | Outcome | Confirmation | "
                "Started | Completed | Failure |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for execution in summary.executions:
        lines.append(
            " | ".join(
                [
                    f"| {_markdown_cell(execution.execution_id)}",
                    _markdown_cell(execution.action_kind),
                    _markdown_cell(execution.decision),
                    _markdown_cell(execution.execution_outcome or "incomplete"),
                    _markdown_cell(execution.confirmation_outcome),
                    _markdown_cell(execution.started_at.isoformat()),
                    _markdown_cell(_optional_iso(execution.completed_at)),
                    f"{_markdown_cell(execution.failure_class or '')} |",
                ]
            )
        )
    return "\n".join(lines)


def _optional_iso(value: datetime | None) -> str:
    """Return an ISO timestamp or the CLI placeholder for missing timestamps."""
    if value is None:
        return "N/A"
    return value.isoformat()


def _markdown_cell(value: object) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return str(value).replace("\n", " ").replace("|", "\\|")


def _write_cli_artifact_text(output: str, content: str) -> Path:
    """Write rendered CLI artifact text to a user-provided path atomically."""
    output_value = output.strip()
    if not output_value:
        raise PolicyNIMError("Runtime evidence report output path must not be empty.")

    target = resolve_runtime_path(Path(output_value))
    if target.exists() and target.is_dir():
        raise PolicyNIMError(f"Runtime evidence report output must be a file path: {target}.")

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            temp_file.write(content)
            temp_file.write("\n")
        os.replace(temp_path, target)
    except OSError as exc:
        cleanup_note = ""
        if temp_path is not None:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError as cleanup_exc:
                cleanup_note = f" Cleanup of staged file {temp_path} also failed: {cleanup_exc}."
        raise PolicyNIMError(
            f"Could not write runtime evidence report to {target}: {exc}.{cleanup_note}"
        ) from exc
    return target


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


def _build_quickstart_payload(
    *,
    target: Literal["hosted-mcp", "local-cli", "local-mcp"],
    client: Literal["codex", "claude-code"],
    hosted_url: str,
    bearer_token_env_var: str,
    repo_root: Path | None,
) -> dict[str, object]:
    """Build offline first-run guidance for the requested workflow target."""
    normalized_hosted_url = _normalize_hosted_mcp_url(
        _normalize_quickstart_value(hosted_url, label="Hosted MCP URL")
    )
    normalized_token_env_var = _normalize_env_var_name(bearer_token_env_var)

    if target == "hosted-mcp":
        hosted_url_placeholder = _is_placeholder_hosted_url(normalized_hosted_url)
        beta_portal_url = _hosted_beta_portal_url(normalized_hosted_url)
        alternate_client = _alternate_mcp_client(client)
        alternate_client_name = _mcp_client_display_name(alternate_client)
        alternate_client_quickstart = _quickstart_installed_cli_command(
            ["quickstart", "--target", target, "--client", alternate_client]
        )
        next_steps = [
            (f"For {alternate_client_name} setup commands, rerun `{alternate_client_quickstart}`."),
            "Ask your client to call `policy_preflight` for the main workflow.",
            "Use `policy_search` first when you need raw retrieval/debugging context.",
        ]
        if hosted_url_placeholder:
            next_steps.insert(
                0,
                "Replace the hosted URL placeholder with the deployed /mcp URL.",
            )
        return {
            "schema_version": "1",
            "target": target,
            "client": client,
            "requires_local_setup": False,
            "calls_external_services": False,
            "hosted_url": normalized_hosted_url,
            "hosted_url_placeholder": hosted_url_placeholder,
            "beta_portal_url": beta_portal_url,
            "description": "Shortest path: use the hosted MCP endpoint without cloning.",
            "prerequisites": [
                "Access to the hosted beta portal.",
                f"A shell environment variable named {normalized_token_env_var}.",
            ],
            "steps": [
                (
                    f"Open {normalized_hosted_url} in a browser; it routes to "
                    f"{beta_portal_url} for token creation."
                ),
                "Sign in with GitHub.",
                "Generate or rotate a hosted API key.",
                "Export the token and add the hosted MCP server to your client.",
            ],
            "commands": [
                f"export {normalized_token_env_var}='<generated-beta-token>'",
                _quickstart_installed_cli_command(
                    [
                        "mcp-config",
                        "--target",
                        "hosted-http",
                        "--client",
                        client,
                        "--hosted-url",
                        normalized_hosted_url,
                        "--bearer-token-env-var",
                        normalized_token_env_var,
                    ]
                ),
            ],
            "client_commands": _quickstart_hosted_client_commands(
                client=client,
                hosted_url=normalized_hosted_url,
                bearer_token_env_var=normalized_token_env_var,
            ),
            "agent_workflows": _quickstart_agent_workflows(),
            "next_steps": next_steps,
            "safety": [
                "This command does not call hosted or NVIDIA services.",
                (
                    "Keep bearer tokens in environment variables; "
                    "do not paste token values into issues."
                ),
            ],
        }

    if target == "local-cli":
        local_cli_command = _quickstart_cli_command
        source_checkout = _doctor_runtime_mode() == "source_checkout"
        local_cli_description = (
            "Local CLI path for running preflight from a source checkout."
            if source_checkout
            else "Local CLI path for running preflight from an installed package."
        )
        local_cli_prerequisites = (
            [
                "PolicyNIM source checkout with uv dependencies synced.",
                "NVIDIA_API_KEY for ingest, search, route, compile, preflight, and eval.",
            ]
            if source_checkout
            else [
                "Python 3.11 or 3.12 for PyPI package installs.",
                "NVIDIA_API_KEY for ingest, search, route, compile, preflight, and eval.",
                "PyPI trusted-publishing evidence is tracked separately from installability.",
            ]
        )
        entrypoint_step = (
            "Check the source-checkout entrypoint."
            if source_checkout
            else "Check the installed entrypoint."
        )
        return {
            "schema_version": "1",
            "target": target,
            "client": client,
            "requires_local_setup": True,
            "calls_external_services": False,
            "description": local_cli_description,
            "prerequisites": local_cli_prerequisites,
            "steps": [
                entrypoint_step,
                "Create local config.",
                "Build the bundled policy index.",
                "Run a citation-backed preflight.",
            ],
            "commands": [
                local_cli_command(["--help"]),
                local_cli_command(["doctor"]),
                local_cli_command(["init"]),
                local_cli_command(["ingest"]),
                local_cli_command(
                    [
                        "preflight",
                        "--task",
                        "Implement a refresh-token cleanup background job",
                        "--top-k",
                        "5",
                    ]
                ),
            ],
            "agent_workflows": _quickstart_agent_workflows(),
            "next_steps": [
                (
                    f"Run `{local_cli_command(['quickstart', '--target', 'hosted-mcp'])}` "
                    "if you want MCP without local setup."
                ),
                (
                    f"Run `{local_cli_command(['support-bundle'])}` when attaching public setup "
                    "evidence or opening an issue."
                ),
                (
                    f"Keep `{local_cli_command(['doctor', '--format', 'json'])}` "
                    "local unless a maintainer "
                    "asks for raw paths in private triage."
                ),
            ],
            "safety": [
                "This command only prints guidance; it does not write config.",
                "Secret values are not printed.",
            ],
        }

    local_command = _quickstart_local_mcp_command
    local_config = _quickstart_local_mcp_config(
        client=client,
        repo_root=repo_root,
    )
    local_config_command = cast(list[str], local_config["command"])
    local_launch_mode = str(local_config["mode"])
    config_step = (
        "Generate client config from the checkout path."
        if local_launch_mode == "source-checkout"
        else "Generate client config from the installed entrypoint."
    )
    local_mcp_description = (
        "Local MCP path for Codex or Claude Code from a source checkout."
        if local_launch_mode == "source-checkout"
        else "Local MCP path for Codex or Claude Code from an installed CLI."
    )
    local_mcp_prerequisites = (
        [
            "PolicyNIM source checkout with uv dependencies synced.",
            "NVIDIA_API_KEY available in the shell that launches the MCP client.",
        ]
        if local_launch_mode == "source-checkout"
        else [
            "An installed PolicyNIM CLI.",
            "NVIDIA_API_KEY available in the shell that launches the MCP client.",
        ]
    )
    return {
        "schema_version": "1",
        "target": target,
        "client": client,
        "local_launch_mode": local_launch_mode,
        "requires_local_setup": True,
        "calls_external_services": False,
        "description": local_mcp_description,
        "prerequisites": local_mcp_prerequisites,
        "steps": [
            "Confirm local setup state.",
            "Create local config if needed.",
            "Build the policy index.",
            "Verify the stdio MCP server lists public tools.",
            config_step,
        ],
        "commands": [
            local_command("doctor"),
            local_command("init"),
            local_command("ingest"),
            local_command("mcp-smoke"),
            _shell_join(local_config_command),
        ],
        "agent_workflows": _quickstart_agent_workflows(),
        "next_steps": [
            "Load the generated config in your MCP client.",
            "Ask the client to list `policy_preflight` and `policy_search`.",
        ],
        "safety": [
            "This command does not launch the MCP server.",
            "Keep NVIDIA_API_KEY in environment variables.",
            (
                "Local MCP setup commands can include exact local filesystem paths; "
                "do not paste them into public issues."
            ),
            "Use `policynim support-bundle` for public diagnostics.",
        ],
    }


def _quickstart_cli_command(parts: Sequence[str]) -> str:
    """Render a shell command for the active local CLI runtime."""
    if _doctor_runtime_mode() == "source_checkout":
        return _shell_join(["uv", "run", "policynim", *parts])
    return _shell_join(["policynim", *parts])


def _quickstart_agent_workflows() -> list[dict[str, str]]:
    """Return copyable first-use prompts for coding-agent workflows."""
    return [
        {
            "title": workflow["title"],
            "tool": workflow["tool"],
            "prompt": workflow["prompt"],
        }
        for workflow in agent_workflows()
    ]


def _alternate_mcp_client(
    client: Literal["codex", "claude-code"],
) -> Literal["codex", "claude-code"]:
    """Return the other supported MCP client for first-run hints."""
    if client == "codex":
        return "claude-code"
    return "codex"


def _mcp_client_display_name(client: Literal["codex", "claude-code"]) -> str:
    """Render supported MCP client names for user-facing guidance."""
    if client == "claude-code":
        return "Claude Code"
    return "Codex"


def _quickstart_hosted_client_commands(
    *,
    client: Literal["codex", "claude-code"],
    hosted_url: str,
    bearer_token_env_var: str,
) -> list[str]:
    """Return the direct hosted MCP client command for quickstart output."""
    payload = _build_hosted_mcp_config_payload(
        client=client,
        hosted_url=hosted_url,
        bearer_token_env_var=bearer_token_env_var,
        server_name="policynim",
    )
    command_key = "cli_shell_command" if client == "claude-code" else "codex_cli_shell_command"
    return [str(payload[command_key])]


def _quickstart_all_hosted_client_commands(
    *,
    hosted_url: str,
    bearer_token_env_var: str,
) -> list[str]:
    """Return hosted MCP setup commands for every supported client."""
    commands: list[str] = []
    for client in ("codex", "claude-code"):
        commands.extend(
            _quickstart_hosted_client_commands(
                client=client,
                hosted_url=hosted_url,
                bearer_token_env_var=bearer_token_env_var,
            )
        )
    return commands


def _quickstart_installed_cli_command(parts: Sequence[str]) -> str:
    """Render a shell command that always targets the installed CLI."""
    return _shell_join(["policynim", *parts])


def _quickstart_local_mcp_command(command: str) -> str:
    """Render a local MCP setup command for quickstart output."""
    return _doctor_cli_command(command)


def _quickstart_local_mcp_config(
    *,
    client: Literal["codex", "claude-code"],
    repo_root: Path | None,
) -> dict[str, object]:
    """Build a local stdio MCP config command description for quickstart."""
    command = [
        *_doctor_cli_command("mcp-config").split(),
        "--target",
        "local-stdio",
        "--client",
        client,
    ]
    if repo_root is not None:
        return {
            "mode": "source-checkout",
            "command": [
                *command,
                "--repo-root",
                str(repo_root.expanduser().resolve(strict=False)),
            ],
        }
    discovered = config_discovery.find_source_checkout_root()
    if discovered is not None:
        return {"mode": "source-checkout", "command": [*command, "--repo-root", str(discovered)]}
    return {"mode": "installed-cli", "command": command}


def _hosted_beta_portal_url(hosted_mcp_url: str) -> str:
    """Derive the hosted beta portal URL from a hosted MCP URL."""
    parsed = urlparse(hosted_mcp_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/beta"
    return "https://<railway-domain>/beta"


def _build_mcp_config_payload(
    *,
    client: Literal["codex", "claude-code"],
    target: Literal["local-stdio", "hosted-http"],
    hosted_url: str | None,
    bearer_token_env_var: str,
    repo_root: Path | None,
    server_name: str,
    uv_command: str,
) -> dict[str, object]:
    """Build client-specific MCP configuration without writing client files."""
    _validate_mcp_config_target_options(
        target=target,
        repo_root=repo_root,
        hosted_url=hosted_url,
        bearer_token_env_var=bearer_token_env_var,
        uv_command=uv_command,
    )
    normalized_server_name = _normalize_mcp_config_value(
        server_name,
        label="MCP server name",
    )
    if target == "hosted-http":
        return _build_hosted_mcp_config_payload(
            client=client,
            hosted_url=_normalize_hosted_mcp_url(hosted_url),
            bearer_token_env_var=_normalize_env_var_name(bearer_token_env_var),
            server_name=normalized_server_name,
        )

    launch = _resolve_local_mcp_launch(repo_root=repo_root, uv_command=uv_command)
    launch_mode = str(launch["mode"])
    command = str(launch["command"])
    args = cast(list[str], launch["args"])
    repo_root_value = cast(str | None, launch.get("repo_root"))
    next_steps = cast(list[str], launch["next_steps"])
    safety = _local_mcp_config_safety()

    if client == "claude-code":
        server_config = {
            "type": "stdio",
            "command": command,
            "args": args,
            "env": {"NVIDIA_API_KEY": "${NVIDIA_API_KEY}"},
        }
        config = {"mcpServers": {normalized_server_name: server_config}}
        compact_config = json.dumps(server_config, separators=(",", ":"))
        payload = {
            "schema_version": "1",
            "client": client,
            "target": target,
            "server_name": normalized_server_name,
            "local_launch_mode": launch_mode,
            "config": config,
            "cli_command": [
                "claude",
                "mcp",
                "add-json",
                normalized_server_name,
                compact_config,
            ],
            "cli_shell_command": _shell_join(
                ["claude", "mcp", "add-json", normalized_server_name, compact_config]
            ),
            "next_steps": next_steps,
            "safety": safety,
        }
        if repo_root_value is not None:
            payload["repo_root"] = repo_root_value
        return payload

    codex_command = [
        "codex",
        "mcp",
        "add",
        normalized_server_name,
        "--env",
        "NVIDIA_API_KEY=$NVIDIA_API_KEY",
        "--",
        command,
        *args,
    ]
    codex_app = {
        "name": normalized_server_name,
        "transport": "STDIO",
        "command": command,
        "arguments": args,
        "environment_variable_passthrough": ["NVIDIA_API_KEY"],
    }
    if repo_root_value is not None:
        codex_app["working_directory"] = repo_root_value
    payload = {
        "schema_version": "1",
        "client": client,
        "target": target,
        "server_name": normalized_server_name,
        "local_launch_mode": launch_mode,
        "codex_cli_command": codex_command,
        "codex_cli_shell_command": _codex_cli_shell_command(
            server_name=normalized_server_name,
            command=command,
            args=args,
        ),
        "codex_app": codex_app,
        "next_steps": next_steps,
        "safety": safety,
    }
    if repo_root_value is not None:
        payload["repo_root"] = repo_root_value
    return payload


def _local_mcp_config_safety() -> list[str]:
    """Return safety notes shared by local MCP config payloads."""
    return [
        (
            "Generated local stdio config can include exact local filesystem paths; "
            "do not paste it into public issues."
        ),
        "Use `policynim support-bundle` for public diagnostics.",
    ]


def _resolve_local_mcp_launch(
    *,
    repo_root: Path | None,
    uv_command: str,
) -> dict[str, object]:
    """Resolve whether local MCP should launch from a checkout or installed CLI."""
    discovered_repo_root = _resolve_mcp_config_repo_root(repo_root)
    if discovered_repo_root is None:
        if uv_command != "uv":
            raise PolicyNIMError(
                "--uv-command requires a PolicyNIM source checkout. "
                "Pass --repo-root /ABS/PATH/TO/policyNIM."
            )
        return {
            "mode": "installed-cli",
            "command": "policynim",
            "args": ["mcp", "--transport", "stdio"],
            "next_steps": [
                "policynim doctor",
                "policynim mcp-smoke",
                "policynim ingest",
            ],
        }

    normalized_uv_command = _normalize_mcp_config_value(uv_command, label="uv command")
    return {
        "mode": "source-checkout",
        "command": normalized_uv_command,
        "args": [
            "run",
            "--directory",
            str(discovered_repo_root),
            "policynim",
            "mcp",
            "--transport",
            "stdio",
        ],
        "repo_root": str(discovered_repo_root),
        "next_steps": [
            "uv run policynim doctor",
            "uv run policynim mcp-smoke",
            "uv run policynim ingest",
        ],
    }


def _validate_mcp_config_target_options(
    *,
    target: Literal["local-stdio", "hosted-http"],
    repo_root: Path | None,
    hosted_url: str | None,
    bearer_token_env_var: str,
    uv_command: str,
) -> None:
    """Reject option combinations that would produce misleading MCP config."""
    if target == "hosted-http":
        if repo_root is not None:
            raise PolicyNIMError("--repo-root is only valid with --target local-stdio.")
        if uv_command != "uv":
            raise PolicyNIMError("--uv-command is only valid with --target local-stdio.")
        return

    if hosted_url is not None:
        raise PolicyNIMError("--hosted-url is only valid with --target hosted-http.")
    if bearer_token_env_var != "POLICYNIM_TOKEN":
        raise PolicyNIMError("--bearer-token-env-var is only valid with --target hosted-http.")


def _build_hosted_mcp_config_payload(
    *,
    client: Literal["codex", "claude-code"],
    hosted_url: str,
    bearer_token_env_var: str,
    server_name: str,
) -> dict[str, object]:
    """Build hosted HTTP MCP config for Codex or Claude Code."""
    hosted_url_placeholder = _is_placeholder_hosted_url(hosted_url)
    beta_portal_url = _hosted_beta_portal_url(hosted_url)
    next_steps = [
        f"Open {beta_portal_url} to generate or rotate a hosted API key.",
        f"Export {bearer_token_env_var}='<generated-beta-token>'",
        "Ask your client to call `policy_preflight` for the main workflow.",
        "Use `policy_search` first when you need raw retrieval/debugging context.",
    ]
    if hosted_url_placeholder:
        next_steps.insert(
            0,
            "Replace the hosted URL placeholder with the deployed /mcp URL.",
        )
    if client == "claude-code":
        header = f"Authorization: Bearer ${bearer_token_env_var}"
        command = [
            "claude",
            "mcp",
            "add",
            "--transport",
            "http",
            server_name,
            hosted_url,
            "--header",
            header,
        ]
        return {
            "schema_version": "1",
            "client": client,
            "target": "hosted-http",
            "server_name": server_name,
            "hosted_url": hosted_url,
            "beta_portal_url": beta_portal_url,
            "hosted_url_placeholder": hosted_url_placeholder,
            "bearer_token_env_var": bearer_token_env_var,
            "cli_command": command,
            "cli_shell_command": _claude_hosted_shell_command(
                server_name=server_name,
                hosted_url=hosted_url,
                bearer_token_env_var=bearer_token_env_var,
            ),
            "next_steps": next_steps,
        }

    command = [
        "codex",
        "mcp",
        "add",
        server_name,
        "--url",
        hosted_url,
        "--bearer-token-env-var",
        bearer_token_env_var,
    ]
    return {
        "schema_version": "1",
        "client": client,
        "target": "hosted-http",
        "server_name": server_name,
        "hosted_url": hosted_url,
        "beta_portal_url": beta_portal_url,
        "hosted_url_placeholder": hosted_url_placeholder,
        "bearer_token_env_var": bearer_token_env_var,
        "codex_cli_command": command,
        "codex_cli_shell_command": _shell_join(command),
        "next_steps": [
            *next_steps,
            f"Run `codex mcp get {server_name}` to inspect the saved server entry.",
        ],
    }


def _resolve_mcp_config_repo_root(repo_root: Path | None) -> Path | None:
    """Resolve and validate an optional source checkout root."""
    if repo_root is None:
        discovered = config_discovery.find_source_checkout_root()
        if discovered is None:
            return None
        candidate = discovered
    else:
        candidate = repo_root
    resolved = candidate.expanduser().resolve(strict=False)
    if not _looks_like_policynim_checkout(resolved):
        raise PolicyNIMError(
            "--repo-root must point to a PolicyNIM source checkout. "
            "Omit --repo-root to launch the installed CLI."
        )
    return resolved


def _looks_like_policynim_checkout(path: Path) -> bool:
    """Return whether a path has the expected PolicyNIM checkout shape."""
    return (path / "pyproject.toml").is_file() and (path / "src" / "policynim").is_dir()


def _normalize_mcp_config_value(value: str, *, label: str) -> str:
    """Trim and validate a required MCP config string value."""
    stripped = value.strip()
    if not stripped:
        raise PolicyNIMError(f"{label} cannot be empty.")
    return stripped


def _normalize_hosted_mcp_url(value: str | None) -> str:
    """Normalize a hosted MCP URL and enforce the /mcp endpoint shape."""
    if value is None or not value.strip():
        raise PolicyNIMError("Hosted MCP config requires --hosted-url https://<host>/mcp.")
    stripped = value.strip()
    parsed = urlparse(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PolicyNIMError(
            "Hosted MCP URL must start with http:// or https:// and include a host."
        )
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise PolicyNIMError("Hosted MCP URL must not include embedded credentials.")
    if parsed.path.rstrip("/") != "/mcp":
        raise PolicyNIMError("Hosted MCP URL must point to the /mcp endpoint.")
    return stripped


def _normalize_env_var_name(value: str) -> str:
    """Normalize and validate an environment variable name."""
    stripped = value.strip()
    if not stripped:
        raise PolicyNIMError("Bearer token env var cannot be empty.")
    if not (stripped[0].isalpha() or stripped[0] == "_"):
        raise PolicyNIMError("Bearer token env var must not start with a number.")
    if any(not (character.isalnum() or character == "_") for character in stripped):
        raise PolicyNIMError(
            "Bearer token env var must contain only letters, numbers, and underscores."
        )
    return stripped


def _normalize_quickstart_value(value: str, *, label: str) -> str:
    """Trim and validate a required quickstart string value."""
    stripped = value.strip()
    if not stripped:
        raise PolicyNIMError(f"{label} cannot be empty.")
    return stripped


def _is_placeholder_hosted_url(value: str) -> bool:
    """Return whether a hosted URL still contains unresolved placeholder text."""
    normalized = value.lower()
    return any(
        marker in normalized
        for marker in (
            "<",
            ">",
            "example.",
            ".example/",
            ".invalid",
            "todo",
            "placeholder",
        )
    )


def _render_mcp_config_payload(payload: dict[str, object]) -> list[str]:
    """Render an MCP config payload as terminal-friendly text."""
    if payload.get("target") == "hosted-http":
        return _render_hosted_mcp_config(payload)
    client = str(payload["client"])
    if client == "claude-code":
        return _render_claude_code_mcp_config(payload)
    return _render_codex_mcp_config(payload)


def _render_quickstart_payload(payload: dict[str, object]) -> list[str]:
    """Render a quickstart payload as terminal-friendly text."""
    return [
        "PolicyNIM quickstart",
        f"Target: {payload['target']}",
        f"Client: {payload['client']}",
        str(payload["description"]),
        "",
        "Prerequisites:",
        *_render_quickstart_list(payload, "prerequisites"),
        "",
        "Steps:",
        *_render_quickstart_numbered_list(payload, "steps"),
        "",
        "Commands:",
        *_render_quickstart_list(payload, "commands"),
        *_render_quickstart_client_commands(payload),
        "",
        "Agent workflows:",
        *_render_quickstart_agent_workflows(payload),
        "",
        "Next:",
        *_render_quickstart_list(payload, "next_steps"),
        "",
        "Safety:",
        *_render_quickstart_list(payload, "safety"),
    ]


def _render_quickstart_list(payload: dict[str, object], key: str) -> list[str]:
    """Render one quickstart list field as bullet lines."""
    values = payload.get(key, [])
    if not isinstance(values, list):
        return []
    return [f"- {value}" for value in values]


def _render_quickstart_numbered_list(payload: dict[str, object], key: str) -> list[str]:
    """Render one quickstart list field as numbered steps."""
    values = payload.get(key, [])
    if not isinstance(values, list):
        return []
    return [f"{index}. {value}" for index, value in enumerate(values, start=1)]


def _render_quickstart_client_commands(payload: dict[str, object]) -> list[str]:
    """Render optional direct MCP client setup commands."""
    lines = _render_quickstart_list(payload, "client_commands")
    if not lines:
        return []
    return ["", "Client setup:", *lines]


def _render_quickstart_agent_workflows(payload: dict[str, object]) -> list[str]:
    """Render quickstart agent workflow cards as terminal-friendly prompts."""
    values = payload.get("agent_workflows", [])
    if not isinstance(values, list):
        return []
    lines: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        title = value.get("title")
        prompt = value.get("prompt")
        if isinstance(title, str) and isinstance(prompt, str):
            lines.append(f"- {title}: {prompt}")
    return lines


def _render_hosted_mcp_config(payload: dict[str, object]) -> list[str]:
    """Render hosted HTTP MCP client setup guidance."""
    client_label = "Claude Code" if payload["client"] == "claude-code" else "Codex"
    command_label = "Claude Code CLI" if payload["client"] == "claude-code" else "Codex CLI"
    lines = [
        f"PolicyNIM hosted MCP config for {client_label}",
        f"Hosted URL: {payload['hosted_url']}",
        f"Bearer token env var: {payload['bearer_token_env_var']}",
        "",
        f"{command_label}:",
        str(payload["cli_shell_command"])
        if payload["client"] == "claude-code"
        else str(payload["codex_cli_shell_command"]),
    ]
    return [*lines, *_render_mcp_config_next_steps(payload)]


def _render_claude_code_mcp_config(payload: dict[str, object]) -> list[str]:
    """Render local stdio MCP setup guidance for Claude Code."""
    config = payload["config"]
    lines = [
        "PolicyNIM MCP config for Claude Code",
        f"Launch mode: {payload['local_launch_mode']}",
    ]
    if "repo_root" in payload:
        lines.append(f"Repo root: {payload['repo_root']}")
    lines.extend(
        [
            "",
            "Project-scoped .mcp.json:",
            json.dumps(config, indent=2),
            "",
            "Claude Code CLI:",
            str(payload["cli_shell_command"]),
        ]
    )
    return [*lines, *_render_mcp_config_next_steps(payload)]


def _render_codex_mcp_config(payload: dict[str, object]) -> list[str]:
    """Render local stdio MCP setup guidance for Codex."""
    codex_app = payload["codex_app"]
    if not isinstance(codex_app, dict):
        raise PolicyNIMError("Generated Codex app config was malformed.")
    arguments = codex_app.get("arguments", [])
    if not isinstance(arguments, list):
        raise PolicyNIMError("Generated Codex app arguments were malformed.")
    lines = [
        "PolicyNIM MCP config for Codex",
        f"Launch mode: {payload['local_launch_mode']}",
    ]
    if "repo_root" in payload:
        lines.append(f"Repo root: {payload['repo_root']}")
    lines.extend(
        [
            "",
            "Codex CLI:",
            str(payload["codex_cli_shell_command"]),
            "",
            "Codex App:",
            f"Name: {codex_app.get('name')}",
            f"Transport: {codex_app.get('transport')}",
            f"Command to launch: {codex_app.get('command')}",
            "Arguments:",
            *[f"- {argument}" for argument in arguments],
        ]
    )
    working_directory = codex_app.get("working_directory")
    if working_directory is not None:
        lines.append(f"Working directory: {working_directory}")
    lines.append("Environment variable passthrough: NVIDIA_API_KEY")
    return [*lines, *_render_mcp_config_next_steps(payload)]


def _render_mcp_config_next_steps(payload: dict[str, object]) -> list[str]:
    """Render shared MCP config next-step and safety sections."""
    next_steps = payload.get("next_steps", [])
    safety = payload.get("safety", [])
    lines: list[str] = []
    if not isinstance(next_steps, list):
        next_steps = []
    if next_steps:
        lines.extend(["", "Before using this config:", *[f"- {step}" for step in next_steps]])
    if isinstance(safety, list) and safety:
        lines.extend(["", "Safety:", *[f"- {item}" for item in safety]])
    return lines


def _shell_join(command: Sequence[str]) -> str:
    """Render argv as a shell-safe command string."""
    return shlex.join(command)


def _claude_hosted_shell_command(
    *,
    server_name: str,
    hosted_url: str,
    bearer_token_env_var: str,
) -> str:
    """Render the hosted Claude Code MCP command without exposing token values."""
    prefix = _shell_join(
        [
            "claude",
            "mcp",
            "add",
            "--transport",
            "http",
            server_name,
            hosted_url,
            "--header",
        ]
    )
    return f'{prefix} "Authorization: Bearer ${bearer_token_env_var}"'


def _codex_cli_shell_command(
    *,
    server_name: str,
    command: str,
    args: Sequence[str],
) -> str:
    """Render the Codex CLI command for a local stdio MCP server."""
    prefix = _shell_join(["codex", "mcp", "add", server_name, "--env"])
    launch = _shell_join([command, *args])
    return f"{prefix} NVIDIA_API_KEY=$NVIDIA_API_KEY -- {launch}"


async def _build_support_bundle(
    *,
    include_mcp_smoke: bool,
    mcp_timeout_seconds: float,
    include_local_paths: bool,
) -> dict[str, object]:
    """Build public or private diagnostics for maintainer support."""
    bundle: dict[str, object] = {
        "schema_version": "1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "policynim_version": _safe_installed_version(),
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "first_run": _build_support_bundle_first_run_report(),
        "doctor": _build_doctor_report(),
        "mcp_smoke": {
            "status": "skipped",
            "reason": ("Pass --include-mcp-smoke to verify stdio tool registration."),
        },
        "redaction": {
            "secrets_included": False,
            "local_paths": "included" if include_local_paths else "redacted",
            "path_markers": [],
            "note": (
                "Secret values are not printed. Local paths are included for private "
                "maintainer triage."
                if include_local_paths
                else (
                    "Secret values are not printed; local path prefixes are redacted "
                    "for public issues. Pass --include-local-paths only for private "
                    "maintainer triage."
                )
            ),
        },
    }
    if include_mcp_smoke:
        bundle["mcp_smoke"] = await _run_mcp_stdio_smoke(timeout_seconds=mcp_timeout_seconds)
    if include_local_paths:
        return bundle

    redacted_bundle, markers = _redact_support_bundle_paths(bundle)
    redaction = redacted_bundle.get("redaction")
    if isinstance(redaction, dict):
        redaction["path_markers"] = markers
    return redacted_bundle


def _build_support_bundle_first_run_report() -> dict[str, object]:
    """Build quickstart summaries for all first-run targets."""
    targets: dict[str, object] = {}
    target_specs: tuple[
        tuple[str, Literal["hosted-mcp", "local-cli", "local-mcp"]],
        ...,
    ] = (
        ("hosted_mcp", "hosted-mcp"),
        ("local_cli", "local-cli"),
        ("local_mcp", "local-mcp"),
    )
    for key, target in target_specs:
        payload = _build_quickstart_payload(
            target=target,
            client="codex",
            hosted_url="https://<railway-domain>/mcp",
            bearer_token_env_var="POLICYNIM_TOKEN",
            repo_root=None,
        )
        targets[key] = _support_bundle_quickstart_summary(
            payload,
            quickstart_command=_support_bundle_quickstart_command(target),
        )

    return {
        "runtime_mode": _doctor_runtime_mode(),
        "default_target": "hosted-mcp",
        "targets": targets,
    }


def _support_bundle_quickstart_command(
    target: Literal["hosted-mcp", "local-cli", "local-mcp"],
) -> str:
    """Return the command that reproduces one quickstart target."""
    command = f"quickstart --target {target}"
    if target == "local-mcp":
        command = f"{command} --client codex"
    if target == "hosted-mcp":
        return _quickstart_installed_cli_command([*command.split(), "--format", "json"])
    return _doctor_cli_command(f"{command} --format json")


def _support_bundle_quickstart_summary(
    payload: dict[str, object],
    *,
    quickstart_command: str,
) -> dict[str, object]:
    """Extract support-bundle-safe quickstart fields."""
    summary: dict[str, object] = {
        "target": payload["target"],
        "description": payload["description"],
        "quickstart_command": quickstart_command,
        "requires_local_setup": payload["requires_local_setup"],
        "calls_external_services": payload["calls_external_services"],
        "steps": payload["steps"],
        "commands": payload["commands"],
        "agent_workflows": payload["agent_workflows"],
        "next_steps": payload["next_steps"],
        "safety": payload["safety"],
    }
    for optional_key in (
        "hosted_url",
        "beta_portal_url",
        "hosted_url_placeholder",
        "local_launch_mode",
        "client_commands",
    ):
        if optional_key in payload:
            summary[optional_key] = payload[optional_key]
    if payload["target"] == "hosted-mcp":
        summary["client_commands"] = _quickstart_all_hosted_client_commands(
            hosted_url=str(payload["hosted_url"]),
            bearer_token_env_var="POLICYNIM_TOKEN",
        )
    return summary


def _redact_support_bundle_paths(bundle: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Redact local path prefixes from a support bundle."""
    replacements = _support_bundle_path_replacements(bundle)
    redacted = _replace_local_path_prefixes(bundle, replacements)
    markers = sorted({marker for _, marker in replacements})
    return cast(dict[str, object], redacted), markers


def _support_bundle_path_replacements(
    bundle: dict[str, object] | None = None,
) -> list[tuple[str, str]]:
    """Return local path prefixes and the markers that should replace them."""
    candidates: list[tuple[Path, str]] = [
        (Path(sys.executable), "<python-executable>"),
        (Path.home(), "<home>"),
    ]
    source_root = config_discovery.find_source_checkout_root()
    if source_root is not None:
        candidates.append((source_root, "<repo-root>"))

    standalone = config_discovery.standalone_paths()
    candidates.extend(
        [
            (standalone.config_file.parent, "<config-dir>"),
            (standalone.data_root, "<data-dir>"),
        ]
    )

    replacements: dict[str, str] = {}
    if sys.executable:
        replacements[sys.executable] = "<python-executable>"
    for path, marker in candidates:
        for candidate in (
            path.expanduser().as_posix(),
            path.expanduser().resolve(strict=False).as_posix(),
        ):
            if candidate and candidate != "/":
                replacements[candidate] = marker

    if bundle is not None:
        for path_text in _support_bundle_discovered_path_prefixes(bundle, replacements):
            replacements.setdefault(path_text, "<local-path>")
    return sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True)


def _support_bundle_discovered_path_prefixes(
    bundle: dict[str, object],
    known_replacements: dict[str, str],
) -> list[str]:
    """Find additional local path prefixes embedded in support-bundle values."""
    prefixes: set[str] = set()
    for path_text in _support_bundle_absolute_path_strings(bundle):
        if _support_bundle_path_is_known(path_text, known_replacements):
            continue
        path_prefix = _support_bundle_redaction_prefix(path_text)
        if path_prefix is None or _support_bundle_path_is_known(path_prefix, known_replacements):
            continue
        prefixes.add(path_prefix)
    return sorted(prefixes, key=len, reverse=True)


def _support_bundle_absolute_path_strings(value: object) -> list[str]:
    """Extract path-like absolute strings from a support-bundle value."""
    paths: set[str] = set()
    if isinstance(value, str):
        paths.update(_support_bundle_absolute_path_tokens(value))
    elif isinstance(value, list):
        for item in value:
            paths.update(_support_bundle_absolute_path_strings(item))
    elif isinstance(value, dict):
        for item in value.values():
            paths.update(_support_bundle_absolute_path_strings(item))
    return sorted(paths, key=len, reverse=True)


def _support_bundle_absolute_path_tokens(value: str) -> list[str]:
    """Return absolute path tokens found inside a support-bundle string."""
    tokens = [value]
    try:
        tokens.extend(shlex.split(value))
    except ValueError:
        tokens.extend(value.split())
    tokens.extend(match.group(2) for match in _POSIX_PATH_TOKEN_RE.finditer(value))

    paths: set[str] = set()
    for token in tokens:
        candidate = token.strip("`'\".,;:()[]{}")
        if _support_bundle_token_is_absolute_path(candidate):
            paths.add(candidate)
    return sorted(paths, key=len, reverse=True)


def _support_bundle_token_is_absolute_path(value: str) -> bool:
    """Return whether a token looks like an absolute local filesystem path."""
    if "/ABS/PATH/" in value:
        return False
    if value.startswith("/") and not value.startswith("//"):
        return len(Path(value).parts) >= 3
    return len(value) >= 3 and value[0].isalpha() and value[1] == ":" and value[2] in {"\\", "/"}


def _support_bundle_redaction_prefix(path_text: str) -> str | None:
    """Return the parent prefix to redact for an absolute path string."""
    if path_text.startswith("/"):
        path = Path(path_text).expanduser()
        parent = path.parent
        if parent == path or parent.as_posix() == "/":
            return None
        return parent.as_posix()
    return None


def _support_bundle_path_is_known(
    path_text: str,
    replacements: dict[str, str],
) -> bool:
    """Return whether a path already falls under an existing redaction prefix."""
    normalized = path_text.rstrip("/")
    for existing in replacements:
        prefix = existing.rstrip("/")
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return True
    return False


def _replace_local_path_prefixes(value: object, replacements: Sequence[tuple[str, str]]) -> object:
    """Recursively replace local path prefixes in support-bundle values."""
    if isinstance(value, str):
        redacted = value
        for path_prefix, marker in replacements:
            redacted = redacted.replace(path_prefix, marker)
        return redacted
    if isinstance(value, list):
        return [_replace_local_path_prefixes(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_local_path_prefixes(item, replacements) for key, item in value.items()
        }
    return value


def _safe_installed_version() -> str:
    """Return the installed package version without failing diagnostics."""
    try:
        return _resolve_installed_version()
    except PolicyNIMError:
        return "unknown"


def _render_support_bundle_markdown(rendered_json: str) -> str:
    """Wrap rendered support-bundle JSON in a Markdown code fence."""
    return "\n".join(
        [
            "## PolicyNIM Support Bundle",
            "",
            "```json",
            rendered_json,
            "```",
        ]
    )


async def _run_mcp_stdio_smoke(
    *,
    timeout_seconds: float,
    command: list[str] | None = None,
    cwd: Path | None = None,
) -> dict[str, object]:
    """Launch local stdio MCP and report public tool registration."""
    command = command or _mcp_stdio_smoke_command()
    report: dict[str, object] = {
        "status": "error",
        "transport": "stdio",
        "command": command,
        "tools": [],
        "missing_tools": list(_EXPECTED_MCP_TOOLS),
    }
    with TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        try:
            parameters = StdioServerParameters(
                command=command[0],
                args=command[1:],
                env=_mcp_stdio_smoke_env(),
                cwd=cwd or Path.cwd(),
            )
            async with stdio_client(parameters, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=timeout_seconds),
                ) as session:
                    await session.initialize()
                    tool_result = await session.list_tools()
        except Exception as exc:
            report["message"] = f"MCP stdio smoke failed: {type(exc).__name__}: {exc}"
            report["next_steps"] = _mcp_stdio_recovery_steps()
            errlog.seek(0)
            stderr_tail = errlog.read().strip()[-2000:]
            if stderr_tail:
                report["server_stderr_tail"] = stderr_tail
            return report

    tool_names = sorted(tool.name for tool in tool_result.tools)
    missing_tools = [tool for tool in _EXPECTED_MCP_TOOLS if tool not in tool_names]
    report["tools"] = tool_names
    report["missing_tools"] = missing_tools
    report["status"] = "ok" if not missing_tools else "error"
    if missing_tools:
        report["message"] = "MCP stdio server did not list all expected public tools."
        report["next_steps"] = _mcp_stdio_recovery_steps()
    return report


def _mcp_stdio_smoke_command_from_config_file(path: Path) -> tuple[list[str], Path | None]:
    """Extract a local stdio launch command from generated mcp-config JSON."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyNIMError(f"Could not read MCP config file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PolicyNIMError(f"MCP config file {path} must contain JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PolicyNIMError("MCP config file must contain a JSON object.")
    if payload.get("target") != "local-stdio":
        raise PolicyNIMError(
            "mcp-smoke --mcp-config-file only supports local-stdio configs. "
            "Use hosted launch evidence for hosted-http configs."
        )

    client = payload.get("client")
    if client == "codex":
        return _codex_stdio_command_from_config_payload(payload)
    if client == "claude-code":
        return _claude_stdio_command_from_config_payload(payload)
    raise PolicyNIMError("MCP config file client must be codex or claude-code.")


def _codex_stdio_command_from_config_payload(
    payload: dict[object, object],
) -> tuple[list[str], Path | None]:
    """Return the launch command and working directory from a Codex MCP config."""
    codex_app = payload.get("codex_app")
    if not isinstance(codex_app, dict):
        raise PolicyNIMError("Codex MCP config file is missing codex_app.")
    if codex_app.get("transport") != "STDIO":
        raise PolicyNIMError("Codex MCP config file must use STDIO transport.")
    command = codex_app.get("command")
    arguments = codex_app.get("arguments")
    launch_command = _mcp_stdio_launch_command(command, arguments)
    cwd = _optional_path(codex_app.get("working_directory")) or _optional_path(
        payload.get("repo_root")
    )
    return launch_command, cwd


def _claude_stdio_command_from_config_payload(
    payload: dict[object, object],
) -> tuple[list[str], Path | None]:
    """Return the launch command and working directory from a Claude Code MCP config."""
    config = payload.get("config")
    server_name = payload.get("server_name")
    if not isinstance(config, dict) or not isinstance(server_name, str):
        raise PolicyNIMError("Claude Code MCP config file is missing config or server_name.")
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        raise PolicyNIMError("Claude Code MCP config file is missing mcpServers.")
    server = servers.get(server_name)
    if not isinstance(server, dict):
        raise PolicyNIMError(f"Claude Code MCP config file is missing {server_name!r}.")
    if server.get("type") != "stdio":
        raise PolicyNIMError("Claude Code MCP config file must use stdio transport.")
    launch_command = _mcp_stdio_launch_command(server.get("command"), server.get("args"))
    return launch_command, _optional_path(payload.get("repo_root"))


def _mcp_stdio_launch_command(command: object, arguments: object) -> list[str]:
    """Validate and return a local MCP stdio launch command."""
    if not isinstance(command, str) or not command:
        raise PolicyNIMError("MCP config file must include a non-empty stdio command.")
    if not isinstance(arguments, list) or any(
        not isinstance(argument, str) for argument in arguments
    ):
        raise PolicyNIMError("MCP config file stdio arguments must be a string list.")
    return [command, *cast(list[str], arguments)]


def _optional_path(value: object) -> Path | None:
    """Convert a non-empty string value to a path when present."""
    if not isinstance(value, str) or not value:
        return None
    return Path(value)


def _mcp_stdio_smoke_command() -> list[str]:
    """Return the command used to re-enter the local MCP stdio server."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "mcp", "--transport", "stdio"]
    return [sys.executable, "-m", "policynim.interfaces.cli", "mcp", "--transport", "stdio"]


def _mcp_stdio_smoke_env() -> dict[str, str]:
    """Return an env that can resolve the current installed policynim entrypoint."""
    env = os.environ.copy()
    executable_dir = str(Path(sys.executable).parent)
    existing_path = env.get("PATH", "")
    env["PATH"] = (
        f"{executable_dir}{os.pathsep}{existing_path}" if existing_path else executable_dir
    )
    return env


def _mcp_stdio_recovery_steps() -> list[str]:
    """Return setup steps for failed local MCP stdio smoke checks."""
    if _doctor_runtime_mode() == "source_checkout":
        return [
            "Run `uv run policynim doctor` to inspect config, credentials, and local index state.",
            "Run `uv run policynim ingest` before calling policy_preflight or policy_search.",
            "Run `uv run policynim mcp-smoke --format json` after changing local setup.",
            (
                "Regenerate client config with `uv run policynim mcp-config --target "
                "local-stdio --client codex --repo-root /ABS/PATH/TO/policyNIM --format json` "
                "or the same command with `--client claude-code`."
            ),
            (
                "If the MCP client cannot find `uv`, rerun mcp-config with "
                "`--uv-command /ABS/PATH/TO/uv`."
            ),
        ]
    return [
        "Run `policynim doctor` to inspect config, credentials, and local index state.",
        "Run `policynim ingest` before calling policy_preflight or policy_search.",
        "Run `policynim mcp-smoke --format json` after changing local setup.",
        (
            "Regenerate client config with `policynim mcp-config --target local-stdio "
            "--client codex --format json` or the same command with `--client claude-code`."
        ),
    ]


def _render_mcp_smoke_report(report: dict[str, object]) -> list[str]:
    """Render an MCP smoke report as terminal-friendly text."""
    lines = [
        "PolicyNIM MCP smoke",
        f"Status: {report['status']}",
        f"Transport: {report['transport']}",
    ]
    command = report.get("command")
    if isinstance(command, list):
        lines.append(f"Command: {' '.join(str(part) for part in command)}")
    tools = report.get("tools")
    if isinstance(tools, list):
        lines.append(f"Tools: {', '.join(str(tool) for tool in tools) or 'none'}")
    missing_tools = report.get("missing_tools")
    if isinstance(missing_tools, list) and missing_tools:
        lines.append(f"missing_tools: {', '.join(str(tool) for tool in missing_tools)}")
    message = report.get("message")
    if isinstance(message, str) and message:
        lines.append(f"Message: {message}")
    next_steps = report.get("next_steps")
    if isinstance(next_steps, list) and next_steps:
        lines.append("")
        lines.append("Next steps:")
        for step in next_steps:
            lines.append(f"- {step}")
    return lines


def _build_doctor_report() -> dict[str, object]:
    """Build a setup diagnostic report without calling hosted APIs."""
    discovery = config_discovery.discover_config_files()
    expected_config_file = config_discovery.resolve_init_config_file()
    report: dict[str, object] = {
        "status": "ok",
        "runtime_mode": _doctor_runtime_mode(),
        "config": {
            "active_config_file": _path_text(discovery.active_config_file),
            "discovered_env_files": [_path_text(path) for path in discovery.env_files],
            "expected_init_config_file": expected_config_file.as_posix(),
        },
        "checks": [],
        "paths": {},
        "mcp": {},
        "next_steps": [],
    }
    checks: list[dict[str, str]] = []
    next_steps: list[str] = []

    if config_discovery.standalone_setup_missing():
        message = f"Run `policynim init` to create {expected_config_file}."
        checks.append({"name": "setup", "status": "action_required", "message": message})
        report["status"] = "action_required"
        report["checks"] = checks
        report["next_steps"] = [message]
        return report

    try:
        settings = get_settings()
    except PolicyNIMError as exc:
        checks.append(
            {
                "name": "configuration",
                "status": "error",
                "message": _cli_error_message(exc),
            }
        )
        report["status"] = "error"
        report["checks"] = checks
        report["next_steps"] = ["Fix the configuration error above, then rerun `policynim doctor`."]
        return report

    checks.append(
        {
            "name": "configuration",
            "status": "ok",
            "message": "Settings loaded without validation errors.",
        }
    )
    if settings.nvidia_api_key:
        checks.append(
            {"name": "nvidia_api_key", "status": "ok", "message": "NVIDIA_API_KEY is set."}
        )
    else:
        checks.append(
            {
                "name": "nvidia_api_key",
                "status": "action_required",
                "message": "NVIDIA_API_KEY is not set.",
            }
        )
        next_steps.append(
            f"Run `{_doctor_cli_command('init')}` or set `NVIDIA_API_KEY` before ingest/search."
        )

    index_path = resolve_runtime_path(settings.index_db_path)
    runtime_rules_path = resolve_runtime_path(settings.runtime_rules_artifact_path)
    report["paths"] = {
        "index_db_path": index_path.as_posix(),
        "runtime_rules_artifact_path": runtime_rules_path.as_posix(),
        "runtime_evidence_db_path": resolve_runtime_path(
            settings.runtime_evidence_db_path
        ).as_posix(),
        "eval_workspace_dir": resolve_runtime_path(settings.eval_workspace_dir).as_posix(),
    }

    legacy_lancedb_alias_configured = _doctor_legacy_lancedb_alias_configured(discovery)
    index_recovery_step_added = False
    index_readiness = inspect_index_readiness(create_index_store(settings))
    if index_readiness.state == "directory":
        checks.append(
            {
                "name": "local_index_path",
                "status": "action_required",
                "message": (
                    "Configured local SQLite index path points to a directory. "
                    "Set POLICYNIM_INDEX_DB_PATH to a SQLite file path such as "
                    "data/index.sqlite3."
                ),
            }
        )
        next_steps.append(
            _doctor_index_directory_next_step(
                legacy_lancedb_alias_configured=legacy_lancedb_alias_configured
            )
        )
        index_recovery_step_added = True
    elif index_readiness.state == "ready":
        checks.append(
            {
                "name": "local_index_path",
                "status": "ok",
                "message": "A populated local SQLite index exists.",
            }
        )
    elif index_readiness.state == "unreadable":
        detail = format_index_readiness_detail(index_readiness.error) or "PermissionError"
        checks.append(
            {
                "name": "local_index_path",
                "status": "action_required",
                "message": f"Configured local SQLite index file could not be read ({detail}).",
            }
        )
        next_steps.append(_doctor_index_unreadable_next_step())
        index_recovery_step_added = True
    elif index_readiness.state == "missing":
        checks.append(
            {
                "name": "local_index_path",
                "status": "action_required",
                "message": "No local index path exists yet.",
            }
        )
        next_steps.append(_doctor_ingest_next_step())
    else:
        checks.append(
            {
                "name": "local_index_path",
                "status": "action_required",
                "message": (
                    "Configured local SQLite index file is not a populated "
                    "PolicyNIM sqlite-vec index."
                ),
            }
        )
        next_steps.append(_doctor_ingest_next_step())
        index_recovery_step_added = True

    if runtime_rules_path.exists():
        checks.append(
            {
                "name": "runtime_rules_artifact",
                "status": "ok",
                "message": "Runtime rules artifact exists.",
            }
        )
    else:
        checks.append(
            {
                "name": "runtime_rules_artifact",
                "status": "action_required",
                "message": "Runtime rules artifact is missing.",
            }
        )
        ingest_next_step = _doctor_ingest_next_step()
        if not index_recovery_step_added and ingest_next_step not in next_steps:
            next_steps.append(ingest_next_step)

    report["mcp"] = {
        "stdio_command": _doctor_mcp_command("mcp --transport stdio"),
        "smoke_command": _doctor_mcp_command("mcp-smoke --format json"),
        "local_stdio_config_commands": _doctor_mcp_config_commands(),
        "streamable_http_url": f"http://{settings.mcp_host}:{settings.mcp_port}/mcp",
        "auth_required": settings.mcp_require_auth,
    }
    if not next_steps:
        search_command = _doctor_cli_command('search --query "refresh token cleanup" --top-k 5')
        mcp_command = _doctor_cli_command("mcp --transport stdio")
        next_steps.append(f"Run `{search_command}` or `{mcp_command}`.")

    if any(check["status"] == "error" for check in checks):
        report["status"] = "error"
    elif any(check["status"] == "action_required" for check in checks):
        report["status"] = "action_required"
    report["checks"] = checks
    report["next_steps"] = next_steps
    return report


def _doctor_runtime_mode() -> str:
    """Return the current runtime mode for diagnostic output."""
    if config_discovery.is_hosted_process_environment():
        return "hosted"
    if config_discovery.is_source_checkout():
        return "source_checkout"
    return "standalone"


def _doctor_cli_command(command: str) -> str:
    """Render a CLI command for the current diagnostic runtime."""
    if _doctor_runtime_mode() == "source_checkout":
        return f"uv run policynim {command}"
    return f"policynim {command}"


def _doctor_ingest_next_step() -> str:
    """Return the standard ingest recovery step for doctor output."""
    return (
        f"Run `{_doctor_cli_command('ingest')}` to build the local policy index "
        "and runtime rules artifact."
    )


def _doctor_index_directory_next_step(*, legacy_lancedb_alias_configured: bool) -> str:
    """Return recovery guidance for SQLite index paths that point at directories."""
    if legacy_lancedb_alias_configured:
        return (
            "Replace deprecated `POLICYNIM_LANCEDB_URI` with "
            f"`POLICYNIM_INDEX_DB_PATH=data/index.sqlite3`, then run "
            f"`{_doctor_cli_command('ingest')}`."
        )
    return (
        "Set `POLICYNIM_INDEX_DB_PATH=data/index.sqlite3`, then run "
        f"`{_doctor_cli_command('ingest')}`."
    )


def _doctor_index_unreadable_next_step() -> str:
    """Return recovery guidance for unreadable SQLite index files."""
    return (
        "Fix the local SQLite index file permissions or point "
        "`POLICYNIM_INDEX_DB_PATH` at a readable SQLite file, then run "
        f"`{_doctor_cli_command('ingest')}`."
    )


def _doctor_legacy_lancedb_alias_configured(
    discovery: config_discovery.ConfigDiscovery,
) -> bool:
    """Return true when the deprecated LanceDB env alias is still configured."""
    if "POLICYNIM_LANCEDB_URI" in os.environ:
        return True
    for env_file in discovery.env_files:
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("POLICYNIM_LANCEDB_URI="):
                    return True
        except OSError:
            continue
    return False


def _doctor_mcp_command(command: str) -> str:
    """Render an MCP-related command for doctor output."""
    return _doctor_cli_command(command)


def _doctor_mcp_config_commands() -> dict[str, str]:
    """Return shell-safe local stdio MCP config commands by client."""
    if _doctor_runtime_mode() != "source_checkout":
        return {
            "codex": _shell_join(
                [
                    "policynim",
                    "mcp-config",
                    "--target",
                    "local-stdio",
                    "--client",
                    "codex",
                    "--format",
                    "json",
                ]
            ),
            "claude-code": _shell_join(
                [
                    "policynim",
                    "mcp-config",
                    "--target",
                    "local-stdio",
                    "--client",
                    "claude-code",
                    "--format",
                    "json",
                ]
            ),
        }

    repo_root = config_discovery.find_source_checkout_root()
    return {
        "codex": _doctor_local_stdio_mcp_config_command("codex", repo_root=repo_root),
        "claude-code": _doctor_local_stdio_mcp_config_command(
            "claude-code",
            repo_root=repo_root,
        ),
    }


def _doctor_local_stdio_mcp_config_command(
    client: Literal["codex", "claude-code"],
    *,
    repo_root: Path | None,
) -> str:
    """Render a shell-safe local stdio MCP config command for doctor output."""
    parts = [
        "uv",
        "run",
        "policynim",
        "mcp-config",
        "--target",
        "local-stdio",
        "--client",
        client,
    ]
    if repo_root is not None:
        parts.extend(["--repo-root", str(repo_root)])
    parts.extend(["--format", "json"])
    return _shell_join(parts)


def _render_doctor_report(report: dict[str, object]) -> list[str]:
    """Render a doctor report as terminal-friendly text."""
    lines = [
        "PolicyNIM doctor",
        f"Status: {report['status']}",
        f"Runtime mode: {report['runtime_mode']}",
    ]
    config = report["config"]
    if isinstance(config, dict):
        lines.append(f"Config file: {config.get('active_config_file') or 'not found'}")
    checks = report["checks"]
    if isinstance(checks, list):
        lines.append("")
        lines.append("Checks:")
        for check in checks:
            if isinstance(check, dict):
                lines.append(
                    f"- {check.get('name')}: {check.get('status')} - {check.get('message')}"
                )
    mcp = report["mcp"]
    if isinstance(mcp, dict) and mcp:
        lines.append("")
        lines.append("MCP:")
        lines.append(f"- stdio: {mcp.get('stdio_command')}")
        smoke_command = mcp.get("smoke_command")
        if isinstance(smoke_command, str):
            lines.append(f"- smoke: {smoke_command}")
        config_commands = mcp.get("local_stdio_config_commands")
        if isinstance(config_commands, dict):
            for client, command in config_commands.items():
                lines.append(f"- {client} config: {command}")
        lines.append(f"- streamable-http: {mcp.get('streamable_http_url')}")
    next_steps = report["next_steps"]
    if isinstance(next_steps, list) and next_steps:
        lines.append("")
        lines.append("Next steps:")
        for step in next_steps:
            lines.append(f"- {step}")
    return lines


def _path_text(path: Path | None) -> str | None:
    """Return a POSIX path string for optional paths."""
    return None if path is None else path.as_posix()


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
