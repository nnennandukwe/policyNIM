"""Release ship/hold check contract tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "release_check.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_launch_issue_output() -> str:
    commands = (
        "uv run python scripts/collect_launch_evidence.py "
        "--release-attestation-asset-name install.sh "
        "--require-requested-probes "
        "--write-external-evidence-file docs/launch-evidence.json "
        "--merge-existing --format json\n"
        "uv run python scripts/collect_launch_evidence.py "
        "--pypi-publish-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>' "
        "--require-requested-probes "
        "--write-external-evidence-file docs/launch-evidence.json "
        "--merge-existing --format json\n"
        "uv run python scripts/collect_launch_evidence.py "
        "--hosted-mcp-url 'https://<railway-domain>/mcp' "
        "--require-requested-probes "
        "--write-external-evidence-file docs/launch-evidence.json "
        "--merge-existing --format json\n"
        "uv run python scripts/collect_launch_evidence.py "
        "--hosted-smoke-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>' "
        "--require-requested-probes "
        "--write-external-evidence-file docs/launch-evidence.json "
        "--merge-existing --format json\n"
        "uv run python scripts/collect_launch_evidence.py "
        "--mcp-client-evidence-file launch-notes/codex-mcp-session.json "
        "--require-requested-probes "
        "--write-external-evidence-file docs/launch-evidence.json "
        "--merge-existing --format json\n"
    )
    return (
        "# PolicyNIM Public Launch Evidence\n\n"
        "## Missing Evidence Collection Commands\n\n"
        f"{commands}"
    )


def _valid_init_help_output() -> str:
    return (
        "Usage: policynim init [OPTIONS]\n\n"
        "Run interactive local setup, prompt for NVIDIA_API_KEY, and write the "
        "local PolicyNIM config file.\n"
    )


def _valid_ingest_help_output() -> str:
    return (
        "Usage: policynim ingest [OPTIONS]\n\n"
        "Build the local policy index from the shipped corpus.\n"
    )


def _valid_preflight_help_output() -> str:
    return (
        "Usage: policynim preflight [OPTIONS]\n\n"
        "Return policy guidance for a coding task.\n\n"
        "--task TEXT Describe the coding task that needs policy guidance.\n"
    )


def _valid_agent_workflows() -> list[dict[str, str]]:
    return [
        {
            "title": "Preflight before implementation",
            "tool": "policy_preflight",
            "prompt": (
                "Before editing, call policy_preflight for: Implement a refresh-token "
                "cleanup background job. Use the cited constraints in your implementation "
                "plan. If the result is insufficient_context, stop and call policy_search "
                "with a narrower query before changing files."
            ),
        },
        {
            "title": "Retrieve policy evidence while debugging",
            "tool": "policy_search",
            "prompt": (
                "Use policy_search for: release installer checksum verification. "
                "Summarize the relevant cited policy lines before proposing a fix."
            ),
        },
        {
            "title": "Verify MCP tool availability",
            "tool": "list_tools",
            "prompt": (
                "List the PolicyNIM MCP tools and confirm policy_preflight and "
                "policy_search are available before starting implementation."
            ),
        },
    ]


def _valid_hosted_client_commands() -> list[str]:
    return [
        (
            "codex mcp add policynim --url https://example.invalid/mcp "
            "--bearer-token-env-var POLICYNIM_TOKEN"
        )
    ]


def _valid_support_bundle_hosted_client_commands() -> list[str]:
    return [
        *_valid_hosted_client_commands(),
        (
            "claude mcp add --transport http policynim https://example.invalid/mcp "
            '--header "Authorization: Bearer $POLICYNIM_TOKEN"'
        ),
    ]


def _valid_hosted_token_flow_fields() -> dict[str, object]:
    return {
        "hosted_url": "https://example.invalid/mcp",
        "beta_portal_url": "https://example.invalid/beta",
        "steps": [
            (
                "Open https://example.invalid/mcp in a browser; it routes to "
                "https://example.invalid/beta for token creation."
            )
        ],
        "next_steps": [
            (
                "For Claude Code setup commands, rerun "
                "`policynim quickstart --target hosted-mcp --client claude-code`."
            ),
            "Ask your client to call `policy_preflight` for the main workflow.",
        ],
    }


def _valid_quickstart_output(
    *,
    target: str,
    requires_local_setup: bool,
    commands: list[str],
    local_launch_mode: str | None = None,
    client_commands: list[str] | None = None,
) -> str:
    payload: dict[str, object] = {
        "target": target,
        "requires_local_setup": requires_local_setup,
        "calls_external_services": False,
        "commands": commands,
        "agent_workflows": _valid_agent_workflows(),
    }
    if target == "hosted-mcp":
        payload.update(_valid_hosted_token_flow_fields())
    if client_commands is None and target == "hosted-mcp":
        client_commands = _valid_hosted_client_commands()
    if client_commands is not None:
        payload["client_commands"] = client_commands
    if local_launch_mode is not None:
        payload["local_launch_mode"] = local_launch_mode
    return json.dumps(payload)


def _valid_support_bundle_payload() -> dict[str, object]:
    return {
        "schema_version": "1",
        "first_run": {
            "runtime_mode": "standalone",
            "default_target": "hosted-mcp",
            "targets": {
                "hosted_mcp": {
                    "target": "hosted-mcp",
                    "requires_local_setup": False,
                    "quickstart_command": (
                        "policynim quickstart --target hosted-mcp --format json"
                    ),
                    "calls_external_services": False,
                    "commands": ["policynim mcp-config"],
                    "client_commands": _valid_support_bundle_hosted_client_commands(),
                    "agent_workflows": _valid_agent_workflows(),
                    **_valid_hosted_token_flow_fields(),
                },
                "local_cli": {
                    "target": "local-cli",
                    "requires_local_setup": True,
                    "quickstart_command": ("policynim quickstart --target local-cli --format json"),
                    "calls_external_services": False,
                    "commands": ["policynim --help"],
                    "agent_workflows": _valid_agent_workflows(),
                },
                "local_mcp": {
                    "target": "local-mcp",
                    "local_launch_mode": "installed-cli",
                    "requires_local_setup": True,
                    "calls_external_services": False,
                    "quickstart_command": (
                        "policynim quickstart --target local-mcp --client codex --format json"
                    ),
                    "commands": ["policynim mcp-config --target local-stdio --client codex"],
                    "agent_workflows": _valid_agent_workflows(),
                },
            },
        },
    }


def _valid_codex_local_mcp_config_output() -> str:
    return json.dumps(
        {
            "schema_version": "1",
            "client": "codex",
            "target": "local-stdio",
            "server_name": "policynim",
            "local_launch_mode": "installed-cli",
            "codex_cli_command": [
                "codex",
                "mcp",
                "add",
                "policynim",
                "--env",
                "NVIDIA_API_KEY=$NVIDIA_API_KEY",
                "--",
                "policynim",
                "mcp",
                "--transport",
                "stdio",
            ],
            "codex_cli_shell_command": (
                "codex mcp add policynim --env NVIDIA_API_KEY=$NVIDIA_API_KEY "
                "-- policynim mcp --transport stdio"
            ),
            "codex_app": {
                "name": "policynim",
                "transport": "STDIO",
                "command": "policynim",
                "arguments": ["mcp", "--transport", "stdio"],
                "environment_variable_passthrough": ["NVIDIA_API_KEY"],
            },
            "next_steps": [
                "policynim doctor",
                "policynim mcp-smoke",
                "policynim ingest",
            ],
            "safety": [
                "Generated local stdio config can include exact local filesystem paths; "
                "do not paste it into public issues.",
                "Use `policynim support-bundle` for public diagnostics.",
            ],
        }
    )


def _valid_claude_local_mcp_config_output() -> str:
    return json.dumps(
        {
            "schema_version": "1",
            "client": "claude-code",
            "target": "local-stdio",
            "server_name": "policynim",
            "local_launch_mode": "installed-cli",
            "config": {
                "mcpServers": {
                    "policynim": {
                        "type": "stdio",
                        "command": "policynim",
                        "args": ["mcp", "--transport", "stdio"],
                        "env": {"NVIDIA_API_KEY": "${NVIDIA_API_KEY}"},
                    }
                }
            },
            "cli_command": [
                "claude",
                "mcp",
                "add-json",
                "policynim",
                (
                    '{"type":"stdio","command":"policynim","args":["mcp",'
                    '"--transport","stdio"],"env":{"NVIDIA_API_KEY":"${NVIDIA_API_KEY}"}}'
                ),
            ],
            "cli_shell_command": (
                "claude mcp add-json policynim "
                '\'{"type":"stdio","command":"policynim","args":["mcp",'
                '"--transport","stdio"],"env":{"NVIDIA_API_KEY":"${NVIDIA_API_KEY}"}}\''
            ),
            "next_steps": [
                "policynim doctor",
                "policynim mcp-smoke",
                "policynim ingest",
            ],
            "safety": [
                "Generated local stdio config can include exact local filesystem paths; "
                "do not paste it into public issues.",
                "Use `policynim support-bundle` for public diagnostics.",
            ],
        }
    )


def _valid_codex_hosted_mcp_config_output() -> str:
    return json.dumps(
        {
            "schema_version": "1",
            "client": "codex",
            "target": "hosted-http",
            "server_name": "policynim",
            "hosted_url": "https://example.invalid/mcp",
            "beta_portal_url": "https://example.invalid/beta",
            "hosted_url_placeholder": True,
            "bearer_token_env_var": "POLICYNIM_TOKEN",
            "codex_cli_command": [
                "codex",
                "mcp",
                "add",
                "policynim",
                "--url",
                "https://example.invalid/mcp",
                "--bearer-token-env-var",
                "POLICYNIM_TOKEN",
            ],
            "codex_cli_shell_command": (
                "codex mcp add policynim --url https://example.invalid/mcp "
                "--bearer-token-env-var POLICYNIM_TOKEN"
            ),
            "next_steps": [
                "Replace the hosted URL placeholder with the deployed /mcp URL.",
                "Open https://example.invalid/beta to generate or rotate a hosted API key.",
                "Export POLICYNIM_TOKEN='<generated-beta-token>'",
                "Ask your client to call `policy_preflight` for the main workflow.",
                "Use `policy_search` first when you need raw retrieval/debugging context.",
            ],
        }
    )


def _valid_claude_hosted_mcp_config_output() -> str:
    return json.dumps(
        {
            "schema_version": "1",
            "client": "claude-code",
            "target": "hosted-http",
            "server_name": "policynim",
            "hosted_url": "https://example.invalid/mcp",
            "beta_portal_url": "https://example.invalid/beta",
            "hosted_url_placeholder": True,
            "bearer_token_env_var": "POLICYNIM_TOKEN",
            "cli_command": [
                "claude",
                "mcp",
                "add",
                "--transport",
                "http",
                "policynim",
                "https://example.invalid/mcp",
                "--header",
                "Authorization: Bearer $POLICYNIM_TOKEN",
            ],
            "cli_shell_command": (
                "claude mcp add --transport http policynim https://example.invalid/mcp "
                '--header "Authorization: Bearer $POLICYNIM_TOKEN"'
            ),
            "next_steps": [
                "Replace the hosted URL placeholder with the deployed /mcp URL.",
                "Open https://example.invalid/beta to generate or rotate a hosted API key.",
                "Export POLICYNIM_TOKEN='<generated-beta-token>'",
                "Ask your client to call `policy_preflight` for the main workflow.",
                "Use `policy_search` first when you need raw retrieval/debugging context.",
            ],
        }
    )


def _help_stdout_for_command(command_list: Sequence[str]) -> str | None:
    if "init" in command_list and "--help" in command_list:
        return _valid_init_help_output()
    if "ingest" in command_list and "--help" in command_list:
        return _valid_ingest_help_output()
    if "preflight" in command_list and "--help" in command_list:
        return _valid_preflight_help_output()
    return None


def test_release_check_runs_required_gates_and_clean_wheel_smoke(tmp_path: Path) -> None:
    """Keep the local ship/hold command aligned with the documented release gate."""
    module = _load_script_module()
    commands: list[list[str]] = []

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        commands.append(command_list)
        if command_list[:2] == ["uv", "build"]:
            out_dir = Path(command_list[command_list.index("--out-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "policynim-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        help_stdout = _help_stdout_for_command(command_list)
        if help_stdout is not None:
            stdout = help_stdout
        elif "quickstart" in command_list and "local-cli" in command_list:
            stdout = _valid_quickstart_output(
                target="local-cli",
                requires_local_setup=True,
                commands=["policynim --help"],
            )
        elif "quickstart" in command_list and "local-mcp" in command_list:
            stdout = _valid_quickstart_output(
                target="local-mcp",
                requires_local_setup=True,
                local_launch_mode="installed-cli",
                commands=["policynim mcp-config --target local-stdio --client codex"],
            )
        elif "quickstart" in command_list:
            stdout = _valid_quickstart_output(
                target="hosted-mcp",
                requires_local_setup=False,
                commands=[],
            )
        elif "doctor" in command_list:
            stdout = '{"status":"ok"}'
        elif "mcp-smoke" in command_list:
            stdout = (
                '{"status":"ok","transport":"stdio",'
                '"tools":["policy_preflight","policy_search"],"missing_tools":[]}'
            )
        elif (
            "mcp-config" in command_list
            and "hosted-http" in command_list
            and "claude-code" in command_list
        ):
            stdout = _valid_claude_hosted_mcp_config_output()
        elif "mcp-config" in command_list and "hosted-http" in command_list:
            stdout = _valid_codex_hosted_mcp_config_output()
        elif (
            "mcp-config" in command_list
            and "local-stdio" in command_list
            and "claude-code" in command_list
        ):
            stdout = _valid_claude_local_mcp_config_output()
        elif "mcp-config" in command_list:
            stdout = _valid_codex_local_mcp_config_output()
        elif "support-bundle" in command_list:
            support_payload = _valid_support_bundle_payload()
            support_payload["payload"] = "x" * 5000
            stdout = json.dumps(support_payload)
        elif any(part.endswith("oss_readiness_check.py") for part in command_list):
            stdout = (
                _valid_launch_issue_output()
                if "launch-issue" in command_list
                else '{"decision":"local_ready_external_missing"}'
            )
        elif any(part.endswith("check_release_notes.py") for part in command_list):
            stdout = '{"decision":"passed","version":"0.1.0"}'
        elif any(
            part.endswith(("sync_github_labels.py", "sync_github_topics.py"))
            for part in command_list
        ):
            stdout = '{"dry_run":true,"plan":[]}'
        else:
            stdout = f"ok from {cwd}"
        return subprocess.CompletedProcess(
            command_list,
            0,
            stdout=stdout,
            stderr="",
        )

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    assert result["decision"] == "ship"
    assert result["required_passed"] is True
    assert [entry["name"] for entry in result["checks"]] == [
        "lockfile",
        "ruff",
        "pyright",
        "offline_pytest",
        "release_notes_check",
        "oss_readiness_json",
        "oss_readiness_launch_issue",
        "oss_readiness_launch_issue_contract",
        "github_label_taxonomy_dry_run_json",
        "github_topic_taxonomy_dry_run_json",
        "build_distribution",
        "create_clean_venv",
        "upgrade_pip",
        "install_wheel",
        "installed_cli_help",
        "installed_cli_init_help",
        "installed_cli_ingest_help",
        "installed_cli_preflight_help",
        "installed_cli_quickstart_json",
        "installed_cli_quickstart_local_cli_json",
        "installed_cli_quickstart_local_mcp_json",
        "installed_cli_doctor_json",
        "installed_cli_support_bundle",
        "installed_cli_mcp_smoke_json",
        "installed_cli_mcp_config_json",
        "installed_cli_claude_mcp_config_json",
        "installed_cli_hosted_mcp_config_json",
        "installed_cli_claude_hosted_mcp_config_json",
        "installed_cli_version",
        "init_help_contract",
        "ingest_help_contract",
        "preflight_help_contract",
        "quickstart_json_parse",
        "quickstart_contract",
        "quickstart_local_cli_json_parse",
        "quickstart_local_cli_contract",
        "quickstart_local_mcp_json_parse",
        "quickstart_local_mcp_contract",
        "doctor_json_parse",
        "support_bundle_json_parse",
        "support_bundle_contract",
        "mcp_smoke_json_parse",
        "mcp_config_json_parse",
        "mcp_config_contract",
        "claude_mcp_config_json_parse",
        "claude_mcp_config_contract",
        "installed_cli_mcp_smoke_from_codex_config_json",
        "mcp_smoke_from_codex_config_json_parse",
        "installed_cli_mcp_smoke_from_claude_config_json",
        "mcp_smoke_from_claude_config_json_parse",
        "hosted_mcp_config_json_parse",
        "hosted_mcp_config_contract",
        "claude_hosted_mcp_config_json_parse",
        "claude_hosted_mcp_config_contract",
    ]
    assert ["uv", "lock", "--check"] in commands
    assert ["uv", "run", "ruff", "check", "."] in commands
    assert ["uv", "run", "pyright"] in commands
    assert ["uv", "run", "pytest", "-q", "-m", "not live and not docker_live"] in commands
    assert [
        sys.executable,
        "scripts/check_release_notes.py",
        "--format",
        "json",
    ] in commands
    assert [
        sys.executable,
        "scripts/oss_readiness_check.py",
        "--format",
        "json",
    ] in commands
    assert [
        sys.executable,
        "scripts/oss_readiness_check.py",
        "--format",
        "launch-issue",
    ] in commands
    assert [
        sys.executable,
        "scripts/sync_github_labels.py",
        "--format",
        "json",
    ] in commands
    assert [
        sys.executable,
        "scripts/sync_github_topics.py",
        "--format",
        "json",
    ] in commands
    assert any(command[:2] == ["uv", "build"] for command in commands)
    assert any(command[-2:] == ["init", "--help"] for command in commands)
    assert any(command[-2:] == ["ingest", "--help"] for command in commands)
    assert any(command[-2:] == ["preflight", "--help"] for command in commands)
    assert any(command[-3:] == ["quickstart", "--format", "json"] for command in commands)
    assert any(
        command[-5:] == ["quickstart", "--target", "local-cli", "--format", "json"]
        for command in commands
    )
    assert any(
        command[-5:] == ["quickstart", "--target", "local-mcp", "--format", "json"]
        for command in commands
    )
    assert any(command[-3:] == ["policynim", "doctor", "--format"] for command in commands) is False
    assert any(command[-3:] == ["doctor", "--format", "json"] for command in commands)
    assert any(command[-1:] == ["support-bundle"] for command in commands)
    assert any(command[-3:] == ["mcp-smoke", "--format", "json"] for command in commands)
    assert any(
        "mcp-smoke" in command
        and "--mcp-config-file" in command
        and "codex-mcp-config.json" in " ".join(command)
        for command in commands
    )
    assert any(
        "mcp-smoke" in command
        and "--mcp-config-file" in command
        and "claude-mcp-config.json" in " ".join(command)
        for command in commands
    )
    assert any(
        "mcp-config" in command
        and "--target" in command
        and "local-stdio" in command
        and "--client" in command
        and "codex" in command
        and "--repo-root" not in command
        and "--format" in command
        and "json" in command
        for command in commands
    )
    assert any(
        "mcp-config" in command
        and "--target" in command
        and "local-stdio" in command
        and "--client" in command
        and "claude-code" in command
        and "--repo-root" not in command
        and "--format" in command
        and "json" in command
        for command in commands
    )
    assert any(
        "mcp-config" in command
        and "--target" in command
        and "hosted-http" in command
        and "--client" in command
        and "codex" in command
        and "--hosted-url" in command
        and "https://example.invalid/mcp" in command
        and "--bearer-token-env-var" in command
        and "POLICYNIM_TOKEN" in command
        and "--format" in command
        and "json" in command
        for command in commands
    )
    assert any(
        "mcp-config" in command
        and "--target" in command
        and "hosted-http" in command
        and "--client" in command
        and "claude-code" in command
        and "--hosted-url" in command
        and "https://example.invalid/mcp" in command
        and "--bearer-token-env-var" in command
        and "POLICYNIM_TOKEN" in command
        and "--format" in command
        and "json" in command
        for command in commands
    )
    parse_checks = [check for check in result["checks"] if check["name"].endswith("_json_parse")]
    assert parse_checks
    assert all(check["command"][0] == sys.executable for check in parse_checks)
    assert all("_stdout_full" not in check for check in result["checks"])


def test_release_check_holds_and_skips_later_checks_after_failure(tmp_path: Path) -> None:
    """Avoid producing misleading release evidence after a required gate fails."""
    module = _load_script_module()
    commands: list[list[str]] = []

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        commands.append(command_list)
        return subprocess.CompletedProcess(
            command_list,
            1 if command_list == ["uv", "run", "ruff", "check", "."] else 0,
            stdout="",
            stderr="lint failed",
        )

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    assert result["decision"] == "hold"
    assert result["required_passed"] is False
    assert [entry["name"] for entry in result["checks"]] == ["lockfile", "ruff"]
    assert all(command[:2] != ["uv", "build"] for command in commands)


def test_release_check_parse_failure_keeps_actionable_command_metadata(
    tmp_path: Path,
) -> None:
    """Show the exact JSON parse gate when installed CLI output is malformed."""
    module = _load_script_module()

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        if command_list[:2] == ["uv", "build"]:
            out_dir = Path(command_list[command_list.index("--out-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "policynim-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        help_stdout = _help_stdout_for_command(command_list)
        if "quickstart" in command_list:
            stdout = "{not-json"
        elif help_stdout is not None:
            stdout = help_stdout
        elif "doctor" in command_list:
            stdout = '{"status":"ok"}'
        elif "mcp-smoke" in command_list:
            stdout = '{"status":"ok","tools":["policy_preflight","policy_search"]}'
        elif "mcp-config" in command_list:
            stdout = '{"target":"local-stdio"}'
        elif "support-bundle" in command_list:
            stdout = '{"status":"ok"}'
        elif any(part.endswith("oss_readiness_check.py") for part in command_list):
            stdout = (
                _valid_launch_issue_output()
                if "launch-issue" in command_list
                else '{"decision":"local_ready_external_missing"}'
            )
        elif any(part.endswith("check_release_notes.py") for part in command_list):
            stdout = '{"decision":"passed","version":"0.1.0"}'
        elif any(
            part.endswith(("sync_github_labels.py", "sync_github_topics.py"))
            for part in command_list
        ):
            stdout = '{"dry_run":true,"plan":[]}'
        else:
            stdout = f"ok from {cwd}"
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    failed = result["checks"][-1]
    assert result["decision"] == "hold"
    assert failed["name"] == "quickstart_json_parse"
    assert failed["status"] == "failed"
    assert failed["command"][0] == sys.executable
    assert failed["stderr_tail"].startswith("Invalid JSON output:")


def test_release_check_holds_when_installed_quickstart_semantics_drift(
    tmp_path: Path,
) -> None:
    """Do not ship a wheel whose local MCP quickstart points at checkout mode."""
    module = _load_script_module()

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        if command_list[:2] == ["uv", "build"]:
            out_dir = Path(command_list[command_list.index("--out-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "policynim-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        help_stdout = _help_stdout_for_command(command_list)
        if "quickstart" in command_list and "local-cli" in command_list:
            stdout = _valid_quickstart_output(
                target="local-cli",
                requires_local_setup=True,
                commands=["policynim --help"],
            )
        elif "quickstart" in command_list and "local-mcp" in command_list:
            stdout = _valid_quickstart_output(
                target="local-mcp",
                requires_local_setup=True,
                local_launch_mode="source-checkout",
                commands=["uv run policynim mcp-config --repo-root /tmp/policyNIM"],
            )
        elif "quickstart" in command_list:
            stdout = _valid_quickstart_output(
                target="hosted-mcp",
                requires_local_setup=False,
                commands=[],
            )
        elif help_stdout is not None:
            stdout = help_stdout
        elif "doctor" in command_list:
            stdout = '{"status":"ok"}'
        elif "mcp-smoke" in command_list:
            stdout = '{"status":"ok","tools":["policy_preflight","policy_search"]}'
        elif "mcp-config" in command_list:
            stdout = '{"target":"local-stdio","local_launch_mode":"installed-cli"}'
        elif "support-bundle" in command_list:
            stdout = '{"status":"ok"}'
        elif any(part.endswith("oss_readiness_check.py") for part in command_list):
            stdout = (
                _valid_launch_issue_output()
                if "launch-issue" in command_list
                else '{"decision":"local_ready_external_missing"}'
            )
        elif any(part.endswith("check_release_notes.py") for part in command_list):
            stdout = '{"decision":"passed","version":"0.1.0"}'
        elif any(
            part.endswith(("sync_github_labels.py", "sync_github_topics.py"))
            for part in command_list
        ):
            stdout = '{"dry_run":true,"plan":[]}'
        else:
            stdout = f"ok from {cwd}"
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    failed = result["checks"][-1]
    assert result["decision"] == "hold"
    assert failed["name"] == "quickstart_local_mcp_contract"
    assert failed["status"] == "failed"
    assert "local_launch_mode expected 'installed-cli'" in failed["stderr_tail"]


def test_release_check_holds_when_installed_init_help_drifts(tmp_path: Path) -> None:
    """Do not ship a wheel whose first-run setup command has broken help text."""
    module = _load_script_module()

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        if command_list[:2] == ["uv", "build"]:
            out_dir = Path(command_list[command_list.index("--out-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "policynim-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        help_stdout = _help_stdout_for_command(command_list)
        if "quickstart" in command_list and "local-cli" in command_list:
            stdout = _valid_quickstart_output(
                target="local-cli",
                requires_local_setup=True,
                commands=["policynim --help"],
            )
        elif "quickstart" in command_list and "local-mcp" in command_list:
            stdout = _valid_quickstart_output(
                target="local-mcp",
                requires_local_setup=True,
                local_launch_mode="installed-cli",
                commands=["policynim mcp-config --target local-stdio --client codex"],
            )
        elif "quickstart" in command_list:
            stdout = _valid_quickstart_output(
                target="hosted-mcp",
                requires_local_setup=False,
                commands=[],
            )
        elif "init" in command_list and "--help" in command_list:
            stdout = "Usage: policynim setup [OPTIONS]\n"
        elif help_stdout is not None:
            stdout = help_stdout
        elif "doctor" in command_list:
            stdout = '{"status":"ok"}'
        elif "mcp-smoke" in command_list:
            stdout = '{"status":"ok","tools":["policy_preflight","policy_search"]}'
        elif "mcp-config" in command_list:
            stdout = '{"target":"local-stdio","local_launch_mode":"installed-cli"}'
        elif "support-bundle" in command_list:
            stdout = '{"status":"ok"}'
        elif any(part.endswith("oss_readiness_check.py") for part in command_list):
            stdout = (
                _valid_launch_issue_output()
                if "launch-issue" in command_list
                else '{"decision":"local_ready_external_missing"}'
            )
        elif any(part.endswith("check_release_notes.py") for part in command_list):
            stdout = '{"decision":"passed","version":"0.1.0"}'
        elif any(
            part.endswith(("sync_github_labels.py", "sync_github_topics.py"))
            for part in command_list
        ):
            stdout = '{"dry_run":true,"plan":[]}'
        else:
            stdout = f"ok from {cwd}"
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    failed = result["checks"][-1]
    assert result["decision"] == "hold"
    assert failed["name"] == "init_help_contract"
    assert failed["status"] == "failed"
    assert "init help must include 'Usage: policynim init'" in failed["stderr_tail"]


def test_release_check_holds_when_primary_cli_help_drifts(tmp_path: Path) -> None:
    """Do not ship a wheel whose ingest or preflight help is no longer discoverable."""
    module = _load_script_module()

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        if command_list[:2] == ["uv", "build"]:
            out_dir = Path(command_list[command_list.index("--out-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "policynim-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        help_stdout = _help_stdout_for_command(command_list)
        if "quickstart" in command_list and "local-cli" in command_list:
            stdout = _valid_quickstart_output(
                target="local-cli",
                requires_local_setup=True,
                commands=["policynim --help"],
            )
        elif "quickstart" in command_list and "local-mcp" in command_list:
            stdout = _valid_quickstart_output(
                target="local-mcp",
                requires_local_setup=True,
                local_launch_mode="installed-cli",
                commands=["policynim mcp-config --target local-stdio --client codex"],
            )
        elif "quickstart" in command_list:
            stdout = _valid_quickstart_output(
                target="hosted-mcp",
                requires_local_setup=False,
                commands=[],
            )
        elif "preflight" in command_list and "--help" in command_list:
            stdout = (
                "Usage: policynim preflight [OPTIONS]\n\n"
                "Return policy guidance for a coding task.\n"
            )
        elif help_stdout is not None:
            stdout = help_stdout
        elif "doctor" in command_list:
            stdout = '{"status":"ok"}'
        elif "mcp-smoke" in command_list:
            stdout = '{"status":"ok","tools":["policy_preflight","policy_search"]}'
        elif "mcp-config" in command_list:
            stdout = '{"target":"local-stdio","local_launch_mode":"installed-cli"}'
        elif "support-bundle" in command_list:
            stdout = '{"status":"ok"}'
        elif any(part.endswith("oss_readiness_check.py") for part in command_list):
            stdout = (
                _valid_launch_issue_output()
                if "launch-issue" in command_list
                else '{"decision":"local_ready_external_missing"}'
            )
        elif any(part.endswith("check_release_notes.py") for part in command_list):
            stdout = '{"decision":"passed","version":"0.1.0"}'
        elif any(
            part.endswith(("sync_github_labels.py", "sync_github_topics.py"))
            for part in command_list
        ):
            stdout = '{"dry_run":true,"plan":[]}'
        else:
            stdout = f"ok from {cwd}"
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    failed = result["checks"][-1]
    assert result["decision"] == "hold"
    assert failed["name"] == "preflight_help_contract"
    assert failed["status"] == "failed"
    assert "preflight help must include '--task'" in failed["stderr_tail"]


def test_release_check_holds_when_installed_support_bundle_semantics_drift(
    tmp_path: Path,
) -> None:
    """Do not ship a wheel whose issue bundle points at checkout-only setup."""
    module = _load_script_module()

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        if command_list[:2] == ["uv", "build"]:
            out_dir = Path(command_list[command_list.index("--out-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "policynim-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        help_stdout = _help_stdout_for_command(command_list)
        if "quickstart" in command_list and "local-cli" in command_list:
            stdout = _valid_quickstart_output(
                target="local-cli",
                requires_local_setup=True,
                commands=["policynim --help"],
            )
        elif "quickstart" in command_list and "local-mcp" in command_list:
            stdout = _valid_quickstart_output(
                target="local-mcp",
                requires_local_setup=True,
                local_launch_mode="installed-cli",
                commands=["policynim mcp-config --target local-stdio --client codex"],
            )
        elif "quickstart" in command_list:
            stdout = _valid_quickstart_output(
                target="hosted-mcp",
                requires_local_setup=False,
                commands=[],
            )
        elif help_stdout is not None:
            stdout = help_stdout
        elif "doctor" in command_list:
            stdout = '{"status":"ok"}'
        elif "mcp-smoke" in command_list:
            stdout = '{"status":"ok","tools":["policy_preflight","policy_search"]}'
        elif "mcp-config" in command_list:
            stdout = '{"target":"local-stdio","local_launch_mode":"installed-cli"}'
        elif "support-bundle" in command_list:
            stdout = (
                '{"schema_version":"1","first_run":{"runtime_mode":"source_checkout",'
                '"default_target":"hosted-mcp","targets":{'
                '"hosted_mcp":{"target":"hosted-mcp","requires_local_setup":false,'
                '"calls_external_services":false,'
                '"commands":["uv run policynim mcp-config --target hosted-http"]},'
                '"local_cli":{"target":"local-cli","requires_local_setup":true,'
                '"calls_external_services":false,"commands":["uv run policynim --help"]},'
                '"local_mcp":{"target":"local-mcp","local_launch_mode":"source-checkout",'
                '"requires_local_setup":true,"calls_external_services":false,'
                '"commands":["uv run policynim mcp-config --repo-root /tmp/policyNIM"]}'
                "}}}"
            )
        elif any(part.endswith("oss_readiness_check.py") for part in command_list):
            stdout = (
                _valid_launch_issue_output()
                if "launch-issue" in command_list
                else '{"decision":"local_ready_external_missing"}'
            )
        elif any(part.endswith("check_release_notes.py") for part in command_list):
            stdout = '{"decision":"passed","version":"0.1.0"}'
        elif any(
            part.endswith(("sync_github_labels.py", "sync_github_topics.py"))
            for part in command_list
        ):
            stdout = '{"dry_run":true,"plan":[]}'
        else:
            stdout = f"ok from {cwd}"
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    failed = result["checks"][-1]
    assert result["decision"] == "hold"
    assert failed["name"] == "support_bundle_contract"
    assert failed["status"] == "failed"
    assert "first_run.runtime_mode expected 'standalone'" in failed["stderr_tail"]
    assert "local_mcp.local_launch_mode expected 'installed-cli'" in failed["stderr_tail"]


def test_release_check_holds_when_support_bundle_quickstart_commands_drift(
    tmp_path: Path,
) -> None:
    """Do not ship issue diagnostics whose quickstart commands point at checkout mode."""
    module = _load_script_module()

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        if command_list[:2] == ["uv", "build"]:
            out_dir = Path(command_list[command_list.index("--out-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "policynim-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        help_stdout = _help_stdout_for_command(command_list)
        if "quickstart" in command_list and "local-cli" in command_list:
            stdout = _valid_quickstart_output(
                target="local-cli",
                requires_local_setup=True,
                commands=["policynim --help"],
            )
        elif "quickstart" in command_list and "local-mcp" in command_list:
            stdout = _valid_quickstart_output(
                target="local-mcp",
                requires_local_setup=True,
                local_launch_mode="installed-cli",
                commands=["policynim mcp-config --target local-stdio --client codex"],
            )
        elif "quickstart" in command_list:
            stdout = _valid_quickstart_output(
                target="hosted-mcp",
                requires_local_setup=False,
                commands=["policynim mcp-config"],
            )
        elif help_stdout is not None:
            stdout = help_stdout
        elif "doctor" in command_list:
            stdout = '{"status":"ok"}'
        elif "mcp-smoke" in command_list:
            stdout = '{"status":"ok","tools":["policy_preflight","policy_search"]}'
        elif "mcp-config" in command_list:
            stdout = '{"target":"local-stdio","local_launch_mode":"installed-cli"}'
        elif "support-bundle" in command_list:
            stdout = (
                '{"schema_version":"1","first_run":{"runtime_mode":"standalone",'
                '"default_target":"hosted-mcp","targets":{'
                '"hosted_mcp":{"target":"hosted-mcp","quickstart_command":'
                '"uv run policynim quickstart --target hosted-mcp --format json",'
                '"requires_local_setup":false,"calls_external_services":false,'
                '"commands":["policynim mcp-config"]},'
                '"local_cli":{"target":"local-cli","quickstart_command":'
                '"policynim quickstart --target local-cli --format json",'
                '"requires_local_setup":true,"calls_external_services":false,'
                '"commands":["policynim --help"]},'
                '"local_mcp":{"target":"local-mcp","quickstart_command":'
                '"policynim quickstart --target local-mcp --client codex --format json",'
                '"local_launch_mode":"installed-cli","requires_local_setup":true,'
                '"calls_external_services":false,'
                '"commands":["policynim mcp-config --target local-stdio --client codex"]}'
                "}}}"
            )
        elif any(part.endswith("oss_readiness_check.py") for part in command_list):
            stdout = (
                _valid_launch_issue_output()
                if "launch-issue" in command_list
                else '{"decision":"local_ready_external_missing"}'
            )
        elif any(part.endswith("check_release_notes.py") for part in command_list):
            stdout = '{"decision":"passed","version":"0.1.0"}'
        elif any(
            part.endswith(("sync_github_labels.py", "sync_github_topics.py"))
            for part in command_list
        ):
            stdout = '{"dry_run":true,"plan":[]}'
        else:
            stdout = f"ok from {cwd}"
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    failed = result["checks"][-1]
    assert result["decision"] == "hold"
    assert failed["name"] == "support_bundle_contract"
    assert failed["status"] == "failed"
    assert (
        "hosted_mcp.quickstart_command must use installed CLI commands" in (failed["stderr_tail"])
    )


def test_release_check_holds_before_build_when_oss_readiness_gate_fails(
    tmp_path: Path,
) -> None:
    """Avoid building release artifacts after maintainer-trust gates fail."""
    module = _load_script_module()
    commands: list[list[str]] = []

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        commands.append(command_list)
        return subprocess.CompletedProcess(
            command_list,
            1 if any(part.endswith("oss_readiness_check.py") for part in command_list) else 0,
            stdout="",
            stderr="readiness failed",
        )

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    assert result["decision"] == "hold"
    assert [entry["name"] for entry in result["checks"]] == [
        "lockfile",
        "ruff",
        "pyright",
        "offline_pytest",
        "release_notes_check",
        "oss_readiness_json",
    ]
    assert all(command[:2] != ["uv", "build"] for command in commands)


def test_release_check_holds_before_build_when_launch_issue_renderer_fails(
    tmp_path: Path,
) -> None:
    """Avoid tagging when the paste-ready launch issue output is broken."""
    module = _load_script_module()
    commands: list[list[str]] = []

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        commands.append(command_list)
        return subprocess.CompletedProcess(
            command_list,
            1
            if any(part.endswith("oss_readiness_check.py") for part in command_list)
            and "launch-issue" in command_list
            else 0,
            stdout="",
            stderr="launch issue failed",
        )

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    assert result["decision"] == "hold"
    assert [entry["name"] for entry in result["checks"]] == [
        "lockfile",
        "ruff",
        "pyright",
        "offline_pytest",
        "release_notes_check",
        "oss_readiness_json",
        "oss_readiness_launch_issue",
    ]
    assert all(command[:2] != ["uv", "build"] for command in commands)


def test_release_check_holds_before_build_when_launch_issue_commands_are_not_strict(
    tmp_path: Path,
) -> None:
    """Do not ship launch issue commands that can silently collect failed proof."""
    module = _load_script_module()
    commands: list[list[str]] = []

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        commands.append(command_list)
        stdout = "ok"
        if any(part.endswith("oss_readiness_check.py") for part in command_list):
            if "launch-issue" in command_list:
                stdout = (
                    "# PolicyNIM Public Launch Evidence\n\n"
                    "## Missing Evidence Collection Commands\n\n"
                    "### github_artifact_attestations\n\n"
                    "uv run python scripts/collect_launch_evidence.py "
                    "--release-attestation-asset-name install.sh "
                    "--write-external-evidence-file docs/launch-evidence.json "
                    "--merge-existing --format json\n"
                )
            else:
                stdout = '{"decision":"local_ready_external_missing"}'
        elif any(part.endswith("check_release_notes.py") for part in command_list):
            stdout = '{"decision":"passed","version":"0.1.0"}'
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    failed = result["checks"][-1]
    assert result["decision"] == "hold"
    assert failed["name"] == "oss_readiness_launch_issue_contract"
    assert failed["status"] == "failed"
    assert "--validate-launch-issue-contract" in failed["command"]
    assert "--validate-json-contract" not in failed["command"]
    assert "--require-requested-probes" in failed["stderr_tail"]
    assert all(command[:2] != ["uv", "build"] for command in commands)


def test_launch_issue_contract_allows_partial_missing_evidence_commands() -> None:
    """Allow passed external proof to remove command blocks from the launch issue."""
    module = _load_script_module()
    payload = (
        "# PolicyNIM Public Launch Evidence\n\n"
        "## Missing Evidence Collection Commands\n\n"
        "### hosted_mcp_domain\n\n"
        "uv run python scripts/collect_launch_evidence.py "
        "--hosted-mcp-url 'https://<railway-domain>/mcp' "
        "--require-requested-probes "
        "--write-external-evidence-file docs/launch-evidence.json "
        "--merge-existing --format json\n"
    )

    result = module._launch_issue_contract_check(
        name="oss_readiness_launch_issue_contract",
        payload=payload,
    )

    assert result["status"] == "passed"


def test_release_check_strict_public_holds_before_build_when_external_proof_is_missing(
    tmp_path: Path,
) -> None:
    """Let maintainers run one release gate that also enforces public-launch proof."""
    module = _load_script_module()
    evidence_file = tmp_path / "launch-evidence.json"
    commands: list[list[str]] = []

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        commands.append(command_list)
        strict_public_gate = (
            any(part.endswith("oss_readiness_check.py") for part in command_list)
            and "--strict-public" in command_list
        )
        return subprocess.CompletedProcess(
            command_list,
            1 if strict_public_gate else 0,
            stdout=(
                _valid_launch_issue_output()
                if "launch-issue" in command_list
                else '{"decision":"hold_external_missing"}'
            ),
            stderr="",
        )

    result = module.run_release_check(
        repo_root=tmp_path,
        runner=fake_runner,
        strict_public=True,
        external_evidence_file=evidence_file,
    )

    assert result["decision"] == "hold"
    assert result["strict_public"] is True
    assert result["external_evidence_file"] == str(evidence_file)
    assert [entry["name"] for entry in result["checks"]] == [
        "lockfile",
        "ruff",
        "pyright",
        "offline_pytest",
        "release_notes_check",
        "oss_readiness_json",
        "oss_readiness_launch_issue",
        "oss_readiness_launch_issue_contract",
        "oss_readiness_strict_public_json",
    ]
    assert [
        sys.executable,
        "scripts/oss_readiness_check.py",
        "--external-evidence-file",
        str(evidence_file),
        "--format",
        "json",
    ] in commands
    assert [
        sys.executable,
        "scripts/oss_readiness_check.py",
        "--external-evidence-file",
        str(evidence_file),
        "--format",
        "launch-issue",
    ] in commands
    assert [
        sys.executable,
        "scripts/oss_readiness_check.py",
        "--strict-public",
        "--external-evidence-file",
        str(evidence_file),
        "--format",
        "json",
    ] in commands
    assert all(command[:2] != ["uv", "build"] for command in commands)


def test_release_check_holds_before_oss_readiness_when_release_notes_fail(
    tmp_path: Path,
) -> None:
    """Avoid claiming maintainer readiness when the versioned changelog is missing."""
    module = _load_script_module()
    commands: list[list[str]] = []

    def fake_runner(
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        command_list = [str(part) for part in command]
        commands.append(command_list)
        return subprocess.CompletedProcess(
            command_list,
            1 if any(part.endswith("check_release_notes.py") for part in command_list) else 0,
            stdout="",
            stderr="release notes failed",
        )

    result = module.run_release_check(repo_root=tmp_path, runner=fake_runner)

    assert result["decision"] == "hold"
    assert [entry["name"] for entry in result["checks"]] == [
        "lockfile",
        "ruff",
        "pyright",
        "offline_pytest",
        "release_notes_check",
    ]
    assert all(
        not any(part.endswith("oss_readiness_check.py") for part in command) for command in commands
    )
    assert all(command[:2] != ["uv", "build"] for command in commands)


def test_release_check_json_cli_is_machine_readable() -> None:
    """Keep JSON output clean enough for release automation and issue evidence."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--format",
            "json",
            "--dry-run",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["decision"] == "not_evaluated"
    assert payload["dry_run"] is True
    assert payload["required_passed"] is None
    assert payload["strict_public"] is False
    assert payload["external_evidence_file"] is None
    assert payload["checks"][0]["command"] == ["uv", "lock", "--check"]
    assert any(
        check["name"] == "installed_cli_init_help" and check["command"][-2:] == ["init", "--help"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "installed_cli_ingest_help"
        and check["command"][-2:] == ["ingest", "--help"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "installed_cli_preflight_help"
        and check["command"][-2:] == ["preflight", "--help"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "init_help_contract"
        and "--validate-init-help-contract" in check["command"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "ingest_help_contract"
        and "--validate-help-contract" in check["command"]
        and check["command"][-2:] == ["ingest", "<ingest-help-output>"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "preflight_help_contract"
        and "--validate-help-contract" in check["command"]
        and check["command"][-2:] == ["preflight", "<preflight-help-output>"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "installed_cli_quickstart_local_cli_json"
        and check["command"][-5:] == ["quickstart", "--target", "local-cli", "--format", "json"]
        for check in payload["checks"]
    )
    assert any(check["name"] == "quickstart_local_cli_contract" for check in payload["checks"])
    assert any(
        check["name"] == "installed_cli_quickstart_local_mcp_json"
        and check["command"][-5:] == ["quickstart", "--target", "local-mcp", "--format", "json"]
        for check in payload["checks"]
    )
    assert any(check["name"] == "quickstart_local_mcp_contract" for check in payload["checks"])
    assert any(check["name"] == "installed_cli_doctor_json" for check in payload["checks"])
    assert any(check["name"] == "support_bundle_contract" for check in payload["checks"])
    assert any(check["name"] == "installed_cli_mcp_smoke_json" for check in payload["checks"])
    assert any(check["name"] == "mcp_smoke_json_parse" for check in payload["checks"])
    assert any(check["name"] == "installed_cli_mcp_config_json" for check in payload["checks"])
    assert any(
        check["name"] == "mcp_config_contract"
        and "--validate-json-contract" in check["command"]
        and check["command"][-2:] == ["mcp-config-codex-local-stdio", "<mcp-config-output>"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "installed_cli_claude_mcp_config_json" for check in payload["checks"]
    )
    assert any(
        check["name"] == "claude_mcp_config_contract"
        and "--validate-json-contract" in check["command"]
        and check["command"][-2:]
        == ["mcp-config-claude-code-local-stdio", "<claude-mcp-config-output>"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "installed_cli_mcp_smoke_from_codex_config_json"
        and "--mcp-config-file" in check["command"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "mcp_smoke_from_codex_config_json_parse" for check in payload["checks"]
    )
    assert any(
        check["name"] == "installed_cli_mcp_smoke_from_claude_config_json"
        and "--mcp-config-file" in check["command"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "mcp_smoke_from_claude_config_json_parse" for check in payload["checks"]
    )
    assert any(
        check["name"] == "installed_cli_hosted_mcp_config_json" for check in payload["checks"]
    )
    assert any(
        check["name"] == "hosted_mcp_config_contract"
        and "--validate-json-contract" in check["command"]
        and check["command"][-2:] == ["mcp-config-codex-hosted-http", "<hosted-mcp-config-output>"]
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "installed_cli_claude_hosted_mcp_config_json"
        for check in payload["checks"]
    )
    assert any(
        check["name"] == "claude_hosted_mcp_config_contract"
        and "--validate-json-contract" in check["command"]
        and check["command"][-2:]
        == ["mcp-config-claude-code-hosted-http", "<claude-hosted-mcp-config-output>"]
        for check in payload["checks"]
    )
    assert any(check["name"] == "oss_readiness_json" for check in payload["checks"])
    assert any(check["name"] == "oss_readiness_launch_issue" for check in payload["checks"])
    assert any(
        check["name"] == "oss_readiness_launch_issue_contract"
        and "--validate-launch-issue-contract" in check["command"]
        for check in payload["checks"]
    )
    assert any(check["name"] == "github_label_taxonomy_dry_run_json" for check in payload["checks"])
    assert any(check["name"] == "github_topic_taxonomy_dry_run_json" for check in payload["checks"])
    parse_checks = [check for check in payload["checks"] if check["name"].endswith("_json_parse")]
    assert parse_checks
    assert all(check["command"][0] == sys.executable for check in parse_checks)


def test_release_check_json_contract_validator_accepts_payload_file(tmp_path: Path) -> None:
    """Keep dry-run remediation commands executable for captured JSON output."""
    payload_file = tmp_path / "quickstart.json"
    payload_file.write_text(
        json.dumps(
            {
                "target": "hosted-mcp",
                "requires_local_setup": False,
                "calls_external_services": False,
                **_valid_hosted_token_flow_fields(),
                "commands": [],
                "client_commands": _valid_hosted_client_commands(),
                "agent_workflows": _valid_agent_workflows(),
            }
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-json-contract",
            "quickstart-hosted-mcp",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    parsed = json.loads(result.stdout)
    assert parsed["name"] == "quickstart_contract"
    assert parsed["status"] == "passed"


def test_release_check_holds_when_hosted_quickstart_omits_mcp_token_flow(
    tmp_path: Path,
) -> None:
    """Do not ship hosted quickstart JSON that hides the MCP URL token path."""
    payload_file = tmp_path / "quickstart.json"
    payload_file.write_text(
        json.dumps(
            {
                "target": "hosted-mcp",
                "requires_local_setup": False,
                "calls_external_services": False,
                "commands": [],
                "client_commands": _valid_hosted_client_commands(),
                "agent_workflows": _valid_agent_workflows(),
            }
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-json-contract",
            "quickstart-hosted-mcp",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "failed"
    assert "hosted quickstart hosted_url must be an https /mcp URL" in (parsed["stderr_tail"])
    assert "hosted quickstart steps must explain the browser token flow" in (parsed["stderr_tail"])


def test_release_check_holds_when_hosted_quickstart_next_steps_use_checkout_command(
    tmp_path: Path,
) -> None:
    """Keep hosted quickstart no-clone, including alternate-client guidance."""
    payload = json.loads(
        _valid_quickstart_output(
            target="hosted-mcp",
            requires_local_setup=False,
            commands=[],
        )
    )
    payload["next_steps"] = [
        (
            "For Claude Code setup commands, rerun "
            "`uv run policynim quickstart --target hosted-mcp --client claude-code`."
        )
    ]
    payload_file = tmp_path / "quickstart.json"
    payload_file.write_text(json.dumps(payload))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-json-contract",
            "quickstart-hosted-mcp",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "failed"
    assert "hosted quickstart next_steps must use installed CLI commands" in (parsed["stderr_tail"])


def test_release_check_holds_when_support_bundle_omits_hosted_token_flow(
    tmp_path: Path,
) -> None:
    """Do not ship issue diagnostics that hide the hosted MCP URL token path."""
    payload = _valid_support_bundle_payload()
    first_run = payload["first_run"]
    assert isinstance(first_run, dict)
    targets = first_run["targets"]
    assert isinstance(targets, dict)
    hosted_target = targets["hosted_mcp"]
    assert isinstance(hosted_target, dict)
    del hosted_target["hosted_url"]
    del hosted_target["steps"]
    payload_file = tmp_path / "support-bundle.json"
    payload_file.write_text(json.dumps(payload))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-json-contract",
            "support-bundle",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "failed"
    assert "hosted_mcp hosted_url must be an https /mcp URL" in parsed["stderr_tail"]
    assert "hosted_mcp steps must explain the browser token flow" in parsed["stderr_tail"]


def test_release_check_holds_when_support_bundle_omits_claude_hosted_client_command(
    tmp_path: Path,
) -> None:
    """Do not ship support diagnostics that only help one hosted MCP client."""
    payload = _valid_support_bundle_payload()
    first_run = payload["first_run"]
    assert isinstance(first_run, dict)
    targets = first_run["targets"]
    assert isinstance(targets, dict)
    hosted_target = targets["hosted_mcp"]
    assert isinstance(hosted_target, dict)
    hosted_target["client_commands"] = _valid_hosted_client_commands()
    payload_file = tmp_path / "support-bundle.json"
    payload_file.write_text(json.dumps(payload))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-json-contract",
            "support-bundle",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "failed"
    assert (
        "hosted_mcp.client_commands must include a Claude Code MCP add command"
        in (parsed["stderr_tail"])
    )


def test_release_check_holds_when_quickstart_client_commands_are_missing(
    tmp_path: Path,
) -> None:
    """Do not ship hosted quickstart JSON that omits copyable client setup."""
    payload_file = tmp_path / "quickstart.json"
    payload_file.write_text(
        json.dumps(
            {
                "target": "hosted-mcp",
                "requires_local_setup": False,
                "calls_external_services": False,
                "commands": [],
                "agent_workflows": _valid_agent_workflows(),
            }
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-json-contract",
            "quickstart-hosted-mcp",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "failed"
    assert (
        "hosted quickstart client_commands must be a non-empty string list"
        in (parsed["stderr_tail"])
    )


def test_release_check_holds_when_quickstart_agent_workflows_are_missing(
    tmp_path: Path,
) -> None:
    """Do not ship quickstart JSON that omits the copyable agent workflow prompts."""
    payload_file = tmp_path / "quickstart.json"
    payload_file.write_text(
        json.dumps(
            {
                "target": "hosted-mcp",
                "requires_local_setup": False,
                "calls_external_services": False,
                "commands": [],
            }
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-json-contract",
            "quickstart-hosted-mcp",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "failed"
    assert "agent_workflows must be a non-empty list" in parsed["stderr_tail"]


def test_release_check_mcp_config_contract_validator_accepts_payload_file(
    tmp_path: Path,
) -> None:
    """Keep captured MCP config remediation commands executable."""
    payload_file = tmp_path / "mcp-config.json"
    payload_file.write_text(_valid_codex_local_mcp_config_output(), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-json-contract",
            "mcp-config-codex-local-stdio",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    parsed = json.loads(result.stdout)
    assert parsed["name"] == "mcp_config_contract"
    assert parsed["status"] == "passed"


def test_release_check_mcp_config_contract_rejects_checkout_config_for_installed_wheel(
    tmp_path: Path,
) -> None:
    """Do not ship an installed wheel whose local MCP config points at checkout mode."""
    payload_file = tmp_path / "mcp-config.json"
    payload = json.loads(_valid_codex_local_mcp_config_output())
    payload["local_launch_mode"] = "source-checkout"
    payload["repo_root"] = "/tmp/policyNIM"
    payload["codex_cli_command"] = [
        "codex",
        "mcp",
        "add",
        "policynim",
        "--env",
        "NVIDIA_API_KEY=$NVIDIA_API_KEY",
        "--",
        "uv",
        "run",
        "--directory",
        "/tmp/policyNIM",
        "policynim",
        "mcp",
        "--transport",
        "stdio",
    ]
    payload["codex_app"]["command"] = "uv"
    payload["codex_app"]["arguments"] = [
        "run",
        "--directory",
        "/tmp/policyNIM",
        "policynim",
        "mcp",
        "--transport",
        "stdio",
    ]
    payload["codex_app"]["working_directory"] = "/tmp/policyNIM"
    payload_file.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-json-contract",
            "mcp-config-codex-local-stdio",
            str(payload_file),
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "local_launch_mode expected 'installed-cli'" in result.stderr
    assert "installed local stdio config must not include repo_root" in result.stderr
    assert "installed local stdio config must launch policynim directly" in result.stderr


def test_release_check_init_help_contract_validator_accepts_payload_file(
    tmp_path: Path,
) -> None:
    """Keep dry-run remediation commands executable for captured init help output."""
    payload_file = tmp_path / "init-help.txt"
    payload_file.write_text(_valid_init_help_output(), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-init-help-contract",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    parsed = json.loads(result.stdout)
    assert parsed["name"] == "init_help_contract"
    assert parsed["status"] == "passed"


def test_release_check_command_help_contract_validator_accepts_payload_file(
    tmp_path: Path,
) -> None:
    """Keep dry-run remediation commands executable for primary CLI help output."""
    payload_file = tmp_path / "preflight-help.txt"
    payload_file.write_text(_valid_preflight_help_output(), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-help-contract",
            "preflight",
            str(payload_file),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    parsed = json.loads(result.stdout)
    assert parsed["name"] == "preflight_help_contract"
    assert parsed["status"] == "passed"


def test_release_check_launch_issue_contract_validator_rejects_non_strict_commands(
    tmp_path: Path,
) -> None:
    """Keep launch-issue dry-run remediation aligned with release-gate failures."""
    payload_file = tmp_path / "launch-issue.md"
    payload_file.write_text(
        "# PolicyNIM Public Launch Evidence\n\n"
        "## Missing Evidence Collection Commands\n\n"
        "uv run python scripts/collect_launch_evidence.py "
        "--hosted-mcp-url 'https://<railway-domain>/mcp' "
        "--write-external-evidence-file docs/launch-evidence.json "
        "--merge-existing --format json\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-launch-issue-contract",
            str(payload_file),
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "--require-requested-probes" in result.stderr


def test_release_check_strict_public_dry_run_shows_external_gate(
    tmp_path: Path,
) -> None:
    """Keep strict-public mode auditable before maintainers run the expensive smoke."""
    module = _load_script_module()
    evidence_file = tmp_path / "launch-evidence.json"

    result = module.run_release_check(
        repo_root=tmp_path,
        dry_run=True,
        strict_public=True,
        external_evidence_file=evidence_file,
    )

    assert result["decision"] == "not_evaluated"
    assert result["strict_public"] is True
    assert result["external_evidence_file"] == str(evidence_file)
    assert any(
        check["name"] == "oss_readiness_json"
        and check["command"]
        == [
            sys.executable,
            "scripts/oss_readiness_check.py",
            "--external-evidence-file",
            str(evidence_file),
            "--format",
            "json",
        ]
        for check in result["checks"]
    )
    assert any(
        check["name"] == "oss_readiness_launch_issue"
        and check["command"]
        == [
            sys.executable,
            "scripts/oss_readiness_check.py",
            "--external-evidence-file",
            str(evidence_file),
            "--format",
            "launch-issue",
        ]
        for check in result["checks"]
    )
    assert any(
        check["name"] == "oss_readiness_strict_public_json"
        and check["command"]
        == [
            sys.executable,
            "scripts/oss_readiness_check.py",
            "--strict-public",
            "--external-evidence-file",
            str(evidence_file),
            "--format",
            "json",
        ]
        for check in result["checks"]
    )
