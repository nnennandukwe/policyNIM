"""External launch evidence collector contract tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from urllib.request import Request

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "collect_launch_evidence.py"
HOSTED_CODEX_SETUP_COMMAND = (
    "codex mcp add policynim --url https://policynim.dev/mcp --bearer-token-env-var POLICYNIM_TOKEN"
)
HOSTED_CLAUDE_SETUP_COMMAND = (
    "claude mcp add --transport http policynim https://policynim.dev/mcp "
    '--header "Authorization: Bearer $POLICYNIM_TOKEN"'
)
LOCAL_CODEX_SETUP_COMMAND = (
    "codex mcp add policynim --env NVIDIA_API_KEY=$NVIDIA_API_KEY "
    "-- policynim mcp --transport stdio"
)
LOCAL_CLAUDE_SETUP_COMMAND = (
    "claude mcp add-json policynim "
    '\'{"type":"stdio","command":"policynim","args":["mcp","--transport","stdio"],'
    '"env":{"NVIDIA_API_KEY":"${NVIDIA_API_KEY}"}}\''
)


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("collect_launch_evidence", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_label_file(repo_root: Path) -> None:
    labels_file = repo_root / ".github" / "labels.yml"
    labels_file.parent.mkdir(parents=True)
    labels_file.write_text(
        "\n".join(
            [
                "- name: type/bug",
                '  color: "d73a4a"',
                "  description: Reproducible bug.",
                "",
                "- name: surface/mcp-stdio",
                '  color: "5319e7"',
                "  description: Local stdio MCP.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    topics_file = repo_root / ".github" / "topics.yml"
    topics_file.write_text("- mcp\n- verification\n", encoding="utf-8")


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


def _valid_unix_installer_stdout() -> str:
    return (
        "Installed PolicyNIM 0.1.0.\n"
        "Run `policynim quickstart` to choose a first-run path.\n"
        "Hosted MCP does not require `policynim init` or `policynim ingest`.\n"
        "For local CLI or local MCP, run `policynim init` then `policynim ingest`.\n"
        "Run `policynim doctor` to inspect first-run setup.\n"
        "Run `policynim support-bundle` before opening an issue.\n"
    )


def _valid_quickstart_output(*, target: str) -> str:
    payload: dict[str, object] = {
        "target": target,
        "requires_local_setup": target != "hosted-mcp",
        "calls_external_services": False,
        "commands": [],
        "agent_workflows": _valid_agent_workflows(),
        "next_steps": [],
    }
    if target == "hosted-mcp":
        payload["hosted_url"] = "https://example.invalid/mcp"
        payload["beta_portal_url"] = "https://example.invalid/beta"
        payload["steps"] = [
            (
                "Open https://example.invalid/mcp in a browser; it routes to "
                "https://example.invalid/beta for token creation."
            )
        ]
        payload["client_commands"] = _valid_hosted_client_commands()
    if target == "local-mcp":
        payload["local_launch_mode"] = "installed-cli"
    return json.dumps(payload)


def _valid_support_bundle_output() -> str:
    return json.dumps(
        {
            "schema_version": "1",
            "first_run": {
                "runtime_mode": "standalone",
                "default_target": "hosted-mcp",
                "targets": {
                    "hosted_mcp": {
                        "target": "hosted-mcp",
                        "requires_local_setup": False,
                        "calls_external_services": False,
                        "quickstart_command": (
                            "policynim quickstart --target hosted-mcp --format json"
                        ),
                        "hosted_url": "https://example.invalid/mcp",
                        "beta_portal_url": "https://example.invalid/beta",
                        "steps": [
                            (
                                "Open https://example.invalid/mcp in a browser; "
                                "it routes to https://example.invalid/beta for token creation."
                            )
                        ],
                        "client_commands": _valid_support_bundle_hosted_client_commands(),
                        "agent_workflows": _valid_agent_workflows(),
                    },
                    "local_cli": {
                        "target": "local-cli",
                        "requires_local_setup": True,
                        "calls_external_services": False,
                        "quickstart_command": (
                            "policynim quickstart --target local-cli --format json"
                        ),
                        "agent_workflows": _valid_agent_workflows(),
                    },
                    "local_mcp": {
                        "target": "local-mcp",
                        "requires_local_setup": True,
                        "calls_external_services": False,
                        "local_launch_mode": "installed-cli",
                        "quickstart_command": (
                            "policynim quickstart --target local-mcp --client codex --format json"
                        ),
                        "agent_workflows": _valid_agent_workflows(),
                    },
                },
            },
        }
    )


def _release_payload(*, asset_names: Sequence[str]) -> str:
    return json.dumps(
        {
            "tagName": "v0.1.0",
            "url": "https://github.com/example/policyNIM/releases/tag/v0.1.0",
            "targetCommitish": "abc123",
            "publishedAt": "2026-05-30T20:13:33Z",
            "isDraft": False,
            "isPrerelease": False,
            "assets": [
                {"name": name, "size": 1234, "digest": "sha256:abc"} for name in asset_names
            ],
        }
    )


class _FakeUrlResponse:
    def __init__(self, *, status: int, payload: dict[str, object]) -> None:
        self.status = status
        self._payload = payload

    def __enter__(self) -> _FakeUrlResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def _release_payload_asset_names(module: ModuleType, release_tag: str) -> list[str]:
    return [
        asset
        for asset in module.expected_release_assets(release_tag)
        if asset not in {"RELEASE_MANIFEST.json", "SHA256SUMS"}
    ]


def test_expected_release_assets_include_four_standalone_platforms() -> None:
    """Keep launch evidence aligned with the public standalone asset contract."""
    module = _load_script_module()

    assert module.expected_release_assets("v0.1.0") == [
        "RELEASE_MANIFEST.json",
        "SHA256SUMS",
        "install.ps1",
        "install.sh",
        "policynim-0.1.0-py3-none-any.whl",
        "policynim-0.1.0.tar.gz",
        "policynim-v0.1.0-darwin-amd64.tar.gz",
        "policynim-v0.1.0-darwin-arm64.tar.gz",
        "policynim-v0.1.0-linux-amd64.tar.gz",
        "policynim-v0.1.0-windows-amd64.zip",
    ]


def _write_release_metadata_download(
    module: ModuleType,
    command: list[str],
    *,
    checksum_override: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    if command[:3] != ["gh", "release", "download"]:
        return None
    patterns = [
        command[index + 1] for index, part in enumerate(command[:-1]) if part == "--pattern"
    ]
    if {"RELEASE_MANIFEST.json", "SHA256SUMS"} - set(patterns):
        return None

    release_tag = command[3]
    download_dir = Path(command[command.index("--dir") + 1])
    download_dir.mkdir(parents=True, exist_ok=True)
    asset_names = _release_payload_asset_names(module, release_tag)
    checksums = {asset_name: f"{index + 1:064x}" for index, asset_name in enumerate(asset_names)}
    if checksum_override:
        checksums.update(checksum_override)
    manifest = {
        "schema_version": "1",
        "release_tag": release_tag,
        "source_sha": "abc123",
        "assets": [
            {
                "name": asset_name,
                "size_bytes": 1234,
                "sha256": checksums[asset_name],
            }
            for asset_name in asset_names
        ],
    }
    (download_dir / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (download_dir / "SHA256SUMS").write_text(
        "".join(f"{checksums[asset_name]}  {asset_name}\n" for asset_name in asset_names),
        encoding="utf-8",
    )
    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _successful_runner(
    module: ModuleType,
    command: list[str],
) -> subprocess.CompletedProcess[str]:
    metadata_download = _write_release_metadata_download(module, command)
    if metadata_download is not None:
        return metadata_download
    if command[:3] == ["gh", "release", "view"]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_release_payload(asset_names=module.expected_release_assets("v0.1.0")),
            stderr="",
        )
    if command[:3] == ["gh", "label", "list"]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                [
                    {"name": "type/bug", "color": "d73a4a", "description": "Reproducible bug."},
                    {
                        "name": "surface/mcp-stdio",
                        "color": "5319e7",
                        "description": "Local stdio MCP.",
                    },
                ]
            ),
            stderr="",
        )
    if command[:3] == ["gh", "repo", "view"]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "nameWithOwner": "example/policyNIM",
                    "repositoryTopics": [{"name": "mcp"}, {"name": "verification"}],
                }
            ),
            stderr="",
        )
    raise AssertionError(command)


def _hosted_smoke_run_payload(
    *,
    conclusion: str = "success",
    head_sha: str = "abc123",
    workflow_name: str = "Hosted Beta Smoke",
) -> str:
    return json.dumps(
        {
            "conclusion": conclusion,
            "event": "workflow_dispatch",
            "headSha": head_sha,
            "jobs": [
                {
                    "conclusion": conclusion,
                    "name": "hosted-smoke",
                    "status": "completed",
                }
            ],
            "name": "Hosted Beta Smoke",
            "status": "completed",
            "url": "https://github.com/example/policyNIM/actions/runs/123456789",
            "workflowName": workflow_name,
        }
    )


def _hosted_smoke_junit_xml(*, test_names: Sequence[str]) -> str:
    testcases = "\n".join(
        f'  <testcase classname="tests.test_hosted_mcp_live" name="{name}" />'
        for name in test_names
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<testsuite name="pytest" tests="{len(test_names)}" failures="0" errors="0">\n'
        f"{testcases}\n"
        "</testsuite>\n"
    )


def _pypi_publish_run_payload(
    *,
    conclusion: str = "success",
    head_sha: str = "abc123",
    job_name: str = "publish-pypi",
    workflow_name: str = "Release",
) -> str:
    return json.dumps(
        {
            "conclusion": conclusion,
            "event": "workflow_dispatch",
            "headSha": head_sha,
            "jobs": [
                {
                    "conclusion": conclusion,
                    "name": job_name,
                    "status": "completed",
                }
            ],
            "name": "Release",
            "status": "completed",
            "url": "https://github.com/example/policyNIM/actions/runs/987654321",
            "workflowName": workflow_name,
        }
    )


def _pypi_payload_with_files(*filenames: str) -> dict[str, object]:
    return {
        "info": {"version": "0.1.0", "project_url": "https://pypi.org/project/policynim/"},
        "releases": {
            "0.1.0": [{"filename": filename} for filename in filenames],
        },
    }


def _successful_pypi_install_runner(
    module: ModuleType,
    command: list[str],
    *,
    hosted_quickstart_output: str | None = None,
    support_bundle_output: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if len(command) >= 3 and command[1:3] == ["-m", "venv"]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    if len(command) >= 5 and command[1:4] == ["-m", "pip", "install"]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    if command[-1:] == ["--help"]:
        return subprocess.CompletedProcess(command, 0, stdout="PolicyNIM\n", stderr="")
    if "quickstart" in command and command[-2:] == ["--format", "json"]:
        target = "hosted-mcp"
        if "local-cli" in command:
            target = "local-cli"
        elif "local-mcp" in command:
            target = "local-mcp"
        output = (
            hosted_quickstart_output
            if target == "hosted-mcp" and hosted_quickstart_output is not None
            else _valid_quickstart_output(target=target)
        )
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    if command[-3:] == ["doctor", "--format", "json"]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"status":"needs_configuration","checks":[]}\n',
            stderr="",
        )
    if "support-bundle" in command:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=support_bundle_output or _valid_support_bundle_output(),
            stderr="",
        )
    if "mcp-config" in command:
        client = "claude-code" if "claude-code" in command else "codex"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "schema_version": "1",
                    "client": client,
                    "target": "local-stdio",
                    "server_name": "policynim",
                    "local_launch_mode": "installed-cli",
                }
            ),
            stderr="",
        )
    return _successful_runner(module, command)


def test_collect_launch_evidence_populates_only_verifiable_records(tmp_path: Path) -> None:
    """Collect machine-verifiable external evidence without claiming manual proofs."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    required_assets = module.expected_release_assets("v0.1.0")
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        metadata_download = _write_release_metadata_download(module, command)
        if metadata_download is not None:
            return metadata_download
        if command[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=_release_payload(asset_names=required_assets), stderr=""
            )
        if command[:3] == ["gh", "label", "list"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {"name": "type/bug", "color": "d73a4a", "description": "Reproducible bug."},
                        {
                            "name": "surface/mcp-stdio",
                            "color": "5319e7",
                            "description": "Local stdio MCP.",
                        },
                    ]
                ),
                stderr="",
            )
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "nameWithOwner": "example/policyNIM",
                        "repositoryTopics": [{"name": "mcp"}, {"name": "verification"}],
                    }
                ),
                stderr="",
            )
        raise AssertionError(command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload={
            "info": {"version": "0.1.0", "project_url": "https://pypi.org/project/policynim/"},
            "releases": {
                "0.1.0": [
                    {"filename": "policynim-0.1.0-py3-none-any.whl"},
                    {"filename": "policynim-0.1.0.tar.gz"},
                ],
            },
        },
    )

    evidence = payload["evidence"]
    assert evidence["github_release_artifacts"]["summary"].startswith(
        "GitHub release v0.1.0 contains"
    )
    assert "manifest and SHA256SUMS" in evidence["github_release_artifacts"]["summary"]
    assert (
        evidence["github_release_artifacts"]["reference"]
        == "https://github.com/example/policyNIM/releases/tag/v0.1.0"
    )
    assert evidence["github_labels_applied"]["summary"] == "GitHub labels match .github/labels.yml."
    assert (
        evidence["github_topics_applied"]["summary"]
        == "GitHub repository topics match .github/topics.yml."
    )
    assert evidence["pypi_project"] == {
        "summary": "",
        "reference": "",
        "verified_by": "",
        "verified_at": "",
    }
    assert evidence["github_artifact_attestations"] == {
        "summary": "",
        "reference": "",
        "verified_by": "",
        "verified_at": "",
    }
    assert payload["probes"]["github_artifact_attestations"]["status"] == "manual_required"
    assert payload["probes"]["pypi_install_smoke"]["status"] == "manual_required"
    assert payload["probes"]["pypi_project"]["status"] == "manual_required"
    assert "Trusted publishing" in payload["probes"]["pypi_project"]["next_step"]
    assert ["gh", "release", "view", "v0.1.0", "--json", module.GITHUB_RELEASE_FIELDS] in calls
    assert ["gh", "repo", "view", "--json", "repositoryTopics,nameWithOwner"] in calls


def test_collect_launch_evidence_refuses_release_when_manifest_checksums_drift(
    tmp_path: Path,
) -> None:
    """Do not claim GitHub release evidence when manifest and SHA256SUMS disagree."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    required_assets = module.expected_release_assets("v0.1.0")
    mismatched_asset = "policynim-v0.1.0-linux-amd64.tar.gz"

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        metadata_download = _write_release_metadata_download(
            module,
            command,
            checksum_override={mismatched_asset: "f" * 64},
        )
        if metadata_download is not None:
            download_dir = Path(command[command.index("--dir") + 1])
            checksums_path = download_dir / "SHA256SUMS"
            checksums_path.write_text(
                checksums_path.read_text(encoding="utf-8").replace(
                    f"{'f' * 64}  {mismatched_asset}",
                    f"{'e' * 64}  {mismatched_asset}",
                ),
                encoding="utf-8",
            )
            return metadata_download
        if command[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_release_payload(asset_names=required_assets),
                stderr="",
            )
        if command[:3] == ["gh", "label", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "nameWithOwner": "example/policyNIM",
                        "repositoryTopics": [{"name": "mcp"}, {"name": "verification"}],
                    }
                ),
                stderr="",
            )
        raise AssertionError(command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
    )

    assert payload["evidence"]["github_release_artifacts"]["summary"] == ""
    assert payload["probes"]["github_release_artifacts"]["status"] == ("release_metadata_invalid")
    assert mismatched_asset in payload["probes"]["github_release_artifacts"]["detail"]


def test_collect_launch_evidence_marks_release_view_failure_as_release_specific(
    tmp_path: Path,
) -> None:
    """Fail closed with a release-specific status when the tag cannot be inspected."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="release not found",
            )
        if command[:3] == ["gh", "label", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "nameWithOwner": "example/policyNIM",
                        "repositoryTopics": [{"name": "mcp"}, {"name": "verification"}],
                    }
                ),
                stderr="",
            )
        raise AssertionError(command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.1",
        verified_by="maintainer@example.com",
        verified_at="2026-05-31T13:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
    )

    probe = payload["probes"]["github_release_artifacts"]
    assert probe["status"] == "release_view_failed"
    assert "release not found" in probe["detail"]
    assert payload["evidence"]["github_release_artifacts"]["summary"] == ""


def test_collect_launch_evidence_prefills_release_attestation_when_asset_verifies(
    tmp_path: Path,
) -> None:
    """Download one release asset and turn successful gh attestation verify into evidence."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    asset_name = "policynim-v0.1.0-linux-amd64.tar.gz"
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["gh", "release", "download"]:
            metadata_download = _write_release_metadata_download(module, command)
            if metadata_download is not None:
                return metadata_download
            download_dir = Path(command[command.index("--dir") + 1])
            download_dir.mkdir(parents=True, exist_ok=True)
            (download_dir / asset_name).write_text("release asset", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["gh", "attestation", "verify"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "verificationResult": {
                                "statement": {
                                    "subject": [
                                        {
                                            "name": asset_name,
                                            "digest": {"sha256": ("0" * 64)},
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                ),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
        release_attestation_asset_name=asset_name,
    )

    assert [
        "gh",
        "release",
        "download",
        "v0.1.0",
        "--pattern",
        asset_name,
        "--dir",
        payload["probes"]["github_artifact_attestations"]["download_dir"],
        "--repo",
        "example/policyNIM",
    ] in calls
    assert any(command[:3] == ["gh", "attestation", "verify"] for command in calls)
    assert payload["probes"]["github_artifact_attestations"]["status"] == "passed"
    assert payload["probes"]["github_artifact_attestations"]["subject_count"] == 1
    assert payload["probes"]["github_artifact_attestations"]["subject_names"] == [asset_name]
    assert payload["evidence"]["github_artifact_attestations"] == {
        "summary": (
            "GitHub artifact attestation verifies for "
            "policynim-v0.1.0-linux-amd64.tar.gz from release v0.1.0 with "
            "1 attested subject."
        ),
        "reference": (
            "https://github.com/example/policyNIM/releases/tag/v0.1.0"
            "#policynim-v0.1.0-linux-amd64.tar.gz"
        ),
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T21:00:00Z",
    }


def test_collect_launch_evidence_refuses_failed_release_attestation(
    tmp_path: Path,
) -> None:
    """Do not claim artifact provenance when gh attestation verify fails."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    asset_name = "policynim-v0.1.0-linux-amd64.tar.gz"

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "release", "download"]:
            metadata_download = _write_release_metadata_download(module, command)
            if metadata_download is not None:
                return metadata_download
            download_dir = Path(command[command.index("--dir") + 1])
            download_dir.mkdir(parents=True, exist_ok=True)
            (download_dir / asset_name).write_text("release asset", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["gh", "attestation", "verify"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="no attestation found",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
        release_attestation_asset_name=asset_name,
    )

    assert payload["evidence"]["github_artifact_attestations"]["summary"] == ""
    assert payload["probes"]["github_artifact_attestations"]["status"] == ("verification_failed")
    assert "no attestation found" in payload["probes"]["github_artifact_attestations"]["detail"]


def test_collect_launch_evidence_refuses_empty_attestation_subjects(
    tmp_path: Path,
) -> None:
    """Do not claim release provenance when gh returns no verified subjects."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    asset_name = "policynim-v0.1.0-linux-amd64.tar.gz"

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "release", "download"]:
            metadata_download = _write_release_metadata_download(module, command)
            if metadata_download is not None:
                return metadata_download
            download_dir = Path(command[command.index("--dir") + 1])
            download_dir.mkdir(parents=True, exist_ok=True)
            (download_dir / asset_name).write_text("release asset", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["gh", "attestation", "verify"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps([{"verificationResult": {"statement": {"subject": []}}}]),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
        release_attestation_asset_name=asset_name,
    )

    assert payload["evidence"]["github_artifact_attestations"]["summary"] == ""
    assert payload["probes"]["github_artifact_attestations"]["status"] == (
        "attestation_missing_subjects"
    )
    assert "subject" in payload["probes"]["github_artifact_attestations"]["next_step"]


def test_collect_launch_evidence_refuses_attestation_for_different_asset(
    tmp_path: Path,
) -> None:
    """Do not claim provenance when verified subjects omit the selected release asset."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    asset_name = "policynim-v0.1.0-linux-amd64.tar.gz"

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "release", "download"]:
            metadata_download = _write_release_metadata_download(module, command)
            if metadata_download is not None:
                return metadata_download
            download_dir = Path(command[command.index("--dir") + 1])
            download_dir.mkdir(parents=True, exist_ok=True)
            (download_dir / asset_name).write_text("release asset", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["gh", "attestation", "verify"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "verificationResult": {
                                "statement": {
                                    "subject": [
                                        {
                                            "name": "policynim-v0.1.0-darwin-arm64.tar.gz",
                                            "digest": {"sha256": "0" * 64},
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                ),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
        release_attestation_asset_name=asset_name,
    )

    assert payload["evidence"]["github_artifact_attestations"]["summary"] == ""
    assert payload["probes"]["github_artifact_attestations"]["status"] == (
        "attestation_subject_mismatch"
    )
    assert payload["probes"]["github_artifact_attestations"]["subject_names"] == [
        "policynim-v0.1.0-darwin-arm64.tar.gz"
    ]
    assert asset_name in payload["probes"]["github_artifact_attestations"]["next_step"]


def test_collect_launch_evidence_prefills_pypi_from_successful_publish_run(
    tmp_path: Path,
) -> None:
    """Combine public PyPI JSON and a successful trusted-publish run into evidence."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_pypi_publish_run_payload(),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_publish_run_url="https://github.com/example/policyNIM/actions/runs/987654321",
    )

    assert [
        "gh",
        "run",
        "view",
        "987654321",
        "--json",
        module.GITHUB_RUN_FIELDS,
    ] in calls
    assert payload["probes"]["pypi_project"]["status"] == "passed"
    assert payload["probes"]["pypi_project"]["file_count"] == 2
    assert payload["probes"]["pypi_project"]["filenames"] == [
        "policynim-0.1.0-py3-none-any.whl",
        "policynim-0.1.0.tar.gz",
    ]
    assert payload["evidence"]["pypi_project"] == {
        "summary": (
            "PyPI project policynim version 0.1.0 exposes 2 release files and "
            "Release run 987654321 completed publish-pypi successfully with "
            "trusted publishing for release commit abc123."
        ),
        "reference": "https://github.com/example/policyNIM/actions/runs/987654321",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T21:00:00Z",
    }


def test_collect_launch_evidence_prefills_pypi_install_smoke_from_clean_install(
    tmp_path: Path,
) -> None:
    """Prove the public PyPI package installs and first-run commands start."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return _successful_pypi_install_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_install_smoke=True,
    )

    assert payload["probes"]["pypi_install_smoke"]["status"] == "passed"
    assert payload["probes"]["pypi_install_smoke"]["commands"] == [
        "python -m venv",
        "python -m pip install --upgrade pip",
        "python -m pip install policynim==0.1.0",
        "policynim --help",
        "policynim init --help",
        "policynim ingest --help",
        "policynim preflight --help",
        "policynim quickstart --format json",
        "policynim quickstart --target local-cli --format json",
        "policynim quickstart --target local-mcp --format json",
        "policynim doctor --format json",
        "policynim support-bundle",
        "policynim mcp-config --client codex --target local-stdio --format json",
        "policynim mcp-config --client claude-code --target local-stdio --format json",
    ]
    assert payload["evidence"]["pypi_install_smoke"] == {
        "summary": (
            "Clean PyPI install smoke passed for policynim==0.1.0 with --help, "
            "primary command help, semantic first-run JSON, support-bundle hosted "
            "client_commands for Codex and Claude Code, doctor JSON, and local MCP config JSON."
        ),
        "reference": "https://pypi.org/project/policynim/0.1.0/",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T21:00:00Z",
    }
    assert any(command[-1:] == ["policynim==0.1.0"] for command in calls)


def test_collect_launch_evidence_prefills_github_release_install_smoke(
    tmp_path: Path,
) -> None:
    """Prove the published GitHub installer reaches the first-run CLI contract."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["curl", "-fsSL", "-o"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["sh", "-c"] and "install.sh" in command[2]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_valid_unix_installer_stdout(),
                stderr="",
            )
        return _successful_pypi_install_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        github_install_smoke=True,
    )

    assert payload["probes"]["github_release_install_smoke"]["status"] == "passed"
    assert payload["probes"]["github_release_install_smoke"]["commands"] == [
        "download install.sh",
        "install.sh",
        "policynim --help",
        "policynim init --help",
        "policynim ingest --help",
        "policynim preflight --help",
        "policynim quickstart --format json",
        "policynim quickstart --target local-cli --format json",
        "policynim quickstart --target local-mcp --format json",
        "policynim doctor --format json",
        "policynim support-bundle",
        "policynim mcp-config --client codex --target local-stdio --format json",
        "policynim mcp-config --client claude-code --target local-stdio --format json",
    ]
    assert payload["evidence"]["github_release_install_smoke"] == {
        "summary": (
            "Clean GitHub release installer smoke passed for v0.1.0 with "
            "install.sh guidance, --help, primary command help, semantic "
            "first-run JSON, support-bundle hosted client_commands for Codex and "
            "Claude Code, doctor JSON, and local MCP config JSON."
        ),
        "reference": "https://github.com/nnennandukwe/policyNIM/releases/tag/v0.1.0",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T21:00:00Z",
    }
    assert any(
        command[:3] == ["curl", "-fsSL", "-o"]
        and command[-1]
        == "https://github.com/nnennandukwe/policyNIM/releases/download/v0.1.0/install.sh"
        for command in calls
    )


def test_collect_launch_evidence_refuses_github_install_smoke_without_setup_guidance(
    tmp_path: Path,
) -> None:
    """Do not claim GitHub installer proof when post-install guidance is stale."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["curl", "-fsSL", "-o"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["sh", "-c"] and "install.sh" in command[2]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "Installed PolicyNIM 0.1.0.\n"
                    "Run `policynim init` to configure your local NVIDIA API key.\n"
                ),
                stderr="",
            )
        return _successful_pypi_install_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        github_install_smoke=True,
    )

    assert payload["evidence"]["github_release_install_smoke"]["summary"] == ""
    probe = payload["probes"]["github_release_install_smoke"]
    assert probe["status"] == "invalid_installer_guidance"
    assert probe["command"] == "install.sh"
    assert "Hosted MCP does not require `policynim init` or `policynim ingest`" in (probe["detail"])
    assert "For local CLI or local MCP" in probe["detail"]


def test_collect_launch_evidence_refuses_github_release_install_smoke_command_failure(
    tmp_path: Path,
) -> None:
    """Do not claim GitHub installer proof when the installed CLI lacks quickstart."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["curl", "-fsSL", "-o"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["sh", "-c"] and "install.sh" in command[2]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_valid_unix_installer_stdout(),
                stderr="",
            )
        if command[-3:] == ["quickstart", "--format", "json"]:
            return subprocess.CompletedProcess(
                command,
                2,
                stdout="",
                stderr="No such command 'quickstart'.",
            )
        return _successful_pypi_install_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        github_install_smoke=True,
    )

    assert payload["evidence"]["github_release_install_smoke"]["summary"] == ""
    probe = payload["probes"]["github_release_install_smoke"]
    assert probe["status"] == "missing_first_run_command"
    assert probe["command"] == "policynim quickstart --format json"
    assert "No such command 'quickstart'." in probe["detail"]
    assert "Publish a new GitHub release built from the current CLI" in probe["next_step"]


def test_collect_launch_evidence_refuses_pypi_smoke_without_hosted_client_commands(
    tmp_path: Path,
) -> None:
    """Do not claim public install proof when hosted quickstart lacks MCP setup."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    hosted_quickstart = json.dumps(
        {
            "target": "hosted-mcp",
            "requires_local_setup": False,
            "calls_external_services": False,
            "commands": [],
            "agent_workflows": _valid_agent_workflows(),
        }
    )

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _successful_pypi_install_runner(
            module,
            command,
            hosted_quickstart_output=hosted_quickstart,
        )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_install_smoke=True,
    )

    assert payload["evidence"]["pypi_install_smoke"]["summary"] == ""
    probe = payload["probes"]["pypi_install_smoke"]
    assert probe["status"] == "invalid_first_run_contract"
    assert probe["command"] == "policynim quickstart --format json"
    assert "client_commands must be a non-empty string list" in probe["detail"]
    assert (
        "Publish a new PyPI release built from the current first-run contract"
        in (probe["next_step"])
    )


def test_collect_launch_evidence_refuses_pypi_smoke_without_mcp_token_flow(
    tmp_path: Path,
) -> None:
    """Do not claim public install proof when quickstart hides token creation."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    hosted_quickstart = json.dumps(
        {
            "target": "hosted-mcp",
            "requires_local_setup": False,
            "calls_external_services": False,
            "commands": [],
            "client_commands": _valid_hosted_client_commands(),
            "agent_workflows": _valid_agent_workflows(),
        }
    )

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _successful_pypi_install_runner(
            module,
            command,
            hosted_quickstart_output=hosted_quickstart,
        )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_install_smoke=True,
    )

    assert payload["evidence"]["pypi_install_smoke"]["summary"] == ""
    probe = payload["probes"]["pypi_install_smoke"]
    assert probe["status"] == "invalid_first_run_contract"
    assert probe["command"] == "policynim quickstart --format json"
    assert "hosted quickstart hosted_url must be an https /mcp URL" in probe["detail"]
    assert "hosted quickstart steps must explain the browser token flow" in (probe["detail"])


def test_collect_launch_evidence_refuses_pypi_smoke_without_support_client_commands(
    tmp_path: Path,
) -> None:
    """Do not claim public install proof when support-bundle omits hosted setup."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    support_bundle = json.loads(_valid_support_bundle_output())
    del support_bundle["first_run"]["targets"]["hosted_mcp"]["client_commands"]

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _successful_pypi_install_runner(
            module,
            command,
            support_bundle_output=json.dumps(support_bundle),
        )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_install_smoke=True,
    )

    assert payload["evidence"]["pypi_install_smoke"]["summary"] == ""
    probe = payload["probes"]["pypi_install_smoke"]
    assert probe["status"] == "invalid_first_run_contract"
    assert probe["command"] == "policynim support-bundle"
    assert "first_run.targets.hosted_mcp.client_commands" in probe["detail"]


def test_collect_launch_evidence_refuses_pypi_smoke_without_support_claude_command(
    tmp_path: Path,
) -> None:
    """Do not claim install proof when support diagnostics omit a hosted MCP client."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    support_bundle = json.loads(_valid_support_bundle_output())
    support_bundle["first_run"]["targets"]["hosted_mcp"]["client_commands"] = (
        _valid_hosted_client_commands()
    )

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _successful_pypi_install_runner(
            module,
            command,
            support_bundle_output=json.dumps(support_bundle),
        )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_install_smoke=True,
    )

    assert payload["evidence"]["pypi_install_smoke"]["summary"] == ""
    probe = payload["probes"]["pypi_install_smoke"]
    assert probe["status"] == "invalid_first_run_contract"
    assert probe["command"] == "policynim support-bundle"
    assert (
        "first_run.targets.hosted_mcp.client_commands must include a Claude Code MCP add command"
        in probe["detail"]
    )


def test_collect_launch_evidence_refuses_pypi_smoke_without_support_token_flow(
    tmp_path: Path,
) -> None:
    """Do not claim public install proof when support-bundle hides token setup."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    support_bundle = json.loads(_valid_support_bundle_output())
    hosted_mcp = support_bundle["first_run"]["targets"]["hosted_mcp"]
    del hosted_mcp["hosted_url"]
    del hosted_mcp["steps"]

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _successful_pypi_install_runner(
            module,
            command,
            support_bundle_output=json.dumps(support_bundle),
        )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_install_smoke=True,
    )

    assert payload["evidence"]["pypi_install_smoke"]["summary"] == ""
    probe = payload["probes"]["pypi_install_smoke"]
    assert probe["status"] == "invalid_first_run_contract"
    assert probe["command"] == "policynim support-bundle"
    assert "first_run.targets.hosted_mcp hosted_url must be an https /mcp URL" in (probe["detail"])
    assert (
        "first_run.targets.hosted_mcp steps must explain the browser token flow"
        in (probe["detail"])
    )


def test_collect_launch_evidence_refuses_pypi_install_smoke_command_failure(
    tmp_path: Path,
) -> None:
    """Do not claim public install proof when first-run smoke commands fail."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[-3:] == ["quickstart", "--format", "json"]:
            return subprocess.CompletedProcess(
                command,
                2,
                stdout="",
                stderr="No such command 'quickstart'.",
            )
        if len(command) >= 3 and command[1:3] == ["-m", "venv"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if len(command) >= 5 and command[1:4] == ["-m", "pip", "install"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[-1:] == ["--help"]:
            return subprocess.CompletedProcess(command, 0, stdout="PolicyNIM\n", stderr="")
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_install_smoke=True,
    )

    assert payload["evidence"]["pypi_install_smoke"]["summary"] == ""
    assert payload["probes"]["pypi_install_smoke"]["status"] == "missing_first_run_command"
    assert payload["probes"]["pypi_install_smoke"]["command"] == (
        "policynim quickstart --format json"
    )
    assert "No such command 'quickstart'." in payload["probes"]["pypi_install_smoke"]["detail"]
    assert (
        "Publish a new PyPI release built from the current CLI"
        in (payload["probes"]["pypi_install_smoke"]["next_step"])
    )


def test_collect_launch_evidence_refuses_pypi_run_from_different_release_sha(
    tmp_path: Path,
) -> None:
    """Do not accept trusted-publishing evidence from an unrelated release run."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_pypi_publish_run_payload(head_sha="different-sha"),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_publish_run_url="https://github.com/example/policyNIM/actions/runs/987654321",
    )

    assert payload["evidence"]["pypi_project"]["summary"] == ""
    assert payload["probes"]["pypi_project"]["status"] == "release_sha_mismatch"
    assert payload["probes"]["pypi_project"]["expected_head_sha"] == "abc123"
    assert payload["probes"]["pypi_project"]["actual_head_sha"] == "different-sha"
    assert "same commit" in payload["probes"]["pypi_project"]["next_step"]


def test_collect_launch_evidence_refuses_pypi_without_release_files(
    tmp_path: Path,
) -> None:
    """Do not claim install-channel evidence until PyPI lists the wheel and sdist."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_pypi_publish_run_payload(),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files("policynim-0.1.0-py3-none-any.whl"),
        pypi_publish_run_url="https://github.com/example/policyNIM/actions/runs/987654321",
    )

    assert [
        "gh",
        "run",
        "view",
        "987654321",
        "--json",
        module.GITHUB_RUN_FIELDS,
    ] not in calls
    assert payload["evidence"]["pypi_project"]["summary"] == ""
    assert payload["probes"]["pypi_project"]["status"] == "missing_distribution_files"
    assert payload["probes"]["pypi_project"]["missing_files"] == ["policynim-0.1.0.tar.gz"]
    assert "wheel and sdist" in payload["probes"]["pypi_project"]["next_step"]


def test_collect_launch_evidence_refuses_pypi_run_without_publish_job(
    tmp_path: Path,
) -> None:
    """Do not claim PyPI trusted-publishing evidence from a run without publish-pypi."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_pypi_publish_run_payload(job_name="verify"),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_publish_run_url="https://github.com/example/policyNIM/actions/runs/987654321",
    )

    assert payload["evidence"]["pypi_project"]["summary"] == ""
    assert payload["probes"]["pypi_project"]["status"] == "job_not_successful"
    assert payload["probes"]["pypi_project"]["available_jobs"] == [
        {"conclusion": "success", "name": "verify", "status": "completed"}
    ]
    assert "publish-pypi" in payload["probes"]["pypi_project"]["next_step"]


def test_collect_launch_evidence_reports_skipped_pypi_publish_job(
    tmp_path: Path,
) -> None:
    """Surface skipped trusted-publishing jobs without accepting PyPI evidence."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "conclusion": "success",
                        "event": "workflow_dispatch",
                        "headSha": "abc123",
                        "jobs": [
                            {
                                "conclusion": "skipped",
                                "name": "publish-pypi",
                                "status": "completed",
                            }
                        ],
                        "name": "Release",
                        "status": "completed",
                        "url": "https://github.com/example/policyNIM/actions/runs/987654321",
                        "workflowName": "Release",
                    }
                ),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=_pypi_payload_with_files(
            "policynim-0.1.0-py3-none-any.whl",
            "policynim-0.1.0.tar.gz",
        ),
        pypi_publish_run_url="https://github.com/example/policyNIM/actions/runs/987654321",
    )

    probe = payload["probes"]["pypi_project"]
    assert payload["evidence"]["pypi_project"]["summary"] == ""
    assert probe["status"] == "job_not_successful"
    assert probe["job_name"] == "publish-pypi"
    assert probe["job_status"] == "completed"
    assert probe["job_conclusion"] == "skipped"


def test_launch_evidence_text_output_includes_pypi_job_details(
    tmp_path: Path,
) -> None:
    """Keep human-readable launch evidence output actionable for skipped jobs."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    payload = {
        "release_tag": "v0.1.0",
        "probes": {
            "pypi_project": {
                "status": "job_not_successful",
                "next_step": "Use a Release workflow run where the publish-pypi job passed.",
                "job_name": "publish-pypi",
                "job_status": "completed",
                "job_conclusion": "skipped",
            }
        },
    }

    output = module._render_text(payload)

    assert "- pypi_project: job_not_successful" in output
    assert "  job: publish-pypi completed/skipped" in output


def test_launch_evidence_text_output_includes_probe_failure_detail(
    tmp_path: Path,
) -> None:
    """Expose actionable probe failure details in human-readable output."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    payload = {
        "release_tag": "v0.1.0",
        "probes": {
            "github_artifact_attestations": {
                "status": "verification_failed",
                "detail": "Error: HTTP 404: Not Found",
                "next_step": "Publish release attestations from the Release workflow.",
            }
        },
    }

    output = module._render_text(payload)

    assert "- github_artifact_attestations: verification_failed" in output
    assert "  detail: Error: HTTP 404: Not Found" in output
    assert "  next: Publish release attestations from the Release workflow." in output


def test_collect_launch_evidence_prefills_hosted_domain_when_url_is_ready(
    tmp_path: Path,
) -> None:
    """Turn an opt-in hosted URL probe into reviewable launch evidence."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_urlopen(request: Request | str, *, timeout: float) -> _FakeUrlResponse:
        assert timeout == 15
        url = request.full_url if isinstance(request, Request) else request
        headers = dict(request.header_items()) if isinstance(request, Request) else {}
        calls.append((url, headers))
        if url == "https://policy.example/healthz":
            return _FakeUrlResponse(
                status=200,
                payload={
                    "ready": True,
                    "status": "ok",
                    "row_count": 42,
                    "mcp_url": "https://policy.example/mcp",
                },
            )
        if url == "https://policy.example/mcp":
            assert headers["Authorization"] == "Bearer invalid-token"
            assert headers["Accept"] == "text/event-stream"
            return _FakeUrlResponse(status=401, payload={"error": "Unauthorized."})
        raise AssertionError(url)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=lambda command: _successful_runner(module, command),
        pypi_payload=None,
        hosted_mcp_url="https://policy.example/mcp",
        urlopen=fake_urlopen,
    )

    assert payload["probes"]["hosted_mcp_domain"]["status"] == "passed"
    assert payload["evidence"]["hosted_mcp_domain"] == {
        "summary": (
            "Hosted MCP domain https://policy.example reports ready /healthz "
            "with row_count 42 and rejects invalid bearer tokens on /mcp."
        ),
        "reference": "https://policy.example/healthz",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T21:00:00Z",
    }
    assert calls == [
        ("https://policy.example/healthz", {}),
        (
            "https://policy.example/mcp",
            {"Accept": "text/event-stream", "Authorization": "Bearer invalid-token"},
        ),
    ]


def test_collect_launch_evidence_refuses_hosted_domain_without_bearer_gate(
    tmp_path: Path,
) -> None:
    """Do not claim hosted MCP evidence when invalid bearer tokens are accepted."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_urlopen(request: Request | str, *, timeout: float) -> _FakeUrlResponse:
        url = request.full_url if isinstance(request, Request) else request
        if url == "https://policy.example/healthz":
            return _FakeUrlResponse(
                status=200,
                payload={
                    "ready": True,
                    "status": "ok",
                    "row_count": 42,
                    "mcp_url": "https://policy.example/mcp",
                },
            )
        if url == "https://policy.example/mcp":
            return _FakeUrlResponse(status=200, payload={"accepted": True})
        raise AssertionError(url)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=lambda command: _successful_runner(module, command),
        pypi_payload=None,
        hosted_mcp_url="https://policy.example/mcp",
        urlopen=fake_urlopen,
    )

    assert payload["evidence"]["hosted_mcp_domain"]["summary"] == ""
    assert payload["probes"]["hosted_mcp_domain"]["status"] == "mcp_auth_not_enforced"
    assert "401" in payload["probes"]["hosted_mcp_domain"]["next_step"]


def test_collect_launch_evidence_prefills_hosted_smoke_from_successful_run(
    tmp_path: Path,
) -> None:
    """Turn a supplied Hosted Beta Smoke run URL into launch evidence."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_hosted_smoke_run_payload(),
                stderr="",
            )
        if command[:3] == ["gh", "run", "download"]:
            download_dir = Path(command[command.index("--dir") + 1])
            download_dir.mkdir(parents=True, exist_ok=True)
            (download_dir / "policynim-hosted-smoke-junit.xml").write_text(
                _hosted_smoke_junit_xml(test_names=module.expected_hosted_smoke_tests()),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
        hosted_smoke_run_url="https://github.com/example/policyNIM/actions/runs/123456789",
    )

    assert [
        "gh",
        "run",
        "view",
        "123456789",
        "--json",
        module.GITHUB_RUN_FIELDS,
    ] in calls
    assert [
        "gh",
        "run",
        "download",
        "123456789",
        "--name",
        "hosted-smoke-evidence",
        "--dir",
        "<dynamic-dir>",
    ][:-1] in [call[:-1] for call in calls]
    assert payload["probes"]["hosted_beta_live_smoke"]["status"] == "passed"
    assert payload["probes"]["hosted_beta_live_smoke"]["artifact"] == "hosted-smoke-evidence"
    assert payload["probes"]["hosted_beta_live_smoke"]["junit_file"] == (
        "policynim-hosted-smoke-junit.xml"
    )
    assert payload["evidence"]["hosted_beta_live_smoke"] == {
        "summary": (
            "Hosted Beta Smoke run 123456789 completed successfully with "
            "hosted-smoke-evidence/policynim-hosted-smoke-junit.xml covering "
            "5 live MCP checks from release commit abc123."
        ),
        "reference": "https://github.com/example/policyNIM/actions/runs/123456789",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T21:00:00Z",
    }


def test_collect_launch_evidence_refuses_hosted_smoke_run_from_different_release_sha(
    tmp_path: Path,
) -> None:
    """Do not claim hosted smoke proof from a stale or unrelated workflow run."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_hosted_smoke_run_payload(head_sha="different-sha"),
                stderr="",
            )
        if command[:3] == ["gh", "run", "download"]:
            download_dir = Path(command[command.index("--dir") + 1])
            download_dir.mkdir(parents=True, exist_ok=True)
            (download_dir / "policynim-hosted-smoke-junit.xml").write_text(
                _hosted_smoke_junit_xml(test_names=module.expected_hosted_smoke_tests()),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
        hosted_smoke_run_url="https://github.com/example/policyNIM/actions/runs/123456789",
    )

    assert payload["evidence"]["hosted_beta_live_smoke"]["summary"] == ""
    assert payload["probes"]["hosted_beta_live_smoke"]["status"] == "release_sha_mismatch"
    assert payload["probes"]["hosted_beta_live_smoke"]["expected_head_sha"] == "abc123"
    assert payload["probes"]["hosted_beta_live_smoke"]["actual_head_sha"] == "different-sha"
    assert "same commit" in payload["probes"]["hosted_beta_live_smoke"]["next_step"]


def test_collect_launch_evidence_refuses_failed_hosted_smoke_run(tmp_path: Path) -> None:
    """Do not turn a failed Hosted Beta Smoke run URL into launch evidence."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_hosted_smoke_run_payload(conclusion="failure"),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
        hosted_smoke_run_url="https://github.com/example/policyNIM/actions/runs/123456789",
    )

    assert payload["evidence"]["hosted_beta_live_smoke"]["summary"] == ""
    assert payload["probes"]["hosted_beta_live_smoke"]["status"] == "run_not_successful"
    assert "success" in payload["probes"]["hosted_beta_live_smoke"]["next_step"]


def test_collect_launch_evidence_refuses_hosted_smoke_run_without_expected_junit(
    tmp_path: Path,
) -> None:
    """Do not fill hosted smoke evidence when the retained artifact is incomplete."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    missing_test = "test_hosted_policy_preflight_live"

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_hosted_smoke_run_payload(),
                stderr="",
            )
        if command[:3] == ["gh", "run", "download"]:
            download_dir = Path(command[command.index("--dir") + 1])
            download_dir.mkdir(parents=True, exist_ok=True)
            retained_tests = [
                name for name in module.expected_hosted_smoke_tests() if name != missing_test
            ]
            (download_dir / "policynim-hosted-smoke-junit.xml").write_text(
                _hosted_smoke_junit_xml(test_names=retained_tests),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
        hosted_smoke_run_url="https://github.com/example/policyNIM/actions/runs/123456789",
    )

    assert payload["evidence"]["hosted_beta_live_smoke"]["summary"] == ""
    assert payload["probes"]["hosted_beta_live_smoke"]["status"] == "junit_missing_tests"
    assert payload["probes"]["hosted_beta_live_smoke"]["missing_tests"] == [missing_test]


def test_collect_launch_evidence_prefills_real_client_session_from_reviewed_file(
    tmp_path: Path,
) -> None:
    """Turn a reviewed Codex or Claude MCP session record into launch evidence."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    session_file = tmp_path / "codex-mcp-session.json"
    session_file.write_text(
        json.dumps(
            {
                "client": "codex",
                "transport": "hosted-http",
                "server_name": "policynim",
                "setup_command": HOSTED_CODEX_SETUP_COMMAND,
                "tools": ["policy_preflight", "policy_search"],
                "called_tool": "policy_preflight",
                "reference": "launch-notes/codex-mcp-session.md",
                "secrets_included": False,
            }
        ),
        encoding="utf-8",
    )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=lambda command: _successful_runner(module, command),
        pypi_payload=None,
        mcp_client_evidence_file=session_file,
    )

    assert payload["probes"]["real_mcp_client_session"]["status"] == "passed"
    assert payload["evidence"]["real_mcp_client_session"] == {
        "summary": (
            "Codex loaded the policynim MCP server over hosted-http using a "
            "reviewed setup command, listed "
            "policy_preflight and policy_search, and called policy_preflight."
        ),
        "reference": "launch-notes/codex-mcp-session.md",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T21:00:00Z",
    }


def test_collect_launch_evidence_refuses_client_session_with_secrets(
    tmp_path: Path,
) -> None:
    """Do not accept client-session evidence that says secrets were included."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    session_file = tmp_path / "codex-mcp-session.json"
    session_file.write_text(
        json.dumps(
            {
                "client": "codex",
                "transport": "hosted-http",
                "server_name": "policynim",
                "setup_command": HOSTED_CODEX_SETUP_COMMAND,
                "tools": ["policy_preflight", "policy_search"],
                "called_tool": "policy_preflight",
                "reference": "launch-notes/codex-mcp-session.md",
                "secrets_included": True,
            }
        ),
        encoding="utf-8",
    )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=lambda command: _successful_runner(module, command),
        pypi_payload=None,
        mcp_client_evidence_file=session_file,
    )

    assert payload["evidence"]["real_mcp_client_session"]["summary"] == ""
    assert payload["probes"]["real_mcp_client_session"]["status"] == "secrets_included"
    assert "redacted" in payload["probes"]["real_mcp_client_session"]["next_step"]


def test_collect_launch_evidence_refuses_client_session_placeholder_reference(
    tmp_path: Path,
) -> None:
    """Do not accept template-like client-session references as launch proof."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    session_file = tmp_path / "codex-mcp-session.json"
    session_file.write_text(
        json.dumps(
            {
                "client": "codex",
                "transport": "hosted-http",
                "server_name": "policynim",
                "setup_command": HOSTED_CODEX_SETUP_COMMAND,
                "tools": ["policy_preflight", "policy_search"],
                "called_tool": "policy_preflight",
                "reference": "https://github.com/example/policyNIM/issues/123",
                "secrets_included": False,
            }
        ),
        encoding="utf-8",
    )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=lambda command: _successful_runner(module, command),
        pypi_payload=None,
        mcp_client_evidence_file=session_file,
    )

    assert payload["evidence"]["real_mcp_client_session"]["summary"] == ""
    assert payload["probes"]["real_mcp_client_session"]["status"] == "placeholder_reference"
    assert (
        "real, sanitized reference" in (payload["probes"]["real_mcp_client_session"]["next_step"])
    )


def test_collect_launch_evidence_refuses_checked_in_client_session_example(
    tmp_path: Path,
) -> None:
    """Do not turn the checked-in client-session example into real launch proof."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    example_file = Path("docs/mcp-client-evidence.example.json")

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=lambda command: _successful_runner(module, command),
        pypi_payload=None,
        mcp_client_evidence_file=example_file,
    )

    assert payload["evidence"]["real_mcp_client_session"]["summary"] == ""
    assert payload["probes"]["real_mcp_client_session"]["status"] == "missing_reference"
    assert (
        "non-empty sanitized reference"
        in (payload["probes"]["real_mcp_client_session"]["next_step"])
    )


def test_collect_launch_evidence_missing_client_session_guides_setup_proof(
    tmp_path: Path,
) -> None:
    """Keep missing real-client proof guidance aligned with required setup evidence."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=lambda command: _successful_runner(module, command),
        pypi_payload=None,
    )

    next_step = payload["probes"]["real_mcp_client_session"]["next_step"]

    assert payload["probes"]["real_mcp_client_session"]["status"] == "manual_required"
    assert "transcript or screenshot" in next_step
    assert "secret-safe setup command" in next_step
    assert "policy_preflight" in next_step


def test_collect_launch_evidence_refuses_client_session_without_setup_command(
    tmp_path: Path,
) -> None:
    """Do not accept client-session proof that omits how the MCP server was added."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    session_file = tmp_path / "codex-mcp-session.json"
    session_file.write_text(
        json.dumps(
            {
                "client": "codex",
                "transport": "hosted-http",
                "server_name": "policynim",
                "tools": ["policy_preflight", "policy_search"],
                "called_tool": "policy_preflight",
                "reference": "launch-notes/codex-mcp-session.md",
                "secrets_included": False,
            }
        ),
        encoding="utf-8",
    )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=lambda command: _successful_runner(module, command),
        pypi_payload=None,
        mcp_client_evidence_file=session_file,
    )

    assert payload["evidence"]["real_mcp_client_session"]["summary"] == ""
    assert payload["probes"]["real_mcp_client_session"]["status"] == "missing_setup_command"
    assert "setup command" in payload["probes"]["real_mcp_client_session"]["next_step"]


def test_collect_launch_evidence_refuses_client_session_with_placeholder_setup_command(
    tmp_path: Path,
) -> None:
    """Do not accept client proof whose setup command still contains placeholders."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    session_file = tmp_path / "codex-mcp-session.json"
    session_file.write_text(
        json.dumps(
            {
                "client": "codex",
                "transport": "hosted-http",
                "server_name": "policynim",
                "setup_command": (
                    "codex mcp add policynim --url https://<railway-domain>/mcp "
                    "--bearer-token-env-var POLICYNIM_TOKEN"
                ),
                "tools": ["policy_preflight", "policy_search"],
                "called_tool": "policy_preflight",
                "reference": "launch-notes/codex-mcp-session.md",
                "secrets_included": False,
            }
        ),
        encoding="utf-8",
    )

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=lambda command: _successful_runner(module, command),
        pypi_payload=None,
        mcp_client_evidence_file=session_file,
    )

    assert payload["evidence"]["real_mcp_client_session"]["summary"] == ""
    assert payload["probes"]["real_mcp_client_session"]["status"] == ("placeholder_setup_command")
    assert "real setup command" in (payload["probes"]["real_mcp_client_session"]["next_step"])


def test_validate_mcp_client_evidence_payload_checks_setup_command_shape() -> None:
    """Require the setup command to match the reviewed client and transport."""
    module = _load_script_module()

    for client, transport, setup_command in (
        ("codex", "hosted-http", HOSTED_CODEX_SETUP_COMMAND),
        ("claude-code", "hosted-http", HOSTED_CLAUDE_SETUP_COMMAND),
        ("codex", "local-stdio", LOCAL_CODEX_SETUP_COMMAND),
        ("claude-code", "local-stdio", LOCAL_CLAUDE_SETUP_COMMAND),
    ):
        assert (
            module._validate_mcp_client_evidence_payload(
                {
                    "client": client,
                    "transport": transport,
                    "server_name": "policynim",
                    "setup_command": setup_command,
                    "tools": ["policy_preflight", "policy_search"],
                    "called_tool": "policy_preflight",
                    "reference": "launch-notes/mcp-session.md",
                    "secrets_included": False,
                }
            )
            is None
        )

    wrong_setup = {
        "client": "codex",
        "transport": "hosted-http",
        "server_name": "policynim",
        "setup_command": "claude mcp add --transport http policynim https://policynim.dev/mcp",
        "tools": ["policy_preflight", "policy_search"],
        "called_tool": "policy_preflight",
        "reference": "launch-notes/mcp-session.md",
        "secrets_included": False,
    }
    error = module._validate_mcp_client_evidence_payload(wrong_setup)

    assert error is not None
    assert error["status"] == "setup_command_mismatch"


def test_mcp_client_evidence_template_matches_checked_in_example() -> None:
    """Keep generated client-session templates aligned with the safe checked-in example."""
    module = _load_script_module()
    checked_in = json.loads(
        (REPO_ROOT / "docs" / "mcp-client-evidence.example.json").read_text(encoding="utf-8")
    )

    assert checked_in == module.mcp_client_evidence_template(
        client="codex",
        transport="hosted-http",
    )
    assert checked_in["reference"] == ""
    assert checked_in["setup_command"] == ""


def test_write_mcp_client_evidence_template_creates_safe_placeholder(
    tmp_path: Path,
) -> None:
    """Let maintainers generate a safe real-client evidence record to fill later."""
    module = _load_script_module()
    target = tmp_path / "launch-notes" / "claude-mcp-session.json"

    result = module.write_mcp_client_evidence_template(
        target,
        client="claude-code",
        transport="local-stdio",
        force=False,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert payload == {
        "client": "claude-code",
        "transport": "local-stdio",
        "server_name": "policynim",
        "setup_command": "",
        "tools": ["policy_preflight", "policy_search"],
        "called_tool": "policy_preflight",
        "reference": "",
        "secrets_included": False,
    }


def test_write_mcp_client_evidence_template_refuses_overwrite_without_force(
    tmp_path: Path,
) -> None:
    """Protect reviewed client-session evidence from template rewrites."""
    module = _load_script_module()
    target = tmp_path / "codex-mcp-session.json"
    target.write_text("existing evidence", encoding="utf-8")

    result = module.write_mcp_client_evidence_template(
        target,
        client="codex",
        transport="hosted-http",
        force=False,
    )

    assert result == 1
    assert target.read_text(encoding="utf-8") == "existing evidence"


def test_write_mcp_client_evidence_record_creates_valid_reviewed_record(
    tmp_path: Path,
) -> None:
    """Let maintainers turn a reviewed session reference into collector-ready JSON."""
    module = _load_script_module()
    target = tmp_path / "launch-notes" / "codex-mcp-session.json"

    result = module.write_mcp_client_evidence_record(
        target,
        client="codex",
        transport="hosted-http",
        setup_command=HOSTED_CODEX_SETUP_COMMAND,
        reference="launch-notes/codex-mcp-session.md",
        force=False,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert payload == {
        "client": "codex",
        "transport": "hosted-http",
        "server_name": "policynim",
        "setup_command": HOSTED_CODEX_SETUP_COMMAND,
        "tools": ["policy_preflight", "policy_search"],
        "called_tool": "policy_preflight",
        "reference": "launch-notes/codex-mcp-session.md",
        "secrets_included": False,
    }
    assert module._validate_mcp_client_evidence_payload(payload) is None


def test_write_mcp_client_evidence_record_derives_hosted_setup_command(
    tmp_path: Path,
) -> None:
    """Let maintainers generate hosted client evidence from the verified /mcp URL."""
    module = _load_script_module()
    target = tmp_path / "launch-notes" / "codex-mcp-session.json"

    result = module.write_mcp_client_evidence_record(
        target,
        client="codex",
        transport="hosted-http",
        setup_command="",
        hosted_mcp_url="https://policynim.dev/mcp",
        reference="launch-notes/codex-mcp-session.md",
        force=False,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert payload["setup_command"] == HOSTED_CODEX_SETUP_COMMAND
    assert module._validate_mcp_client_evidence_payload(payload) is None


def test_write_mcp_client_evidence_record_rejects_hosted_url_for_local_stdio(
    tmp_path: Path,
    capsys,
) -> None:
    """Avoid creating local stdio evidence from a hosted-only setup shortcut."""
    module = _load_script_module()
    target = tmp_path / "launch-notes" / "codex-mcp-session.json"

    result = module.write_mcp_client_evidence_record(
        target,
        client="codex",
        transport="local-stdio",
        setup_command="",
        hosted_mcp_url="https://policynim.dev/mcp",
        reference="launch-notes/codex-mcp-session.md",
        force=False,
    )

    assert result == 1
    assert not target.exists()
    assert "--mcp-client-hosted-url is only valid with hosted-http" in capsys.readouterr().err


def test_write_mcp_client_evidence_record_replaces_blank_template_without_force(
    tmp_path: Path,
) -> None:
    """Let maintainers fill the generated blank template without unsafe overwrite flags."""
    module = _load_script_module()
    target = tmp_path / "launch-notes" / "codex-mcp-session.json"
    module.write_mcp_client_evidence_template(
        target,
        client="codex",
        transport="hosted-http",
        force=False,
    )

    result = module.write_mcp_client_evidence_record(
        target,
        client="codex",
        transport="hosted-http",
        setup_command=HOSTED_CODEX_SETUP_COMMAND,
        hosted_mcp_url="",
        reference="launch-notes/codex-mcp-session.md",
        force=False,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert payload["reference"] == "launch-notes/codex-mcp-session.md"


def test_write_mcp_client_evidence_record_refuses_nonblank_overwrite_without_force(
    tmp_path: Path,
) -> None:
    """Protect reviewed client-session records from accidental rewrites."""
    module = _load_script_module()
    target = tmp_path / "launch-notes" / "codex-mcp-session.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "client": "codex",
                "transport": "hosted-http",
                "server_name": "policynim",
                "setup_command": HOSTED_CODEX_SETUP_COMMAND,
                "tools": ["policy_preflight", "policy_search"],
                "called_tool": "policy_preflight",
                "reference": "launch-notes/old-session.md",
                "secrets_included": False,
            }
        ),
        encoding="utf-8",
    )

    result = module.write_mcp_client_evidence_record(
        target,
        client="codex",
        transport="hosted-http",
        setup_command=HOSTED_CODEX_SETUP_COMMAND,
        reference="launch-notes/new-session.md",
        force=False,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert result == 1
    assert payload["reference"] == "launch-notes/old-session.md"


def test_write_mcp_client_evidence_record_refuses_placeholder_reference(
    tmp_path: Path,
    capsys,
) -> None:
    """Avoid generating client evidence records that strict launch checks reject."""
    module = _load_script_module()
    target = tmp_path / "codex-mcp-session.json"

    result = module.write_mcp_client_evidence_record(
        target,
        client="codex",
        transport="hosted-http",
        setup_command=HOSTED_CODEX_SETUP_COMMAND,
        reference="https://github.com/example/policyNIM/issues/123",
        force=False,
    )

    assert result == 1
    assert not target.exists()
    assert "real, sanitized reference" in capsys.readouterr().err


def test_main_writes_mcp_client_template_before_collecting(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Avoid live probes when the user only wants a client evidence template."""
    module = _load_script_module()
    target = tmp_path / "codex-mcp-session.json"

    def fail_collect(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("collect_launch_evidence should not run")

    monkeypatch.setattr(module, "collect_launch_evidence", fail_collect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--write-mcp-client-evidence-template",
            str(target),
            "--mcp-client-template-client",
            "codex",
            "--mcp-client-template-transport",
            "hosted-http",
        ],
    )

    result = module.main()

    assert result == 0
    assert "Wrote MCP client evidence template" in capsys.readouterr().out
    assert json.loads(target.read_text(encoding="utf-8"))["reference"] == ""


def test_main_help_documents_client_setup_command_contract(
    monkeypatch,
    capsys,
) -> None:
    """Keep CLI help clear enough for maintainers collecting client-session proof."""
    module = _load_script_module()
    monkeypatch.setattr(sys, "argv", ["collect_launch_evidence.py", "--help"])

    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    normalized_output = " ".join(output.split())

    assert "--mcp-client-setup-command" in output
    assert "--mcp-client-hosted-url" in output
    assert "blank setup_command and reference fields" in output
    assert "supplying --mcp-client-reference plus either --mcp-client-setup-command" in (
        normalized_output
    )


def test_main_writes_mcp_client_record_before_collecting(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Avoid live probes when the user only wants a completed client evidence record."""
    module = _load_script_module()
    target = tmp_path / "codex-mcp-session.json"

    def fail_collect(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("collect_launch_evidence should not run")

    monkeypatch.setattr(module, "collect_launch_evidence", fail_collect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--write-mcp-client-evidence-record",
            str(target),
            "--mcp-client-template-client",
            "codex",
            "--mcp-client-template-transport",
            "hosted-http",
            "--mcp-client-setup-command",
            HOSTED_CODEX_SETUP_COMMAND,
            "--mcp-client-reference",
            "launch-notes/codex-mcp-session.md",
        ],
    )

    result = module.main()
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert "Wrote MCP client evidence record" in capsys.readouterr().out
    assert payload["setup_command"] == HOSTED_CODEX_SETUP_COMMAND
    assert payload["reference"] == "launch-notes/codex-mcp-session.md"


def test_main_writes_mcp_client_record_from_hosted_url_before_collecting(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Avoid hand-written setup commands when the hosted /mcp URL is known."""
    module = _load_script_module()
    target = tmp_path / "codex-mcp-session.json"

    def fail_collect(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("collect_launch_evidence should not run")

    monkeypatch.setattr(module, "collect_launch_evidence", fail_collect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--write-mcp-client-evidence-record",
            str(target),
            "--mcp-client-template-client",
            "codex",
            "--mcp-client-template-transport",
            "hosted-http",
            "--mcp-client-hosted-url",
            "https://policynim.dev/mcp",
            "--mcp-client-reference",
            "launch-notes/codex-mcp-session.md",
        ],
    )

    result = module.main()
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert "Wrote MCP client evidence record" in capsys.readouterr().out
    assert payload["setup_command"] == HOSTED_CODEX_SETUP_COMMAND


def test_collect_launch_evidence_reports_incomplete_release_assets(tmp_path: Path) -> None:
    """Avoid turning a live release into evidence when required assets are missing."""
    module = _load_script_module()
    _write_label_file(tmp_path)
    assets_without_manifest = [
        asset
        for asset in module.expected_release_assets("v0.1.0")
        if asset != "RELEASE_MANIFEST.json"
    ]

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=_release_payload(asset_names=assets_without_manifest), stderr=""
            )
        if command[:3] == ["gh", "label", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "nameWithOwner": "example/policyNIM",
                        "repositoryTopics": [{"name": "mcp"}, {"name": "verification"}],
                    }
                ),
                stderr="",
            )
        raise AssertionError(command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
    )

    assert payload["evidence"]["github_release_artifacts"]["summary"] == ""
    assert payload["probes"]["github_release_artifacts"]["status"] == "missing_assets"
    assert payload["probes"]["github_release_artifacts"]["missing_assets"] == [
        "RELEASE_MANIFEST.json"
    ]
    assert payload["probes"]["github_labels_applied"]["status"] == "label_drift"
    label_next_step = payload["probes"]["github_labels_applied"]["next_step"]
    assert "gh auth status" in label_next_step
    assert "sync_github_labels.py --live --format json" in label_next_step
    assert "sync_github_labels.py --apply --format json" in label_next_step


def test_collect_launch_evidence_reports_topic_drift(tmp_path: Path) -> None:
    """Do not claim repository discoverability evidence when live topics drift."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "nameWithOwner": "example/policyNIM",
                        "repositoryTopics": [{"name": "mcp"}],
                    }
                ),
                stderr="",
            )
        return _successful_runner(module, command)

    payload = module.collect_launch_evidence(
        repo_root=tmp_path,
        release_tag="v0.1.0",
        verified_by="maintainer@example.com",
        verified_at="2026-05-30T21:00:00Z",
        runner=fake_runner,
        pypi_payload=None,
    )

    assert payload["evidence"]["github_topics_applied"]["summary"] == ""
    assert payload["probes"]["github_topics_applied"]["status"] == "topic_drift"
    assert payload["probes"]["github_topics_applied"]["missing_or_changed"] == ["verification"]
    topic_next_step = payload["probes"]["github_topics_applied"]["next_step"]
    assert "gh auth status" in topic_next_step
    assert "sync_github_topics.py --live --format json" in topic_next_step
    assert "sync_github_topics.py --apply --format json" in topic_next_step


def test_write_collected_evidence_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    """Protect hand-reviewed launch evidence from accidental rewrites."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    target.write_text("existing", encoding="utf-8")

    result = module.write_evidence_file(
        target,
        evidence=module.blank_external_evidence(),
        force=False,
    )

    assert result == 1
    assert target.read_text(encoding="utf-8") == "existing"


def test_main_refuses_existing_evidence_target_before_collecting(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Avoid slow live probes when an output file would be rejected."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    target.write_text("existing", encoding="utf-8")

    def fail_collect(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("collect_launch_evidence should not run")

    monkeypatch.setattr(module, "collect_launch_evidence", fail_collect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--write-external-evidence-file",
            str(target),
        ],
    )

    result = module.main()
    captured = capsys.readouterr()

    assert result == 1
    assert "already exists" in captured.err
    assert "--merge-existing" in captured.err
    assert target.read_text(encoding="utf-8") == "existing"


def test_main_json_output_stays_machine_readable_when_writing_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Expose probe failures to automation even when the collector writes a file."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    payload = {
        "schema_version": "1",
        "generated_at": "2026-05-31T12:00:00Z",
        "release_tag": "v0.1.0",
        "evidence": module.blank_external_evidence(),
        "probes": {
            "github_artifact_attestations": {
                "status": "verification_failed",
                "next_step": "Publish release attestations.",
            }
        },
    }

    monkeypatch.setattr(module, "_fetch_pypi_payload", lambda: None)
    monkeypatch.setattr(module, "collect_launch_evidence", lambda **kwargs: payload)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--write-external-evidence-file",
            str(target),
            "--format",
            "json",
        ],
    )

    result = module.main()
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert result == 0
    assert captured.err == ""
    assert output["probes"]["github_artifact_attestations"]["status"] == "verification_failed"
    assert output["external_evidence_file"] == {
        "path": str(target),
        "mode": "written",
    }
    assert json.loads(target.read_text(encoding="utf-8")) == payload["evidence"]


def test_main_merge_clears_existing_record_when_current_probe_drifts(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Keep the CLI merge path from preserving stale GitHub metadata proof."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    existing = module.blank_external_evidence()
    existing["github_labels_applied"] = {
        "summary": "GitHub labels match .github/labels.yml.",
        "reference": "gh label list --json name,color,description --limit 1000",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T22:00:00Z",
    }
    target.write_text(json.dumps(existing), encoding="utf-8")
    payload = {
        "schema_version": "1",
        "generated_at": "2026-05-31T12:00:00Z",
        "release_tag": "v0.1.0",
        "evidence": module.blank_external_evidence(),
        "probes": {
            "github_labels_applied": {
                "status": "label_drift",
                "missing_or_changed": ["type/launch"],
            }
        },
    }

    monkeypatch.setattr(module, "_fetch_pypi_payload", lambda: None)
    monkeypatch.setattr(module, "collect_launch_evidence", lambda **kwargs: payload)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--write-external-evidence-file",
            str(target),
            "--merge-existing",
            "--format",
            "json",
        ],
    )

    result = module.main()
    output = json.loads(capsys.readouterr().out)
    written = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert output["external_evidence_file"]["mode"] == "merged"
    assert written["github_labels_applied"] == {
        "summary": "",
        "reference": "",
        "verified_by": "",
        "verified_at": "",
    }


def test_main_can_fail_when_requested_probe_does_not_pass(
    monkeypatch,
    capsys,
) -> None:
    """Let release automation fail when an explicitly requested proof is missing."""
    module = _load_script_module()
    payload = {
        "schema_version": "1",
        "generated_at": "2026-05-31T12:00:00Z",
        "release_tag": "v0.1.0",
        "evidence": module.blank_external_evidence(),
        "probes": {
            "github_artifact_attestations": {
                "status": "verification_failed",
                "detail": "Error: HTTP 404: Not Found",
                "next_step": "Publish release attestations.",
            }
        },
    }

    monkeypatch.setattr(module, "_fetch_pypi_payload", lambda: None)
    monkeypatch.setattr(module, "collect_launch_evidence", lambda **kwargs: payload)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--release-attestation-asset-name",
            "policynim-0.1.0-py3-none-any.whl",
            "--require-requested-probes",
            "--format",
            "json",
        ],
    )

    result = module.main()
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["requested_probe_failures"] == [
        {
            "name": "github_artifact_attestations",
            "status": "verification_failed",
            "next_step": "Publish release attestations.",
        }
    ]


def test_main_can_fail_when_requested_pypi_install_smoke_does_not_pass(
    monkeypatch,
    capsys,
) -> None:
    """Treat requested public install smoke as an enforceable release proof."""
    module = _load_script_module()
    payload = {
        "schema_version": "1",
        "generated_at": "2026-05-31T12:00:00Z",
        "release_tag": "v0.1.0",
        "evidence": module.blank_external_evidence(),
        "probes": {
            "pypi_install_smoke": {
                "status": "command_failed",
                "next_step": "Rerun the public PyPI install smoke.",
            }
        },
    }

    monkeypatch.setattr(module, "_fetch_pypi_payload", lambda: None)
    monkeypatch.setattr(module, "collect_launch_evidence", lambda **kwargs: payload)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--pypi-install-smoke",
            "--require-requested-probes",
            "--format",
            "json",
        ],
    )

    result = module.main()
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["requested_probe_failures"] == [
        {
            "name": "pypi_install_smoke",
            "status": "command_failed",
            "next_step": "Rerun the public PyPI install smoke.",
        }
    ]


def test_main_can_fail_when_requested_github_install_smoke_does_not_pass(
    monkeypatch,
    capsys,
) -> None:
    """Treat requested GitHub installer smoke as an enforceable release proof."""
    module = _load_script_module()
    payload = {
        "schema_version": "1",
        "generated_at": "2026-05-31T12:00:00Z",
        "release_tag": "v0.1.0",
        "evidence": module.blank_external_evidence(),
        "probes": {
            "github_release_install_smoke": {
                "status": "missing_first_run_command",
                "next_step": "Publish a new GitHub release.",
            }
        },
    }

    monkeypatch.setattr(module, "_fetch_pypi_payload", lambda: None)
    monkeypatch.setattr(module, "collect_launch_evidence", lambda **kwargs: payload)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--github-install-smoke",
            "--require-requested-probes",
            "--format",
            "json",
        ],
    )

    result = module.main()
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["requested_probe_failures"] == [
        {
            "name": "github_release_install_smoke",
            "status": "missing_first_run_command",
            "next_step": "Publish a new GitHub release.",
        }
    ]


def test_main_strict_requested_probe_mode_ignores_unrequested_failures(
    monkeypatch,
    capsys,
) -> None:
    """Keep partial launch evidence collection compatible by defaulting to requested probes."""
    module = _load_script_module()
    payload = {
        "schema_version": "1",
        "generated_at": "2026-05-31T12:00:00Z",
        "release_tag": "v0.1.0",
        "evidence": module.blank_external_evidence(),
        "probes": {
            "github_artifact_attestations": {
                "status": "manual_required",
                "next_step": "Pass an asset name.",
            }
        },
    }

    monkeypatch.setattr(module, "_fetch_pypi_payload", lambda: None)
    monkeypatch.setattr(module, "collect_launch_evidence", lambda **kwargs: payload)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_launch_evidence.py",
            "--require-requested-probes",
            "--format",
            "json",
        ],
    )

    result = module.main()
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert "requested_probe_failures" not in output


def test_launch_evidence_text_output_includes_requested_probe_failures(
    tmp_path: Path,
) -> None:
    """Make strict collector failures readable without parsing JSON."""
    module = _load_script_module()
    _write_label_file(tmp_path)

    payload = {
        "release_tag": "v0.1.0",
        "probes": {
            "github_artifact_attestations": {
                "status": "verification_failed",
                "next_step": "Publish release attestations.",
            }
        },
        "requested_probe_failures": [
            {
                "name": "github_artifact_attestations",
                "status": "verification_failed",
                "next_step": "Publish release attestations.",
            }
        ],
    }

    output = module._render_text(payload)

    assert "Requested probe failures:" in output
    assert "- github_artifact_attestations: verification_failed" in output
    assert "  next: Publish release attestations." in output


def test_write_collected_evidence_merge_preserves_unverified_existing_records(
    tmp_path: Path,
) -> None:
    """Allow incremental evidence collection without clobbering reviewed records."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    existing = module.blank_external_evidence()
    existing["real_mcp_client_session"] = {
        "summary": "Codex connected to the hosted MCP and called policy_preflight.",
        "reference": "launch-notes/codex-mcp-session.json",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T22:00:00Z",
    }
    target.write_text(json.dumps(existing), encoding="utf-8")
    collected = module.blank_external_evidence()
    collected["github_release_artifacts"] = {
        "summary": "GitHub release v0.1.0 contains every required artifact.",
        "reference": "https://github.com/example/policyNIM/releases/tag/v0.1.0",
        "verified_by": "release-bot",
        "verified_at": "2026-05-30T23:00:00Z",
    }

    result = module.write_evidence_file(
        target,
        evidence=collected,
        force=False,
        merge_existing=True,
    )
    written = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert written["real_mcp_client_session"] == existing["real_mcp_client_session"]
    assert written["github_release_artifacts"] == collected["github_release_artifacts"]


def test_write_collected_evidence_merge_clears_existing_record_on_current_drift(
    tmp_path: Path,
) -> None:
    """Do not preserve old external proof when the current live probe found drift."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    existing = module.blank_external_evidence()
    existing["github_labels_applied"] = {
        "summary": "GitHub labels match .github/labels.yml.",
        "reference": "gh label list --json name,color,description --limit 1000",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T22:00:00Z",
    }
    existing["real_mcp_client_session"] = {
        "summary": "Codex connected to the hosted MCP and called policy_preflight.",
        "reference": "launch-notes/codex-mcp-session.json",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T22:00:00Z",
    }
    target.write_text(json.dumps(existing), encoding="utf-8")

    result = module.write_evidence_file(
        target,
        evidence=module.blank_external_evidence(),
        force=False,
        merge_existing=True,
        probes={
            "github_labels_applied": {
                "status": "label_drift",
                "missing_or_changed": ["type/launch"],
            }
        },
    )
    written = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert written["github_labels_applied"] == {
        "summary": "",
        "reference": "",
        "verified_by": "",
        "verified_at": "",
    }
    assert written["real_mcp_client_session"] == existing["real_mcp_client_session"]


def test_write_collected_evidence_merge_clears_existing_pypi_smoke_on_current_failure(
    tmp_path: Path,
) -> None:
    """Do not preserve old PyPI install proof when the current public smoke fails."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    existing = module.blank_external_evidence()
    existing["pypi_install_smoke"] = {
        "summary": "Clean PyPI install smoke passed for policynim==0.1.0.",
        "reference": "https://pypi.org/project/policynim/0.1.0/",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T22:00:00Z",
    }
    target.write_text(json.dumps(existing), encoding="utf-8")

    result = module.write_evidence_file(
        target,
        evidence=module.blank_external_evidence(),
        force=False,
        merge_existing=True,
        probes={
            "pypi_install_smoke": {
                "status": "missing_first_run_command",
                "command": "policynim quickstart --format json",
            }
        },
    )
    written = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert written["pypi_install_smoke"] == {
        "summary": "",
        "reference": "",
        "verified_by": "",
        "verified_at": "",
    }


def test_write_collected_evidence_merge_clears_existing_github_smoke_on_current_failure(
    tmp_path: Path,
) -> None:
    """Do not preserve old GitHub installer proof when the current smoke fails."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    existing = module.blank_external_evidence()
    existing["github_release_install_smoke"] = {
        "summary": "Clean GitHub release installer smoke passed for v0.1.0.",
        "reference": "https://github.com/nnennandukwe/policyNIM/releases/tag/v0.1.0",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T22:00:00Z",
    }
    target.write_text(json.dumps(existing), encoding="utf-8")

    result = module.write_evidence_file(
        target,
        evidence=module.blank_external_evidence(),
        force=False,
        merge_existing=True,
        probes={
            "github_release_install_smoke": {
                "status": "missing_first_run_command",
                "command": "policynim quickstart --format json",
            }
        },
    )
    written = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert written["github_release_install_smoke"] == {
        "summary": "",
        "reference": "",
        "verified_by": "",
        "verified_at": "",
    }


def test_write_collected_evidence_merge_clears_existing_release_artifacts_on_current_failure(
    tmp_path: Path,
) -> None:
    """Do not preserve old release asset proof when the current release probe fails."""
    module = _load_script_module()
    invalidating_statuses = (
        "draft_release",
        "missing_assets",
        "missing_release_reference",
        "metadata_download_failed",
        "release_view_failed",
        "release_view_invalid_json",
        "release_metadata_missing",
        "release_metadata_invalid",
    )

    for status in invalidating_statuses:
        target = tmp_path / f"{status}.json"
        existing = module.blank_external_evidence()
        existing["github_release_artifacts"] = {
            "summary": (
                "GitHub release v0.1.0 contains required assets: "
                "policynim-v0.1.0-linux-amd64.tar.gz."
            ),
            "reference": "https://github.com/nnennandukwe/policyNIM/releases/tag/v0.1.0",
            "verified_by": "maintainer@example.com",
            "verified_at": "2026-05-30T22:00:00Z",
        }
        existing["real_mcp_client_session"] = {
            "summary": "Codex connected to hosted MCP and called policy_preflight.",
            "reference": "launch-notes/codex-mcp-session.json",
            "verified_by": "maintainer@example.com",
            "verified_at": "2026-05-30T22:00:00Z",
        }
        target.write_text(json.dumps(existing), encoding="utf-8")

        result = module.write_evidence_file(
            target,
            evidence=module.blank_external_evidence(),
            force=False,
            merge_existing=True,
            probes={
                "github_release_artifacts": {
                    "status": status,
                    "next_step": "Fix the GitHub release asset set.",
                }
            },
        )
        written = json.loads(target.read_text(encoding="utf-8"))

        assert result == 0
        assert written["github_release_artifacts"] == {
            "summary": "",
            "reference": "",
            "verified_by": "",
            "verified_at": "",
        }
        assert written["real_mcp_client_session"] == existing["real_mcp_client_session"]


def test_write_collected_evidence_merge_upgrades_legacy_missing_checks(
    tmp_path: Path,
) -> None:
    """Allow incremental collection to add newly introduced blank evidence keys."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    existing = module.blank_external_evidence()
    existing.pop("github_topics_applied")
    existing["github_labels_applied"] = {
        "summary": "GitHub labels match .github/labels.yml.",
        "reference": "gh label list --json name,color,description --limit 1000",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-05-30T22:00:00Z",
    }
    target.write_text(json.dumps(existing), encoding="utf-8")
    collected = module.blank_external_evidence()

    result = module.write_evidence_file(
        target,
        evidence=collected,
        force=False,
        merge_existing=True,
    )
    written = json.loads(target.read_text(encoding="utf-8"))

    assert result == 0
    assert set(written) == set(module.blank_external_evidence())
    assert written["github_labels_applied"] == existing["github_labels_applied"]
    assert written["github_topics_applied"] == {
        "summary": "",
        "reference": "",
        "verified_by": "",
        "verified_at": "",
    }


def test_write_collected_evidence_merge_refuses_malformed_existing_file(
    tmp_path: Path,
) -> None:
    """Fail closed when an incremental evidence merge cannot parse existing proof."""
    module = _load_script_module()
    target = tmp_path / "launch-evidence.json"
    target.write_text("{not-json", encoding="utf-8")

    result = module.write_evidence_file(
        target,
        evidence=module.blank_external_evidence(),
        force=False,
        merge_existing=True,
    )

    assert result == 1
    assert target.read_text(encoding="utf-8") == "{not-json"
