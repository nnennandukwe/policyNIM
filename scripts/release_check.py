"""Run the local PolicyNIM release ship/hold gate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, cast

Decision = Literal["ship", "hold", "not_evaluated"]

REPO_ROOT = Path(__file__).resolve().parents[1]
TAIL_LIMIT = 4000
QUICKSTART_JSON_CONTRACTS: dict[str, tuple[str, str, bool, str | None]] = {
    "quickstart-hosted-mcp": ("quickstart_contract", "hosted-mcp", False, None),
    "quickstart-local-cli": ("quickstart_local_cli_contract", "local-cli", True, None),
    "quickstart-local-mcp": (
        "quickstart_local_mcp_contract",
        "local-mcp",
        True,
        "installed-cli",
    ),
}
MCP_CONFIG_JSON_CONTRACTS: dict[str, tuple[str, str, str]] = {
    "mcp-config-codex-local-stdio": ("mcp_config_contract", "codex", "local-stdio"),
    "mcp-config-claude-code-local-stdio": (
        "claude_mcp_config_contract",
        "claude-code",
        "local-stdio",
    ),
    "mcp-config-codex-hosted-http": (
        "hosted_mcp_config_contract",
        "codex",
        "hosted-http",
    ),
    "mcp-config-claude-code-hosted-http": (
        "claude_hosted_mcp_config_contract",
        "claude-code",
        "hosted-http",
    ),
}
SUPPORT_BUNDLE_JSON_CONTRACT = "support-bundle"
HELP_TEXT_CONTRACTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "init": (
        "init_help_contract",
        (
            "Usage: policynim init",
            "Run interactive local setup",
            "NVIDIA_API_KEY",
        ),
    ),
    "ingest": (
        "ingest_help_contract",
        (
            "Usage: policynim ingest",
            "Build the local policy index",
        ),
    ),
    "preflight": (
        "preflight_help_contract",
        (
            "Usage: policynim preflight",
            "Return policy guidance",
            "--task",
        ),
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format for the release gate summary.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the checks that would run without executing them.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to check. Defaults to this script's repository.",
    )
    parser.add_argument(
        "--strict-public",
        action="store_true",
        help=(
            "Also run the strict public-launch readiness gate and exit non-zero "
            "until external evidence is complete."
        ),
    )
    parser.add_argument(
        "--external-evidence-file",
        type=Path,
        default=None,
        help=(
            "Optional launch evidence JSON file to pass to "
            "scripts/oss_readiness_check.py --strict-public."
        ),
    )
    parser.add_argument(
        "--validate-json-contract",
        nargs=2,
        metavar=("CONTRACT", "PAYLOAD_FILE"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--validate-launch-issue-contract",
        metavar="PAYLOAD_FILE",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--validate-init-help-contract",
        metavar="PAYLOAD_FILE",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--validate-help-contract",
        nargs=2,
        metavar=("COMMAND", "PAYLOAD_FILE"),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    validation_modes = [
        args.validate_json_contract is not None,
        args.validate_launch_issue_contract is not None,
        args.validate_init_help_contract is not None,
        args.validate_help_contract is not None,
    ]
    if sum(validation_modes) > 1:
        parser.error("validation contract options are mutually exclusive")
    if args.validate_json_contract is not None:
        contract_name, payload_arg = args.validate_json_contract
        return _validate_json_contract_cli(
            contract_name=contract_name,
            payload_arg=payload_arg,
            output_format=args.format,
        )
    if args.validate_launch_issue_contract is not None:
        return _validate_launch_issue_contract_cli(
            payload_arg=args.validate_launch_issue_contract,
            output_format=args.format,
        )
    if args.validate_init_help_contract is not None:
        return _validate_init_help_contract_cli(
            payload_arg=args.validate_init_help_contract,
            output_format=args.format,
        )
    if args.validate_help_contract is not None:
        command_name, payload_arg = args.validate_help_contract
        return _validate_help_contract_cli(
            command_name=command_name,
            payload_arg=payload_arg,
            output_format=args.format,
        )

    result = run_release_check(
        repo_root=args.repo_root.resolve(),
        dry_run=args.dry_run,
        strict_public=args.strict_public,
        external_evidence_file=args.external_evidence_file,
    )
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(_render_text_summary(result))
    return _exit_code(result)


def _validate_json_contract_cli(
    *,
    contract_name: str,
    payload_arg: str,
    output_format: str,
) -> int:
    try:
        payload = _read_validation_payload(payload_arg)
    except OSError as exc:
        return _emit_validation_result(
            _synthetic_failure(
                name="json_contract",
                message=f"Could not read payload {payload_arg!r}: {exc}",
            ),
            output_format=output_format,
        )

    if contract_name in QUICKSTART_JSON_CONTRACTS:
        check_name, expected_target, expected_requires_local_setup, expected_launch_mode = (
            QUICKSTART_JSON_CONTRACTS[contract_name]
        )
        result = _quickstart_contract_check(
            name=check_name,
            payload=payload,
            expected_target=expected_target,
            expected_requires_local_setup=expected_requires_local_setup,
            expected_local_launch_mode=expected_launch_mode,
        )
    elif contract_name in MCP_CONFIG_JSON_CONTRACTS:
        check_name, expected_client, expected_target = MCP_CONFIG_JSON_CONTRACTS[contract_name]
        result = _mcp_config_contract_check(
            name=check_name,
            payload=payload,
            expected_client=expected_client,
            expected_target=expected_target,
        )
    elif contract_name == SUPPORT_BUNDLE_JSON_CONTRACT:
        result = _support_bundle_contract_check(name="support_bundle_contract", payload=payload)
    else:
        expected_contracts = ", ".join(
            [
                *QUICKSTART_JSON_CONTRACTS.keys(),
                *MCP_CONFIG_JSON_CONTRACTS.keys(),
                SUPPORT_BUNDLE_JSON_CONTRACT,
            ]
        )
        result = _synthetic_failure(
            name="json_contract",
            message=(
                f"Unknown JSON contract {contract_name!r}. Expected one of: {expected_contracts}."
            ),
        )
    return _emit_validation_result(result, output_format=output_format)


def _validate_launch_issue_contract_cli(*, payload_arg: str, output_format: str) -> int:
    try:
        payload = _read_validation_payload(payload_arg)
    except OSError as exc:
        return _emit_validation_result(
            _synthetic_launch_issue_contract_failure(
                name="oss_readiness_launch_issue_contract",
                message=f"Could not read payload {payload_arg!r}: {exc}",
            ),
            output_format=output_format,
        )
    return _emit_validation_result(
        _launch_issue_contract_check(
            name="oss_readiness_launch_issue_contract",
            payload=payload,
        ),
        output_format=output_format,
    )


def _validate_init_help_contract_cli(*, payload_arg: str, output_format: str) -> int:
    return _validate_help_contract_cli(
        command_name="init",
        payload_arg=payload_arg,
        output_format=output_format,
        legacy_init_command=True,
    )


def _validate_help_contract_cli(
    *,
    command_name: str,
    payload_arg: str,
    output_format: str,
    legacy_init_command: bool = False,
) -> int:
    try:
        payload = _read_validation_payload(payload_arg)
    except OSError as exc:
        check_name = HELP_TEXT_CONTRACTS.get(command_name, ("help_contract", ()))[0]
        return _emit_validation_result(
            _synthetic_help_contract_failure(
                name=check_name,
                command_name=command_name,
                message=f"Could not read payload {payload_arg!r}: {exc}",
                legacy_init_command=legacy_init_command,
            ),
            output_format=output_format,
        )
    return _emit_validation_result(
        _help_contract_check(
            command_name=command_name,
            payload=payload,
            legacy_init_command=legacy_init_command,
        ),
        output_format=output_format,
    )


def _read_validation_payload(payload_arg: str) -> str:
    if payload_arg == "-":
        return sys.stdin.read()
    return Path(payload_arg).read_text(encoding="utf-8")


def _emit_validation_result(result: dict[str, Any], *, output_format: str) -> int:
    if output_format == "json":
        print(json.dumps(result, indent=2))
    elif result["status"] == "passed":
        print(f"{result['name']}: passed")
    else:
        print(f"{result['name']}: failed", file=sys.stderr)
        if result["stderr_tail"]:
            print(result["stderr_tail"], file=sys.stderr)
    return 0 if result["status"] == "passed" else 1


def run_release_check(
    *,
    repo_root: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    dry_run: bool = False,
    strict_public: bool = False,
    external_evidence_file: Path | None = None,
) -> dict[str, Any]:
    """Run deterministic release checks and return a machine-readable summary."""
    command_runner = runner or _default_runner
    checks: list[dict[str, Any]] = []

    for name, command in _deterministic_gate_commands(
        strict_public=strict_public,
        external_evidence_file=external_evidence_file,
    ):
        if dry_run:
            checks.append(_not_run_check(name, command))
            if name == "oss_readiness_launch_issue":
                checks.append(
                    _not_run_check(
                        "oss_readiness_launch_issue_contract",
                        _launch_issue_contract_command("<launch-issue-output>"),
                    )
                )
            continue
        check = _run_command(name=name, command=command, cwd=repo_root, runner=command_runner)
        checks.append(check)
        if check["status"] != "passed":
            return _summary(
                checks=checks,
                repo_root=repo_root,
                dry_run=False,
                strict_public=strict_public,
                external_evidence_file=external_evidence_file,
            )
        if name == "oss_readiness_launch_issue":
            checks.append(
                _launch_issue_contract_check(
                    name="oss_readiness_launch_issue_contract",
                    payload=str(check.get("_stdout_full", "")),
                )
            )
            if checks[-1]["status"] != "passed":
                return _summary(
                    checks=checks,
                    repo_root=repo_root,
                    dry_run=False,
                    strict_public=strict_public,
                    external_evidence_file=external_evidence_file,
                )

    if dry_run:
        for name, command in _wheel_smoke_preview_commands():
            checks.append(_not_run_check(name, command))
        return _summary(
            checks=checks,
            repo_root=repo_root,
            dry_run=True,
            strict_public=strict_public,
            external_evidence_file=external_evidence_file,
        )

    with TemporaryDirectory(prefix="policynim-release-build.") as build_tmp:
        with TemporaryDirectory(prefix="policynim-release-venv.") as venv_tmp:
            with TemporaryDirectory(prefix="policynim-release-cwd.") as cwd_tmp:
                checks.extend(
                    _run_wheel_smoke(
                        repo_root=repo_root,
                        build_dir=Path(build_tmp),
                        venv_dir=Path(venv_tmp),
                        smoke_cwd=Path(cwd_tmp),
                        runner=command_runner,
                    )
                )
    return _summary(
        checks=checks,
        repo_root=repo_root,
        dry_run=False,
        strict_public=strict_public,
        external_evidence_file=external_evidence_file,
    )


def _deterministic_gate_commands(
    *,
    strict_public: bool = False,
    external_evidence_file: Path | None = None,
) -> list[tuple[str, list[str]]]:
    commands = [
        ("lockfile", ["uv", "lock", "--check"]),
        ("ruff", ["uv", "run", "ruff", "check", "."]),
        ("pyright", ["uv", "run", "pyright"]),
        ("offline_pytest", ["uv", "run", "pytest", "-q", "-m", "not live and not docker_live"]),
        (
            "release_notes_check",
            [sys.executable, "scripts/check_release_notes.py", "--format", "json"],
        ),
        (
            "oss_readiness_json",
            _oss_readiness_command(
                output_format="json",
                external_evidence_file=external_evidence_file,
            ),
        ),
        (
            "oss_readiness_launch_issue",
            _oss_readiness_command(
                output_format="launch-issue",
                external_evidence_file=external_evidence_file,
            ),
        ),
    ]
    if strict_public:
        commands.append(
            (
                "oss_readiness_strict_public_json",
                _oss_readiness_command(
                    output_format="json",
                    external_evidence_file=external_evidence_file,
                    strict_public=True,
                ),
            )
        )
    commands.append(
        (
            "github_label_taxonomy_dry_run_json",
            [sys.executable, "scripts/sync_github_labels.py", "--format", "json"],
        )
    )
    commands.append(
        (
            "github_topic_taxonomy_dry_run_json",
            [sys.executable, "scripts/sync_github_topics.py", "--format", "json"],
        )
    )
    return commands


def _oss_readiness_command(
    *,
    output_format: Literal["json", "launch-issue"],
    external_evidence_file: Path | None = None,
    strict_public: bool = False,
) -> list[str]:
    command = [sys.executable, "scripts/oss_readiness_check.py"]
    if strict_public:
        command.append("--strict-public")
    if external_evidence_file is not None:
        command.extend(["--external-evidence-file", str(external_evidence_file)])
    command.extend(["--format", output_format])
    return command


def _wheel_smoke_preview_commands() -> list[tuple[str, list[str]]]:
    return [
        ("build_distribution", ["uv", "build", "--out-dir", "<temp-build-dir>"]),
        ("create_clean_venv", [sys.executable, "-m", "venv", "<temp-venv-dir>"]),
        ("upgrade_pip", ["<venv-python>", "-m", "pip", "install", "--upgrade", "pip"]),
        ("install_wheel", ["<venv-python>", "-m", "pip", "install", "<built-wheel>"]),
        ("installed_cli_help", ["<venv-policynim>", "--help"]),
        ("installed_cli_init_help", ["<venv-policynim>", "init", "--help"]),
        ("installed_cli_ingest_help", ["<venv-policynim>", "ingest", "--help"]),
        ("installed_cli_preflight_help", ["<venv-policynim>", "preflight", "--help"]),
        ("installed_cli_quickstart_json", ["<venv-policynim>", "quickstart", "--format", "json"]),
        (
            "installed_cli_quickstart_local_cli_json",
            ["<venv-policynim>", "quickstart", "--target", "local-cli", "--format", "json"],
        ),
        (
            "installed_cli_quickstart_local_mcp_json",
            ["<venv-policynim>", "quickstart", "--target", "local-mcp", "--format", "json"],
        ),
        ("installed_cli_doctor_json", ["<venv-policynim>", "doctor", "--format", "json"]),
        ("installed_cli_support_bundle", ["<venv-policynim>", "support-bundle"]),
        ("installed_cli_mcp_smoke_json", ["<venv-policynim>", "mcp-smoke", "--format", "json"]),
        (
            "installed_cli_mcp_config_json",
            [
                "<venv-policynim>",
                "mcp-config",
                "--client",
                "codex",
                "--target",
                "local-stdio",
                "--format",
                "json",
            ],
        ),
        (
            "installed_cli_claude_mcp_config_json",
            [
                "<venv-policynim>",
                "mcp-config",
                "--client",
                "claude-code",
                "--target",
                "local-stdio",
                "--format",
                "json",
            ],
        ),
        (
            "installed_cli_hosted_mcp_config_json",
            [
                "<venv-policynim>",
                "mcp-config",
                "--target",
                "hosted-http",
                "--client",
                "codex",
                "--hosted-url",
                "https://example.invalid/mcp",
                "--bearer-token-env-var",
                "POLICYNIM_TOKEN",
                "--format",
                "json",
            ],
        ),
        (
            "installed_cli_claude_hosted_mcp_config_json",
            [
                "<venv-policynim>",
                "mcp-config",
                "--target",
                "hosted-http",
                "--client",
                "claude-code",
                "--hosted-url",
                "https://example.invalid/mcp",
                "--bearer-token-env-var",
                "POLICYNIM_TOKEN",
                "--format",
                "json",
            ],
        ),
        ("installed_cli_version", ["<venv-policynim>", "--version"]),
        ("init_help_contract", _init_help_contract_command("<init-help-output>")),
        (
            "ingest_help_contract",
            _help_contract_command("ingest", "<ingest-help-output>"),
        ),
        (
            "preflight_help_contract",
            _help_contract_command("preflight", "<preflight-help-output>"),
        ),
        ("quickstart_json_parse", _json_parse_command("<quickstart-output>")),
        (
            "quickstart_contract",
            _json_contract_command("quickstart-hosted-mcp", "<quickstart-output>"),
        ),
        (
            "quickstart_local_cli_json_parse",
            _json_parse_command("<quickstart-local-cli-output>"),
        ),
        (
            "quickstart_local_cli_contract",
            _json_contract_command(
                "quickstart-local-cli",
                "<quickstart-local-cli-output>",
            ),
        ),
        (
            "quickstart_local_mcp_json_parse",
            _json_parse_command("<quickstart-local-mcp-output>"),
        ),
        (
            "quickstart_local_mcp_contract",
            _json_contract_command(
                "quickstart-local-mcp",
                "<quickstart-local-mcp-output>",
            ),
        ),
        ("doctor_json_parse", _json_parse_command("<doctor-output>")),
        ("support_bundle_json_parse", _json_parse_command("<support-output>")),
        (
            "support_bundle_contract",
            _json_contract_command(SUPPORT_BUNDLE_JSON_CONTRACT, "<support-output>"),
        ),
        ("mcp_smoke_json_parse", _json_parse_command("<mcp-smoke-output>")),
        ("mcp_config_json_parse", _json_parse_command("<mcp-config-output>")),
        (
            "mcp_config_contract",
            _json_contract_command("mcp-config-codex-local-stdio", "<mcp-config-output>"),
        ),
        (
            "claude_mcp_config_json_parse",
            _json_parse_command("<claude-mcp-config-output>"),
        ),
        (
            "claude_mcp_config_contract",
            _json_contract_command(
                "mcp-config-claude-code-local-stdio",
                "<claude-mcp-config-output>",
            ),
        ),
        (
            "installed_cli_mcp_smoke_from_codex_config_json",
            [
                "<venv-policynim>",
                "mcp-smoke",
                "--mcp-config-file",
                "<codex-mcp-config-output-file>",
                "--format",
                "json",
            ],
        ),
        (
            "mcp_smoke_from_codex_config_json_parse",
            _json_parse_command("<mcp-smoke-from-codex-config-output>"),
        ),
        (
            "installed_cli_mcp_smoke_from_claude_config_json",
            [
                "<venv-policynim>",
                "mcp-smoke",
                "--mcp-config-file",
                "<claude-mcp-config-output-file>",
                "--format",
                "json",
            ],
        ),
        (
            "mcp_smoke_from_claude_config_json_parse",
            _json_parse_command("<mcp-smoke-from-claude-config-output>"),
        ),
        (
            "hosted_mcp_config_json_parse",
            _json_parse_command("<hosted-mcp-config-output>"),
        ),
        (
            "hosted_mcp_config_contract",
            _json_contract_command("mcp-config-codex-hosted-http", "<hosted-mcp-config-output>"),
        ),
        (
            "claude_hosted_mcp_config_json_parse",
            _json_parse_command("<claude-hosted-mcp-config-output>"),
        ),
        (
            "claude_hosted_mcp_config_contract",
            _json_contract_command(
                "mcp-config-claude-code-hosted-http",
                "<claude-hosted-mcp-config-output>",
            ),
        ),
    ]


def _run_wheel_smoke(
    *,
    repo_root: Path,
    build_dir: Path,
    venv_dir: Path,
    smoke_cwd: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    build_check = _run_command(
        name="build_distribution",
        command=["uv", "build", "--out-dir", str(build_dir)],
        cwd=repo_root,
        runner=runner,
    )
    checks.append(build_check)
    if build_check["status"] != "passed":
        return checks

    wheels = sorted(build_dir.glob("*.whl"))
    if not wheels:
        checks.append(
            _synthetic_failure(
                name="find_built_wheel",
                message=f"No wheel was produced in {build_dir}.",
            )
        )
        return checks
    wheel = wheels[0]

    venv_python = _venv_python(venv_dir)
    policynim_bin = _venv_policynim(venv_dir)
    smoke_commands = [
        ("create_clean_venv", [sys.executable, "-m", "venv", str(venv_dir)], repo_root),
        (
            "upgrade_pip",
            [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
            repo_root,
        ),
        ("install_wheel", [str(venv_python), "-m", "pip", "install", str(wheel)], repo_root),
        ("installed_cli_help", [str(policynim_bin), "--help"], smoke_cwd),
        ("installed_cli_init_help", [str(policynim_bin), "init", "--help"], smoke_cwd),
        ("installed_cli_ingest_help", [str(policynim_bin), "ingest", "--help"], smoke_cwd),
        (
            "installed_cli_preflight_help",
            [str(policynim_bin), "preflight", "--help"],
            smoke_cwd,
        ),
        (
            "installed_cli_quickstart_json",
            [str(policynim_bin), "quickstart", "--format", "json"],
            smoke_cwd,
        ),
        (
            "installed_cli_quickstart_local_cli_json",
            [
                str(policynim_bin),
                "quickstart",
                "--target",
                "local-cli",
                "--format",
                "json",
            ],
            smoke_cwd,
        ),
        (
            "installed_cli_quickstart_local_mcp_json",
            [
                str(policynim_bin),
                "quickstart",
                "--target",
                "local-mcp",
                "--format",
                "json",
            ],
            smoke_cwd,
        ),
        (
            "installed_cli_doctor_json",
            [str(policynim_bin), "doctor", "--format", "json"],
            smoke_cwd,
        ),
        ("installed_cli_support_bundle", [str(policynim_bin), "support-bundle"], smoke_cwd),
        (
            "installed_cli_mcp_smoke_json",
            [str(policynim_bin), "mcp-smoke", "--format", "json"],
            smoke_cwd,
        ),
        (
            "installed_cli_mcp_config_json",
            [
                str(policynim_bin),
                "mcp-config",
                "--client",
                "codex",
                "--target",
                "local-stdio",
                "--format",
                "json",
            ],
            smoke_cwd,
        ),
        (
            "installed_cli_claude_mcp_config_json",
            [
                str(policynim_bin),
                "mcp-config",
                "--client",
                "claude-code",
                "--target",
                "local-stdio",
                "--format",
                "json",
            ],
            smoke_cwd,
        ),
        (
            "installed_cli_hosted_mcp_config_json",
            [
                str(policynim_bin),
                "mcp-config",
                "--target",
                "hosted-http",
                "--client",
                "codex",
                "--hosted-url",
                "https://example.invalid/mcp",
                "--bearer-token-env-var",
                "POLICYNIM_TOKEN",
                "--format",
                "json",
            ],
            smoke_cwd,
        ),
        (
            "installed_cli_claude_hosted_mcp_config_json",
            [
                str(policynim_bin),
                "mcp-config",
                "--target",
                "hosted-http",
                "--client",
                "claude-code",
                "--hosted-url",
                "https://example.invalid/mcp",
                "--bearer-token-env-var",
                "POLICYNIM_TOKEN",
                "--format",
                "json",
            ],
            smoke_cwd,
        ),
        ("installed_cli_version", [str(policynim_bin), "--version"], smoke_cwd),
    ]

    quickstart_stdout = ""
    init_help_stdout = ""
    ingest_help_stdout = ""
    preflight_help_stdout = ""
    quickstart_local_cli_stdout = ""
    quickstart_local_mcp_stdout = ""
    doctor_stdout = ""
    support_stdout = ""
    mcp_smoke_stdout = ""
    mcp_config_stdout = ""
    claude_mcp_config_stdout = ""
    hosted_mcp_config_stdout = ""
    claude_hosted_mcp_config_stdout = ""
    for name, command, cwd in smoke_commands:
        check = _run_command(name=name, command=command, cwd=cwd, runner=runner)
        checks.append(check)
        if check["status"] != "passed":
            return checks
        if name == "installed_cli_quickstart_json":
            quickstart_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_init_help":
            init_help_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_ingest_help":
            ingest_help_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_preflight_help":
            preflight_help_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_quickstart_local_cli_json":
            quickstart_local_cli_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_quickstart_local_mcp_json":
            quickstart_local_mcp_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_doctor_json":
            doctor_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_support_bundle":
            support_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_mcp_smoke_json":
            mcp_smoke_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_mcp_config_json":
            mcp_config_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_claude_mcp_config_json":
            claude_mcp_config_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_hosted_mcp_config_json":
            hosted_mcp_config_stdout = str(check.get("_stdout_full", ""))
        elif name == "installed_cli_claude_hosted_mcp_config_json":
            claude_hosted_mcp_config_stdout = str(check.get("_stdout_full", ""))

    checks.append(_init_help_contract_check(name="init_help_contract", payload=init_help_stdout))
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(_help_contract_check(command_name="ingest", payload=ingest_help_stdout))
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(_help_contract_check(command_name="preflight", payload=preflight_help_stdout))
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(_json_parse_check(name="quickstart_json_parse", payload=quickstart_stdout))
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _quickstart_contract_check(
            name="quickstart_contract",
            payload=quickstart_stdout,
            expected_target="hosted-mcp",
            expected_requires_local_setup=False,
            expected_local_launch_mode=None,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _json_parse_check(
            name="quickstart_local_cli_json_parse",
            payload=quickstart_local_cli_stdout,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _quickstart_contract_check(
            name="quickstart_local_cli_contract",
            payload=quickstart_local_cli_stdout,
            expected_target="local-cli",
            expected_requires_local_setup=True,
            expected_local_launch_mode=None,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _json_parse_check(
            name="quickstart_local_mcp_json_parse",
            payload=quickstart_local_mcp_stdout,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _quickstart_contract_check(
            name="quickstart_local_mcp_contract",
            payload=quickstart_local_mcp_stdout,
            expected_target="local-mcp",
            expected_requires_local_setup=True,
            expected_local_launch_mode="installed-cli",
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(_json_parse_check(name="doctor_json_parse", payload=doctor_stdout))
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(_json_parse_check(name="support_bundle_json_parse", payload=support_stdout))
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _support_bundle_contract_check(
            name="support_bundle_contract",
            payload=support_stdout,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(_json_parse_check(name="mcp_smoke_json_parse", payload=mcp_smoke_stdout))
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(_json_parse_check(name="mcp_config_json_parse", payload=mcp_config_stdout))
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _mcp_config_contract_check(
            name="mcp_config_contract",
            payload=mcp_config_stdout,
            expected_client="codex",
            expected_target="local-stdio",
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _json_parse_check(
            name="claude_mcp_config_json_parse",
            payload=claude_mcp_config_stdout,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _mcp_config_contract_check(
            name="claude_mcp_config_contract",
            payload=claude_mcp_config_stdout,
            expected_client="claude-code",
            expected_target="local-stdio",
        )
    )
    if checks[-1]["status"] != "passed":
        return checks

    codex_config_path = smoke_cwd / "codex-mcp-config.json"
    codex_config_path.write_text(mcp_config_stdout, encoding="utf-8")
    checks.append(
        _run_command(
            name="installed_cli_mcp_smoke_from_codex_config_json",
            command=[
                str(policynim_bin),
                "mcp-smoke",
                "--mcp-config-file",
                str(codex_config_path),
                "--format",
                "json",
            ],
            cwd=smoke_cwd,
            runner=runner,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _json_parse_check(
            name="mcp_smoke_from_codex_config_json_parse",
            payload=str(checks[-1].get("_stdout_full", "")),
        )
    )
    if checks[-1]["status"] != "passed":
        return checks

    claude_config_path = smoke_cwd / "claude-mcp-config.json"
    claude_config_path.write_text(claude_mcp_config_stdout, encoding="utf-8")
    checks.append(
        _run_command(
            name="installed_cli_mcp_smoke_from_claude_config_json",
            command=[
                str(policynim_bin),
                "mcp-smoke",
                "--mcp-config-file",
                str(claude_config_path),
                "--format",
                "json",
            ],
            cwd=smoke_cwd,
            runner=runner,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _json_parse_check(
            name="mcp_smoke_from_claude_config_json_parse",
            payload=str(checks[-1].get("_stdout_full", "")),
        )
    )
    if checks[-1]["status"] != "passed":
        return checks

    checks.append(
        _json_parse_check(
            name="hosted_mcp_config_json_parse",
            payload=hosted_mcp_config_stdout,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _mcp_config_contract_check(
            name="hosted_mcp_config_contract",
            payload=hosted_mcp_config_stdout,
            expected_client="codex",
            expected_target="hosted-http",
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _json_parse_check(
            name="claude_hosted_mcp_config_json_parse",
            payload=claude_hosted_mcp_config_stdout,
        )
    )
    if checks[-1]["status"] != "passed":
        return checks
    checks.append(
        _mcp_config_contract_check(
            name="claude_hosted_mcp_config_contract",
            payload=claude_hosted_mcp_config_stdout,
            expected_client="claude-code",
            expected_target="hosted-http",
        )
    )
    return checks


def _run_command(
    *,
    name: str,
    command: Sequence[str],
    cwd: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    command_list = [str(part) for part in command]
    started = time.monotonic()
    completed = runner(command_list, cwd=cwd)
    duration_seconds = round(time.monotonic() - started, 3)
    return {
        "name": name,
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command_list,
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "duration_seconds": duration_seconds,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
        "_stdout_full": completed.stdout or "",
    }


def _default_runner(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _summary(
    *,
    checks: list[dict[str, Any]],
    repo_root: Path,
    dry_run: bool,
    strict_public: bool,
    external_evidence_file: Path | None,
) -> dict[str, Any]:
    if dry_run:
        decision: Decision = "not_evaluated"
        required_passed: bool | None = None
    else:
        required_passed = all(check["status"] == "passed" for check in checks)
        decision = "ship" if required_passed else "hold"
    failed = [check["name"] for check in checks if check["status"] == "failed"]
    return {
        "schema_version": "1",
        "decision": decision,
        "required_passed": required_passed,
        "dry_run": dry_run,
        "strict_public": strict_public,
        "external_evidence_file": (
            str(external_evidence_file) if external_evidence_file is not None else None
        ),
        "repo_root": str(repo_root),
        "checks": [_public_check(check) for check in checks],
        "failed_checks": failed,
    }


def _not_run_check(name: str, command: Sequence[str]) -> dict[str, Any]:
    return {
        "name": name,
        "status": "not_run",
        "command": [str(part) for part in command],
        "cwd": None,
        "returncode": None,
        "duration_seconds": None,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _synthetic_failure(*, name: str, message: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "failed",
        "command": [],
        "cwd": None,
        "returncode": 1,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": message,
    }


def _json_parse_check(*, name: str, payload: str) -> dict[str, Any]:
    try:
        json.loads(payload)
    except json.JSONDecodeError as exc:
        return {
            "name": name,
            "status": "failed",
            "command": _json_parse_command("<captured-output>"),
            "cwd": None,
            "returncode": 1,
            "duration_seconds": 0.0,
            "stdout_tail": "",
            "stderr_tail": f"Invalid JSON output: {exc}",
        }
    return {
        "name": name,
        "status": "passed",
        "command": _json_parse_command("<captured-output>"),
        "cwd": None,
        "returncode": 0,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _quickstart_json_contract_name(
    *,
    expected_target: str,
    expected_requires_local_setup: bool,
    expected_local_launch_mode: str | None,
) -> str:
    for contract_name, (
        _check_name,
        target,
        requires_local_setup,
        launch_mode,
    ) in QUICKSTART_JSON_CONTRACTS.items():
        if (
            expected_target == target
            and expected_requires_local_setup is requires_local_setup
            and expected_local_launch_mode == launch_mode
        ):
            return contract_name
    return "quickstart-hosted-mcp"


def _quickstart_contract_check(
    *,
    name: str,
    payload: str,
    expected_target: str,
    expected_requires_local_setup: bool,
    expected_local_launch_mode: str | None,
) -> dict[str, Any]:
    contract_name = _quickstart_json_contract_name(
        expected_target=expected_target,
        expected_requires_local_setup=expected_requires_local_setup,
        expected_local_launch_mode=expected_local_launch_mode,
    )
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        return _synthetic_contract_failure(
            name=name,
            message=f"Invalid JSON output: {exc}",
            contract_name=contract_name,
        )
    if not isinstance(parsed, dict):
        return _synthetic_contract_failure(
            name=name,
            message="Quickstart output must be a JSON object.",
            contract_name=contract_name,
        )

    errors: list[str] = []
    if parsed.get("target") != expected_target:
        errors.append(f"target expected {expected_target!r}, got {parsed.get('target')!r}")
    if parsed.get("requires_local_setup") is not expected_requires_local_setup:
        errors.append(
            "requires_local_setup expected "
            f"{expected_requires_local_setup!r}, got {parsed.get('requires_local_setup')!r}"
        )
    if parsed.get("calls_external_services") is not False:
        errors.append(
            f"calls_external_services expected False, got {parsed.get('calls_external_services')!r}"
        )
    if expected_local_launch_mode is None:
        if "local_launch_mode" in parsed:
            errors.append("local_launch_mode must be absent for non-local-MCP quickstart output")
    elif parsed.get("local_launch_mode") != expected_local_launch_mode:
        errors.append(
            "local_launch_mode expected "
            f"{expected_local_launch_mode!r}, got {parsed.get('local_launch_mode')!r}"
        )
    commands = parsed.get("commands")
    if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
        errors.append("commands must be a string list")
    elif expected_local_launch_mode == "installed-cli" and any(
        "--repo-root" in item for item in commands
    ):
        errors.append("installed local MCP quickstart commands must not include --repo-root")
    if expected_target == "hosted-mcp":
        errors.extend(_hosted_quickstart_token_flow_contract_errors(parsed))
        errors.extend(
            _hosted_client_commands_contract_errors(
                parsed,
                label="hosted quickstart client_commands",
            )
        )
        next_steps = _string_list(parsed.get("next_steps"))
        if next_steps is None:
            errors.append("hosted quickstart next_steps must be a string list")
        elif any("uv run" in item for item in next_steps):
            errors.append("hosted quickstart next_steps must use installed CLI commands")
    errors.extend(_agent_workflows_contract_errors(parsed))

    if errors:
        return _synthetic_contract_failure(
            name=name,
            message="; ".join(errors),
            contract_name=contract_name,
        )
    return {
        "name": name,
        "status": "passed",
        "command": _json_contract_command(contract_name, "<captured-output>"),
        "cwd": None,
        "returncode": 0,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _support_bundle_contract_check(*, name: str, payload: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        return _synthetic_contract_failure(
            name=name,
            message=f"Invalid JSON output: {exc}",
            contract_name=SUPPORT_BUNDLE_JSON_CONTRACT,
        )
    if not isinstance(parsed, dict):
        return _synthetic_contract_failure(
            name=name,
            message="Support bundle output must be a JSON object.",
            contract_name=SUPPORT_BUNDLE_JSON_CONTRACT,
        )

    errors: list[str] = []
    first_run = parsed.get("first_run")
    if not isinstance(first_run, dict):
        errors.append("first_run must be a JSON object")
    else:
        if first_run.get("runtime_mode") != "standalone":
            errors.append(
                "first_run.runtime_mode expected 'standalone', "
                f"got {first_run.get('runtime_mode')!r}"
            )
        if first_run.get("default_target") != "hosted-mcp":
            errors.append(
                "first_run.default_target expected 'hosted-mcp', "
                f"got {first_run.get('default_target')!r}"
            )
        targets = first_run.get("targets")
        if not isinstance(targets, dict):
            errors.append("first_run.targets must be a JSON object")
        else:
            errors.extend(_support_bundle_target_contract_errors(targets))

    if errors:
        return _synthetic_contract_failure(
            name=name,
            message="; ".join(errors),
            contract_name=SUPPORT_BUNDLE_JSON_CONTRACT,
        )
    return {
        "name": name,
        "status": "passed",
        "command": _json_contract_command(
            SUPPORT_BUNDLE_JSON_CONTRACT,
            "<captured-output>",
        ),
        "cwd": None,
        "returncode": 0,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _support_bundle_target_contract_errors(targets: dict[object, object]) -> list[str]:
    expected_targets = {
        "hosted_mcp": (
            "hosted-mcp",
            False,
            None,
            "policynim quickstart --target hosted-mcp --format json",
        ),
        "local_cli": (
            "local-cli",
            True,
            None,
            "policynim quickstart --target local-cli --format json",
        ),
        "local_mcp": (
            "local-mcp",
            True,
            "installed-cli",
            "policynim quickstart --target local-mcp --client codex --format json",
        ),
    }
    errors: list[str] = []
    for (
        key,
        (
            expected_target,
            expected_requires_local_setup,
            expected_launch_mode,
            expected_quickstart_command,
        ),
    ) in expected_targets.items():
        raw_target = targets.get(key)
        if not isinstance(raw_target, dict):
            errors.append(f"{key} must be a JSON object")
            continue
        if raw_target.get("target") != expected_target:
            errors.append(
                f"{key}.target expected {expected_target!r}, got {raw_target.get('target')!r}"
            )
        if raw_target.get("requires_local_setup") is not expected_requires_local_setup:
            errors.append(
                f"{key}.requires_local_setup expected {expected_requires_local_setup!r}, "
                f"got {raw_target.get('requires_local_setup')!r}"
            )
        if raw_target.get("calls_external_services") is not False:
            errors.append(
                f"{key}.calls_external_services expected False, "
                f"got {raw_target.get('calls_external_services')!r}"
            )
        if expected_launch_mode is None:
            if "local_launch_mode" in raw_target:
                errors.append(f"{key}.local_launch_mode must be absent")
        elif raw_target.get("local_launch_mode") != expected_launch_mode:
            errors.append(
                f"{key}.local_launch_mode expected {expected_launch_mode!r}, "
                f"got {raw_target.get('local_launch_mode')!r}"
            )
        quickstart_command = raw_target.get("quickstart_command")
        if not isinstance(quickstart_command, str):
            errors.append(f"{key}.quickstart_command must be a string")
        else:
            if "uv run" in quickstart_command:
                errors.append(
                    f"{key}.quickstart_command must use installed CLI commands, not uv run"
                )
            if "--repo-root" in quickstart_command:
                errors.append(f"{key}.quickstart_command must not include --repo-root")
            if quickstart_command != expected_quickstart_command:
                errors.append(
                    f"{key}.quickstart_command expected {expected_quickstart_command!r}, "
                    f"got {quickstart_command!r}"
                )
        commands = raw_target.get("commands")
        if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
            errors.append(f"{key}.commands must be a string list")
            continue
        if any("uv run" in item for item in commands):
            errors.append(f"{key}.commands must use installed CLI commands, not uv run")
        if any("--repo-root" in item for item in commands):
            errors.append(f"{key}.commands must not include --repo-root")
        if key == "hosted_mcp":
            errors.extend(
                _hosted_quickstart_token_flow_contract_errors(
                    raw_target,
                    label="hosted_mcp",
                )
            )
            errors.extend(
                _hosted_all_client_commands_contract_errors(
                    raw_target,
                    label=f"{key}.client_commands",
                )
            )
        errors.extend(_agent_workflows_contract_errors(raw_target, label=f"{key}.agent_workflows"))
    return errors


def _hosted_client_commands_contract_errors(
    parsed: dict[object, object],
    *,
    label: str,
) -> list[str]:
    client_commands = _string_list(parsed.get("client_commands"))
    if client_commands is None or not client_commands:
        return [f"{label} must be a non-empty string list"]

    command_text = " ".join(client_commands)
    errors: list[str] = []
    if "/mcp" not in command_text:
        errors.append(f"{label} must include the hosted /mcp URL")
    if "POLICYNIM_TOKEN" not in command_text:
        errors.append(f"{label} must reference POLICYNIM_TOKEN")
    if _contains_generated_token_placeholder(client_commands):
        errors.append(f"{label} must not embed generated bearer tokens")

    if "codex mcp add policynim" in command_text:
        for token in ("--url", "--bearer-token-env-var"):
            if token not in command_text:
                errors.append(f"{label} must include {token!r}")
    elif "claude mcp add" in command_text:
        for token in ("--transport http", "--header", "Authorization: Bearer $POLICYNIM_TOKEN"):
            if token not in command_text:
                errors.append(f"{label} must include {token!r}")
    else:
        errors.append(f"{label} must include a Codex or Claude Code MCP add command")
    return errors


def _hosted_all_client_commands_contract_errors(
    parsed: dict[object, object],
    *,
    label: str,
) -> list[str]:
    errors = _hosted_client_commands_contract_errors(parsed, label=label)
    client_commands = _string_list(parsed.get("client_commands"))
    if client_commands is None:
        return errors

    command_text = " ".join(client_commands)
    if "codex mcp add policynim" not in command_text:
        errors.append(f"{label} must include a Codex MCP add command")
    if "claude mcp add" not in command_text:
        errors.append(f"{label} must include a Claude Code MCP add command")
    return errors


def _hosted_quickstart_token_flow_contract_errors(
    parsed: dict[object, object],
    *,
    label: str = "hosted quickstart",
) -> list[str]:
    errors: list[str] = []
    hosted_url = parsed.get("hosted_url")
    beta_portal_url = parsed.get("beta_portal_url")
    if not (
        isinstance(hosted_url, str)
        and hosted_url.startswith("https://")
        and hosted_url.endswith("/mcp")
    ):
        errors.append(f"{label} hosted_url must be an https /mcp URL")
    if not (
        isinstance(beta_portal_url, str)
        and beta_portal_url.startswith("https://")
        and beta_portal_url.endswith("/beta")
    ):
        errors.append(f"{label} beta_portal_url must be an https /beta URL")

    steps = _string_list(parsed.get("steps"))
    if steps is None:
        errors.append(f"{label} steps must be a string list")
        errors.append(f"{label} steps must explain the browser token flow")
        return errors
    step_text = " ".join(steps).lower()
    required_tokens = ["browser", "token"]
    if isinstance(hosted_url, str):
        required_tokens.append(hosted_url.lower())
    if isinstance(beta_portal_url, str):
        required_tokens.append(beta_portal_url.lower())
    if not all(token in step_text for token in required_tokens):
        errors.append(f"{label} steps must explain the browser token flow")
    return errors


def _agent_workflows_contract_errors(
    parsed: dict[object, object],
    *,
    label: str = "agent_workflows",
) -> list[str]:
    workflows = parsed.get("agent_workflows")
    if not isinstance(workflows, list) or not workflows:
        return [f"{label} must be a non-empty list"]
    if not all(isinstance(item, dict) for item in workflows):
        return [f"{label} entries must be JSON objects"]

    workflow_text = " ".join(
        " ".join(str(item.get(field, "")) for field in ("title", "tool", "prompt"))
        for item in workflows
    )
    errors: list[str] = []
    for token in (
        "Preflight before implementation",
        "policy_preflight",
        "Before editing",
        "cited constraints",
        "insufficient_context",
        "Retrieve policy evidence while debugging",
        "policy_search",
        "cited policy lines",
        "Verify MCP tool availability",
        "before starting implementation",
    ):
        if token not in workflow_text:
            errors.append(f"{label} must mention {token!r}")
    return errors


def _mcp_config_json_contract_name(*, expected_client: str, expected_target: str) -> str:
    for contract_name, (_check_name, client, target) in MCP_CONFIG_JSON_CONTRACTS.items():
        if expected_client == client and expected_target == target:
            return contract_name
    return f"mcp-config-{expected_client}-{expected_target}"


def _mcp_config_contract_check(
    *,
    name: str,
    payload: str,
    expected_client: str,
    expected_target: str,
) -> dict[str, Any]:
    contract_name = _mcp_config_json_contract_name(
        expected_client=expected_client,
        expected_target=expected_target,
    )
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        return _synthetic_contract_failure(
            name=name,
            message=f"Invalid JSON output: {exc}",
            contract_name=contract_name,
        )
    if not isinstance(parsed, dict):
        return _synthetic_contract_failure(
            name=name,
            message="MCP config output must be a JSON object.",
            contract_name=contract_name,
        )

    payload_object = cast(dict[str, Any], parsed)
    errors = _mcp_config_common_contract_errors(
        payload_object,
        expected_client=expected_client,
        expected_target=expected_target,
    )
    if expected_target == "local-stdio":
        errors.extend(_mcp_config_local_stdio_contract_errors(payload_object, expected_client))
    elif expected_target == "hosted-http":
        errors.extend(_mcp_config_hosted_http_contract_errors(payload_object, expected_client))
    else:
        errors.append(f"unsupported MCP config target {expected_target!r}")

    if errors:
        return _synthetic_contract_failure(
            name=name,
            message="; ".join(errors),
            contract_name=contract_name,
        )
    return {
        "name": name,
        "status": "passed",
        "command": _json_contract_command(contract_name, "<captured-output>"),
        "cwd": None,
        "returncode": 0,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _mcp_config_common_contract_errors(
    parsed: dict[str, Any],
    *,
    expected_client: str,
    expected_target: str,
) -> list[str]:
    errors: list[str] = []
    if parsed.get("schema_version") != "1":
        errors.append(f"schema_version expected '1', got {parsed.get('schema_version')!r}")
    if parsed.get("client") != expected_client:
        errors.append(f"client expected {expected_client!r}, got {parsed.get('client')!r}")
    if parsed.get("target") != expected_target:
        errors.append(f"target expected {expected_target!r}, got {parsed.get('target')!r}")
    if parsed.get("server_name") != "policynim":
        errors.append(f"server_name expected 'policynim', got {parsed.get('server_name')!r}")
    next_steps = _string_list(parsed.get("next_steps"))
    if next_steps is None or not next_steps:
        errors.append("next_steps must be a non-empty string list")
    return errors


def _mcp_config_local_stdio_contract_errors(
    parsed: dict[str, Any],
    expected_client: str,
) -> list[str]:
    errors: list[str] = []
    if parsed.get("local_launch_mode") != "installed-cli":
        errors.append(
            f"local_launch_mode expected 'installed-cli', got {parsed.get('local_launch_mode')!r}"
        )
    if "repo_root" in parsed:
        errors.append("installed local stdio config must not include repo_root")

    next_steps = _string_list(parsed.get("next_steps"))
    if next_steps is not None and "mcp-smoke" not in " ".join(next_steps):
        errors.append("installed local stdio next_steps must mention mcp-smoke")

    safety = _string_list(parsed.get("safety"))
    if safety is None:
        errors.append("installed local stdio config safety must be a string list")
    else:
        safety_text = " ".join(safety)
        if "exact local filesystem paths" not in safety_text:
            errors.append("installed local stdio safety must warn about exact local paths")
        if "policynim support-bundle" not in safety_text:
            errors.append("installed local stdio safety must point to policynim support-bundle")

    if expected_client == "codex":
        errors.extend(_codex_local_stdio_contract_errors(parsed))
    elif expected_client == "claude-code":
        errors.extend(_claude_local_stdio_contract_errors(parsed))
    else:
        errors.append(f"unsupported local MCP client {expected_client!r}")
    return errors


def _codex_local_stdio_contract_errors(parsed: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    command = _string_list(parsed.get("codex_cli_command"))
    if command is None:
        errors.append("codex_cli_command must be a string list")
    else:
        if not _has_subsequence(command, ["policynim", "mcp", "--transport", "stdio"]) or any(
            part in command for part in ("uv", "--directory", "--repo-root")
        ):
            errors.append("installed local stdio config must launch policynim directly")
        if "NVIDIA_API_KEY=$NVIDIA_API_KEY" not in command:
            errors.append("codex local stdio command must pass through NVIDIA_API_KEY")

    shell_command = parsed.get("codex_cli_shell_command")
    if isinstance(shell_command, str) and (
        "uv run" in shell_command
        or "--directory" in shell_command
        or "--repo-root" in shell_command
    ):
        errors.append("installed local stdio config must launch policynim directly")

    codex_app = _object_dict(parsed.get("codex_app"))
    if codex_app is None:
        errors.append("codex_app must be a JSON object")
    else:
        if codex_app.get("command") != "policynim":
            errors.append("installed local stdio config must launch policynim directly")
        if codex_app.get("arguments") != ["mcp", "--transport", "stdio"]:
            errors.append("codex_app.arguments expected ['mcp', '--transport', 'stdio']")
        env_passthrough = _string_list(codex_app.get("environment_variable_passthrough"))
        if env_passthrough is None or "NVIDIA_API_KEY" not in env_passthrough:
            errors.append("codex_app must pass through NVIDIA_API_KEY")
    return errors


def _claude_local_stdio_contract_errors(parsed: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    server_config = _nested_object_dict(parsed, "config", "mcpServers", "policynim")
    if server_config is None:
        errors.append("config.mcpServers.policynim must be a JSON object")
    else:
        if server_config.get("type") != "stdio":
            errors.append("Claude local MCP server type expected 'stdio'")
        if server_config.get("command") != "policynim":
            errors.append("installed Claude local stdio config must launch policynim directly")
        if server_config.get("args") != ["mcp", "--transport", "stdio"]:
            errors.append("Claude local MCP args expected ['mcp', '--transport', 'stdio']")
        env = _object_dict(server_config.get("env"))
        if env is None or env.get("NVIDIA_API_KEY") != "${NVIDIA_API_KEY}":
            errors.append("Claude local MCP config must reference ${NVIDIA_API_KEY}")

    cli_command = _string_list(parsed.get("cli_command"))
    if cli_command is None:
        errors.append("cli_command must be a string list")
    else:
        if not _has_subsequence(cli_command, ["claude", "mcp", "add-json", "policynim"]):
            errors.append("Claude local cli_command must add the policynim server")
        if any(part in cli_command for part in ("uv", "--directory", "--repo-root")):
            errors.append("installed Claude local stdio config must launch policynim directly")
    return errors


def _mcp_config_hosted_http_contract_errors(
    parsed: dict[str, Any],
    expected_client: str,
) -> list[str]:
    errors: list[str] = []
    if "repo_root" in parsed:
        errors.append("hosted MCP config must not include repo_root")
    if parsed.get("hosted_url") != "https://example.invalid/mcp":
        errors.append(
            f"hosted_url expected 'https://example.invalid/mcp', got {parsed.get('hosted_url')!r}"
        )
    if parsed.get("beta_portal_url") != "https://example.invalid/beta":
        errors.append(
            "beta_portal_url expected 'https://example.invalid/beta', "
            f"got {parsed.get('beta_portal_url')!r}"
        )
    if parsed.get("hosted_url_placeholder") is not True:
        errors.append(
            f"hosted_url_placeholder expected True, got {parsed.get('hosted_url_placeholder')!r}"
        )
    if parsed.get("bearer_token_env_var") != "POLICYNIM_TOKEN":
        errors.append(
            "bearer_token_env_var expected 'POLICYNIM_TOKEN', "
            f"got {parsed.get('bearer_token_env_var')!r}"
        )

    next_steps = _string_list(parsed.get("next_steps"))
    if next_steps is not None:
        next_step_text = " ".join(next_steps)
        for token in (
            "Replace the hosted URL placeholder",
            "POLICYNIM_TOKEN",
            "policy_preflight",
            "policy_search",
        ):
            if token not in next_step_text:
                errors.append(f"hosted MCP next_steps must mention {token!r}")

    if expected_client == "codex":
        errors.extend(_codex_hosted_http_contract_errors(parsed))
    elif expected_client == "claude-code":
        errors.extend(_claude_hosted_http_contract_errors(parsed))
    else:
        errors.append(f"unsupported hosted MCP client {expected_client!r}")
    return errors


def _codex_hosted_http_contract_errors(parsed: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    command = _string_list(parsed.get("codex_cli_command"))
    if command is None:
        errors.append("codex_cli_command must be a string list")
    else:
        if not _has_subsequence(command, ["codex", "mcp", "add", "policynim"]):
            errors.append("Codex hosted command must add the policynim server")
        for token in (
            "--url",
            "https://example.invalid/mcp",
            "--bearer-token-env-var",
            "POLICYNIM_TOKEN",
        ):
            if token not in command:
                errors.append(f"Codex hosted command must include {token!r}")
        if _contains_generated_token_placeholder(command):
            errors.append("Codex hosted command must not embed generated bearer tokens")

    shell_command = parsed.get("codex_cli_shell_command")
    if isinstance(shell_command, str) and "<generated-beta-token>" in shell_command:
        errors.append("Codex hosted shell command must not embed generated bearer tokens")
    return errors


def _claude_hosted_http_contract_errors(parsed: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    command = _string_list(parsed.get("cli_command"))
    if command is None:
        errors.append("cli_command must be a string list")
    else:
        if not _has_subsequence(command, ["claude", "mcp", "add"]):
            errors.append("Claude hosted command must add the policynim server")
        for token in (
            "--transport",
            "http",
            "https://example.invalid/mcp",
            "--header",
            "Authorization: Bearer $POLICYNIM_TOKEN",
        ):
            if token not in command:
                errors.append(f"Claude hosted command must include {token!r}")
        if _contains_generated_token_placeholder(command):
            errors.append("Claude hosted command must not embed generated bearer tokens")

    shell_command = parsed.get("cli_shell_command")
    if isinstance(shell_command, str) and "<generated-beta-token>" in shell_command:
        errors.append("Claude hosted shell command must not embed generated bearer tokens")
    return errors


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return value


def _object_dict(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, Any], value)


def _nested_object_dict(value: object, *keys: str) -> dict[str, Any] | None:
    current = _object_dict(value)
    for key in keys:
        if current is None:
            return None
        current = _object_dict(current.get(key))
    return current


def _has_subsequence(items: Sequence[str], expected: Sequence[str]) -> bool:
    if not expected:
        return True
    if len(expected) > len(items):
        return False
    return any(
        list(items[index : index + len(expected)]) == list(expected)
        for index in range(len(items) - len(expected) + 1)
    )


def _contains_generated_token_placeholder(items: Sequence[str]) -> bool:
    return any("<generated-beta-token>" in item for item in items)


def _launch_issue_contract_check(*, name: str, payload: str) -> dict[str, Any]:
    requested_probe_flags = (
        "--release-attestation-asset-name",
        "--pypi-publish-run-url",
        "--hosted-mcp-url",
        "--hosted-smoke-run-url",
        "--mcp-client-evidence-file",
    )
    command_lines = [
        line.strip()
        for line in payload.splitlines()
        if "uv run python scripts/collect_launch_evidence.py" in line
    ]
    errors: list[str] = []
    if "## Missing Evidence Collection Commands" not in payload:
        errors.append("launch issue must include Missing Evidence Collection Commands")
    for command_line in command_lines:
        if not any(flag in command_line for flag in requested_probe_flags):
            continue
        if "--require-requested-probes" not in command_line:
            errors.append(
                "requested external proof command must include "
                f"--require-requested-probes: {command_line}"
            )
    if errors:
        return _synthetic_launch_issue_contract_failure(
            name=name,
            message="; ".join(errors),
        )
    return {
        "name": name,
        "status": "passed",
        "command": _launch_issue_contract_command("<captured-output>"),
        "cwd": None,
        "returncode": 0,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _init_help_contract_check(*, name: str, payload: str) -> dict[str, Any]:
    result = _help_contract_check(
        command_name="init",
        payload=payload,
        legacy_init_command=True,
    )
    if result["name"] == name:
        return result
    result["name"] = name
    return result


def _help_contract_check(
    *,
    command_name: str,
    payload: str,
    legacy_init_command: bool = False,
) -> dict[str, Any]:
    contract = HELP_TEXT_CONTRACTS.get(command_name)
    if contract is None:
        expected_commands = ", ".join(sorted(HELP_TEXT_CONTRACTS))
        return _synthetic_help_contract_failure(
            name="help_contract",
            command_name=command_name,
            message=(
                f"Unknown help contract {command_name!r}. Expected one of: {expected_commands}."
            ),
            legacy_init_command=legacy_init_command,
        )
    check_name, required_tokens = contract
    missing = [token for token in required_tokens if token not in payload]
    if missing:
        return _synthetic_help_contract_failure(
            name=check_name,
            command_name=command_name,
            message=(
                f"{command_name} help must include " + ", ".join(repr(token) for token in missing)
            ),
            legacy_init_command=legacy_init_command,
        )
    return {
        "name": check_name,
        "status": "passed",
        "command": (
            _init_help_contract_command("<captured-output>")
            if legacy_init_command
            else _help_contract_command(command_name, "<captured-output>")
        ),
        "cwd": None,
        "returncode": 0,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _synthetic_contract_failure(
    *,
    name: str,
    message: str,
    contract_name: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "failed",
        "command": _json_contract_command(contract_name, "<captured-output>"),
        "cwd": None,
        "returncode": 1,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": message,
    }


def _synthetic_launch_issue_contract_failure(*, name: str, message: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "failed",
        "command": _launch_issue_contract_command("<captured-output>"),
        "cwd": None,
        "returncode": 1,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": message,
    }


def _synthetic_init_help_contract_failure(*, name: str, message: str) -> dict[str, Any]:
    return _synthetic_help_contract_failure(
        name=name,
        command_name="init",
        message=message,
        legacy_init_command=True,
    )


def _synthetic_help_contract_failure(
    *,
    name: str,
    command_name: str,
    message: str,
    legacy_init_command: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "failed",
        "command": (
            _init_help_contract_command("<captured-output>")
            if legacy_init_command
            else _help_contract_command(command_name, "<captured-output>")
        ),
        "cwd": None,
        "returncode": 1,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": message,
    }


def _json_parse_command(payload_label: str) -> list[str]:
    return [sys.executable, "-m", "json.tool", payload_label]


def _json_contract_command(contract_name: str, payload_label: str) -> list[str]:
    return [
        sys.executable,
        "scripts/release_check.py",
        "--validate-json-contract",
        contract_name,
        payload_label,
    ]


def _launch_issue_contract_command(payload_label: str) -> list[str]:
    return [
        sys.executable,
        "scripts/release_check.py",
        "--validate-launch-issue-contract",
        payload_label,
    ]


def _init_help_contract_command(payload_label: str) -> list[str]:
    return [
        sys.executable,
        "scripts/release_check.py",
        "--validate-init-help-contract",
        payload_label,
    ]


def _help_contract_command(command_name: str, payload_label: str) -> list[str]:
    return [
        sys.executable,
        "scripts/release_check.py",
        "--validate-help-contract",
        command_name,
        payload_label,
    ]


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_policynim(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "policynim.exe"
    return venv_dir / "bin" / "policynim"


def _tail(value: str | None) -> str:
    if not value:
        return ""
    return value[-TAIL_LIMIT:]


def _public_check(check: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in check.items() if not key.startswith("_")}


def _render_text_summary(result: dict[str, Any]) -> str:
    lines = [
        "PolicyNIM release check",
        f"Decision: {result['decision']}",
        f"Mode: {'strict public launch' if result['strict_public'] else 'local release'}",
        f"Repo: {result['repo_root']}",
        "",
        "Checks:",
    ]
    if result["external_evidence_file"]:
        lines.insert(3, f"External evidence file: {result['external_evidence_file']}")
    for check in result["checks"]:
        lines.append(f"- {check['name']}: {check['status']}")
        if check["status"] == "failed" and check["stderr_tail"]:
            lines.append(f"  stderr: {check['stderr_tail']}")
    if result["decision"] == "not_evaluated":
        lines.append("")
        lines.append("Dry run only; no ship/hold evidence was produced.")
    return "\n".join(lines)


def _exit_code(result: dict[str, Any]) -> int:
    if result["decision"] == "hold":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
