"""Collect opt-in external launch evidence for PolicyNIM public readiness."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sync_github_labels import LabelSyncError, load_label_taxonomy, plan_label_sync  # noqa: E402
from sync_github_topics import TopicSyncError, load_topic_taxonomy, plan_topic_sync  # noqa: E402

GITHUB_RELEASE_FIELDS = "assets,isDraft,isPrerelease,publishedAt,tagName,targetCommitish,url"
GITHUB_RUN_FIELDS = "conclusion,event,headSha,jobs,name,status,url,workflowName"
HOSTED_SMOKE_ARTIFACT_NAME = "hosted-smoke-evidence"
HOSTED_SMOKE_JUNIT_FILENAME = "policynim-hosted-smoke-junit.xml"
RELEASE_METADATA_ASSET_NAMES = ("RELEASE_MANIFEST.json", "SHA256SUMS")
EXTERNAL_CHECK_NAMES = {
    "github_artifact_attestations",
    "github_release_install_smoke",
    "github_release_artifacts",
    "pypi_install_smoke",
    "pypi_project",
    "hosted_mcp_domain",
    "hosted_beta_live_smoke",
    "github_labels_applied",
    "github_topics_applied",
    "real_mcp_client_session",
}
EVIDENCE_RECORD_FIELDS = ("summary", "reference", "verified_by", "verified_at")
PLACEHOLDER_REFERENCE_MARKERS = (
    "<",
    ">",
    "example.",
    "example/",
    ".invalid",
    "todo",
    "placeholder",
)
_EXPECTED_HOSTED_SMOKE_TESTS = (
    "test_hosted_healthz_reports_ready_index_live",
    "test_hosted_mcp_lists_tools_live",
    "test_hosted_policy_search_live",
    "test_hosted_policy_preflight_live",
    "test_hosted_mcp_rejects_invalid_token_live",
)
PYPI_PROJECT_NAME = "policynim"
_MERGE_INVALIDATING_PROBE_STATUSES = {
    "command_failed",
    "draft_release",
    "invalid_first_run_contract",
    "invalid_json",
    "label_drift",
    "metadata_download_failed",
    "missing_first_run_command",
    "missing_assets",
    "missing_release_reference",
    "release_view_failed",
    "release_view_invalid_json",
    "release_metadata_invalid",
    "release_metadata_missing",
    "topic_drift",
}

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
UrlOpener = Callable[..., Any]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to inspect. Defaults to this script's repository.",
    )
    parser.add_argument(
        "--release-tag",
        default="",
        help="Release tag to inspect. Defaults to v<pyproject version>.",
    )
    parser.add_argument(
        "--verified-by",
        default=os.environ.get("USER", "local-maintainer"),
        help="Verifier identity to place on machine-verified evidence records.",
    )
    parser.add_argument(
        "--pypi-publish-run-url",
        default="",
        help=(
            "Optional GitHub Actions run URL or run id for the Release workflow "
            "that published to PyPI. When set with matching public PyPI JSON, "
            "the collector verifies the publish-pypi job completed successfully "
            "before filling pypi_project evidence."
        ),
    )
    parser.add_argument(
        "--pypi-install-smoke",
        action="store_true",
        help=(
            "Install policynim==<release version> from public PyPI in a clean "
            "virtualenv and run first-run help, quickstart JSON, support-bundle "
            "JSON, doctor JSON, and local MCP config JSON before filling "
            "pypi_install_smoke evidence."
        ),
    )
    parser.add_argument(
        "--github-install-smoke",
        action="store_true",
        help=(
            "Install the GitHub release with the published install.sh in a clean "
            "HOME and run first-run help, quickstart JSON, support-bundle JSON, "
            "doctor JSON, and local MCP config JSON before filling "
            "github_release_install_smoke evidence."
        ),
    )
    parser.add_argument(
        "--release-attestation-asset-name",
        default="",
        help=(
            "Optional release asset filename to download and verify with "
            "`gh attestation verify`. When set, the collector fills "
            "github_artifact_attestations evidence only after verification passes."
        ),
    )
    parser.add_argument(
        "--hosted-mcp-url",
        default="",
        help=(
            "Optional public hosted MCP /mcp URL. When set, the collector calls "
            "the same-origin /healthz route and verifies /mcp rejects an invalid "
            "bearer token."
        ),
    )
    parser.add_argument(
        "--hosted-smoke-run-url",
        default="",
        help=(
            "Optional GitHub Actions run URL or run id for the Hosted Beta Smoke "
            "workflow. When set, the collector verifies the run completed "
            "successfully and validates the hosted-smoke-evidence JUnit artifact "
            "before filling hosted_beta_live_smoke evidence."
        ),
    )
    parser.add_argument(
        "--mcp-client-evidence-file",
        type=Path,
        default=None,
        help=(
            "Optional reviewed JSON record from a real Codex or Claude Code MCP "
            "session. When set, the collector validates the client, transport, "
            "setup command, tool list, policy_preflight call, reference, and "
            "redaction claim before filling real_mcp_client_session evidence."
        ),
    )
    parser.add_argument(
        "--write-mcp-client-evidence-template",
        type=Path,
        default=None,
        help=(
            "Write a safe MCP client-session evidence JSON template with blank "
            "setup_command and reference fields, then exit without running live probes."
        ),
    )
    parser.add_argument(
        "--write-mcp-client-evidence-record",
        type=Path,
        default=None,
        help=(
            "Write a completed MCP client-session evidence JSON record after "
            "supplying --mcp-client-reference plus either --mcp-client-setup-command "
            "or --mcp-client-hosted-url, "
            "then exit without running live probes."
        ),
    )
    parser.add_argument(
        "--mcp-client-template-client",
        choices=("codex", "claude-code"),
        default="codex",
        help="Client value to place in MCP client evidence template or record output.",
    )
    parser.add_argument(
        "--mcp-client-template-transport",
        choices=("hosted-http", "local-stdio"),
        default="hosted-http",
        help="Transport value to place in MCP client evidence template or record output.",
    )
    parser.add_argument(
        "--mcp-client-reference",
        default="",
        help=(
            "Sanitized transcript, screenshot, issue, or release-note reference "
            "to place in --write-mcp-client-evidence-record output."
        ),
    )
    parser.add_argument(
        "--mcp-client-setup-command",
        default="",
        help=(
            "Secret-safe MCP client setup command used in the reviewed real client "
            "session, for example the generated Codex or Claude Code add command."
        ),
    )
    parser.add_argument(
        "--mcp-client-hosted-url",
        default="",
        help=(
            "Hosted /mcp URL used to generate the secret-safe setup command for "
            "--write-mcp-client-evidence-record when the transport is hosted-http."
        ),
    )
    parser.add_argument(
        "--write-external-evidence-file",
        type=Path,
        default=None,
        help=(
            "Write only the external evidence object accepted by "
            "scripts/oss_readiness_check.py --external-evidence-file."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --write-external-evidence-file to overwrite an existing file.",
    )
    parser.add_argument(
        "--merge-existing",
        action="store_true",
        help=(
            "When --write-external-evidence-file already exists, preserve existing "
            "nonblank records for checks this run could not verify."
        ),
    )
    parser.add_argument(
        "--require-requested-probes",
        action="store_true",
        help=(
            "Exit non-zero when any explicitly requested external probe does not "
            "pass. Defaults to partial evidence collection."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Output format for the collection report.",
    )
    args = parser.parse_args()
    if args.write_external_evidence_file is not None and args.force and args.merge_existing:
        parser.error("--force and --merge-existing cannot be combined.")
    if args.write_mcp_client_evidence_template is not None and args.merge_existing:
        parser.error("--merge-existing is only valid with --write-external-evidence-file.")
    if args.write_mcp_client_evidence_record is not None and args.merge_existing:
        parser.error("--merge-existing is only valid with --write-external-evidence-file.")
    if (
        args.write_mcp_client_evidence_template is not None
        and args.write_mcp_client_evidence_record is not None
    ):
        parser.error(
            "--write-mcp-client-evidence-template and "
            "--write-mcp-client-evidence-record cannot be combined."
        )
    if args.write_mcp_client_evidence_template is not None:
        return write_mcp_client_evidence_template(
            args.write_mcp_client_evidence_template,
            client=args.mcp_client_template_client,
            transport=args.mcp_client_template_transport,
            force=args.force,
        )
    if args.write_mcp_client_evidence_record is not None:
        return write_mcp_client_evidence_record(
            args.write_mcp_client_evidence_record,
            client=args.mcp_client_template_client,
            transport=args.mcp_client_template_transport,
            setup_command=args.mcp_client_setup_command,
            hosted_mcp_url=args.mcp_client_hosted_url,
            reference=args.mcp_client_reference,
            force=args.force,
        )
    if (
        args.write_external_evidence_file is not None
        and args.write_external_evidence_file.exists()
        and not args.force
        and not args.merge_existing
    ):
        print(
            f"Error: {_existing_evidence_file_error(args.write_external_evidence_file)}",
            file=sys.stderr,
        )
        return 1

    repo_root = args.repo_root.resolve()
    release_tag = args.release_tag.strip() or _default_release_tag(repo_root)
    payload = collect_launch_evidence(
        repo_root=repo_root,
        release_tag=release_tag,
        verified_by=args.verified_by.strip() or "local-maintainer",
        verified_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        pypi_payload=_fetch_pypi_payload(),
        pypi_publish_run_url=args.pypi_publish_run_url,
        pypi_install_smoke=args.pypi_install_smoke,
        github_install_smoke=args.github_install_smoke,
        release_attestation_asset_name=args.release_attestation_asset_name,
        hosted_mcp_url=args.hosted_mcp_url,
        hosted_smoke_run_url=args.hosted_smoke_run_url,
        mcp_client_evidence_file=args.mcp_client_evidence_file,
    )
    requested_probe_failures = _requested_probe_failures(
        payload,
        requested_probe_names=_requested_probe_names(args),
    )
    if args.require_requested_probes and requested_probe_failures:
        payload["requested_probe_failures"] = requested_probe_failures

    if args.write_external_evidence_file is not None:
        write_mode = _evidence_write_mode(
            args.write_external_evidence_file,
            merge_existing=args.merge_existing,
        )
        write_result = write_evidence_file(
            args.write_external_evidence_file,
            evidence=payload["evidence"],
            force=args.force,
            merge_existing=args.merge_existing,
            probes=payload["probes"],
            emit_message=args.format != "json",
        )
        if write_result != 0:
            return write_result
        payload["external_evidence_file"] = {
            "path": str(args.write_external_evidence_file),
            "mode": write_mode,
        }
        if args.format == "json":
            print(json.dumps(payload, indent=2))
        else:
            print(_render_text(payload))
        return 1 if args.require_requested_probes and requested_probe_failures else 0

    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(_render_text(payload))
    return 1 if args.require_requested_probes and requested_probe_failures else 0


def collect_launch_evidence(
    *,
    repo_root: Path,
    release_tag: str,
    verified_by: str,
    verified_at: str,
    runner: Runner | None = None,
    pypi_payload: dict[str, Any] | None = None,
    pypi_publish_run_url: str = "",
    pypi_install_smoke: bool = False,
    github_install_smoke: bool = False,
    release_attestation_asset_name: str = "",
    hosted_mcp_url: str = "",
    hosted_smoke_run_url: str = "",
    mcp_client_evidence_file: Path | None = None,
    urlopen: UrlOpener | None = None,
) -> dict[str, Any]:
    """Collect machine-verifiable launch facts without claiming manual proof."""
    command_runner = runner or _run_command
    evidence = blank_external_evidence()
    probes: dict[str, dict[str, Any]] = {}

    release_probe, release_record = _probe_github_release(
        release_tag=release_tag,
        verified_by=verified_by,
        verified_at=verified_at,
        runner=command_runner,
    )
    probes["github_release_artifacts"] = release_probe
    if release_record is not None:
        evidence["github_release_artifacts"] = release_record

    github_install_probe, github_install_record = _probe_github_release_install_smoke(
        release_tag=release_tag,
        requested=github_install_smoke,
        verified_by=verified_by,
        verified_at=verified_at,
        runner=command_runner,
    )
    probes["github_release_install_smoke"] = github_install_probe
    if github_install_record is not None:
        evidence["github_release_install_smoke"] = github_install_record

    attestation_probe, attestation_record = _probe_github_artifact_attestation(
        release_tag=release_tag,
        asset_name=release_attestation_asset_name,
        release_url=str(release_probe.get("reference", "")),
        verified_by=verified_by,
        verified_at=verified_at,
        runner=command_runner,
    )
    probes["github_artifact_attestations"] = attestation_probe
    if attestation_record is not None:
        evidence["github_artifact_attestations"] = attestation_record

    pypi_probe, pypi_record = _probe_pypi_project(
        release_tag=release_tag,
        release_target_commitish=str(release_probe.get("target_commitish", "")),
        pypi_payload=pypi_payload,
        pypi_publish_run_url=pypi_publish_run_url,
        verified_by=verified_by,
        verified_at=verified_at,
        runner=command_runner,
    )
    probes["pypi_project"] = pypi_probe
    if pypi_record is not None:
        evidence["pypi_project"] = pypi_record

    pypi_install_probe, pypi_install_record = _probe_pypi_install_smoke(
        release_tag=release_tag,
        requested=pypi_install_smoke,
        verified_by=verified_by,
        verified_at=verified_at,
        runner=command_runner,
    )
    probes["pypi_install_smoke"] = pypi_install_probe
    if pypi_install_record is not None:
        evidence["pypi_install_smoke"] = pypi_install_record

    labels_probe, labels_record = _probe_github_labels(
        repo_root=repo_root,
        verified_by=verified_by,
        verified_at=verified_at,
        runner=command_runner,
    )
    probes["github_labels_applied"] = labels_probe
    if labels_record is not None:
        evidence["github_labels_applied"] = labels_record

    topics_probe, topics_record = _probe_github_topics(
        repo_root=repo_root,
        verified_by=verified_by,
        verified_at=verified_at,
        runner=command_runner,
    )
    probes["github_topics_applied"] = topics_probe
    if topics_record is not None:
        evidence["github_topics_applied"] = topics_record

    hosted_probe, hosted_record = _probe_hosted_mcp_domain(
        hosted_mcp_url=hosted_mcp_url,
        verified_by=verified_by,
        verified_at=verified_at,
        urlopen=urlopen or urllib.request.urlopen,
    )
    probes["hosted_mcp_domain"] = hosted_probe
    if hosted_record is not None:
        evidence["hosted_mcp_domain"] = hosted_record

    smoke_probe, smoke_record = _probe_hosted_smoke_run(
        hosted_smoke_run_url=hosted_smoke_run_url,
        release_target_commitish=str(release_probe.get("target_commitish", "")),
        verified_by=verified_by,
        verified_at=verified_at,
        runner=command_runner,
    )
    probes["hosted_beta_live_smoke"] = smoke_probe
    if smoke_record is not None:
        evidence["hosted_beta_live_smoke"] = smoke_record

    client_probe, client_record = _probe_mcp_client_evidence_file(
        mcp_client_evidence_file=mcp_client_evidence_file,
        verified_by=verified_by,
        verified_at=verified_at,
    )
    probes["real_mcp_client_session"] = client_probe
    if client_record is not None:
        evidence["real_mcp_client_session"] = client_record

    return {
        "schema_version": "1",
        "generated_at": verified_at,
        "release_tag": release_tag,
        "evidence": evidence,
        "probes": {name: probes[name] for name in sorted(probes)},
    }


def blank_external_evidence() -> dict[str, dict[str, str]]:
    """Return a blank evidence object compatible with oss_readiness_check.py."""
    return {
        name: {field: "" for field in EVIDENCE_RECORD_FIELDS}
        for name in sorted(EXTERNAL_CHECK_NAMES)
    }


def _requested_probe_names(args: argparse.Namespace) -> list[str]:
    requested: list[str] = []
    if str(args.release_attestation_asset_name).strip():
        requested.append("github_artifact_attestations")
    if str(args.pypi_publish_run_url).strip():
        requested.append("pypi_project")
    if bool(args.pypi_install_smoke):
        requested.append("pypi_install_smoke")
    if bool(args.github_install_smoke):
        requested.append("github_release_install_smoke")
    if str(args.hosted_mcp_url).strip():
        requested.append("hosted_mcp_domain")
    if str(args.hosted_smoke_run_url).strip():
        requested.append("hosted_beta_live_smoke")
    if args.mcp_client_evidence_file is not None:
        requested.append("real_mcp_client_session")
    return requested


def _requested_probe_failures(
    payload: dict[str, Any],
    *,
    requested_probe_names: list[str],
) -> list[dict[str, str]]:
    probes = payload.get("probes")
    failures: list[dict[str, str]] = []
    for name in requested_probe_names:
        probe = probes.get(name) if isinstance(probes, dict) else None
        if isinstance(probe, dict) and probe.get("status") == "passed":
            continue
        status = "missing_probe"
        next_step = "Rerun the collector and inspect probe output for this requested proof."
        if isinstance(probe, dict):
            status = str(probe.get("status") or status)
            next_step = str(probe.get("next_step") or next_step)
        failures.append(
            {
                "name": name,
                "status": status,
                "next_step": next_step,
            }
        )
    return failures


def mcp_client_evidence_template(
    *,
    client: str = "codex",
    transport: str = "hosted-http",
) -> dict[str, Any]:
    """Return a safe MCP client-session evidence template."""
    if client not in {"codex", "claude-code"}:
        raise ValueError("MCP client evidence template client must be codex or claude-code.")
    if transport not in {"hosted-http", "local-stdio"}:
        raise ValueError(
            "MCP client evidence template transport must be hosted-http or local-stdio."
        )
    return {
        "client": client,
        "transport": transport,
        "server_name": "policynim",
        "setup_command": "",
        "tools": list(_expected_mcp_tools()),
        "called_tool": "policy_preflight",
        "reference": "",
        "secrets_included": False,
    }


def mcp_client_evidence_record(
    *,
    client: str = "codex",
    transport: str = "hosted-http",
    setup_command: str,
    hosted_mcp_url: str = "",
    reference: str,
) -> dict[str, Any]:
    """Return a completed MCP client-session evidence record."""
    payload = mcp_client_evidence_template(client=client, transport=transport)
    payload["setup_command"] = _resolve_mcp_client_setup_command(
        client=client,
        transport=transport,
        setup_command=setup_command,
        hosted_mcp_url=hosted_mcp_url,
    )
    payload["reference"] = reference.strip()
    validation_error = _validate_mcp_client_evidence_payload(payload)
    if validation_error is not None:
        raise ValueError(validation_error["next_step"])
    return payload


def _resolve_mcp_client_setup_command(
    *,
    client: str,
    transport: str,
    setup_command: str,
    hosted_mcp_url: str,
) -> str:
    raw_setup_command = setup_command.strip()
    raw_hosted_url = hosted_mcp_url.strip()
    if raw_hosted_url and transport != "hosted-http":
        raise ValueError("--mcp-client-hosted-url is only valid with hosted-http transport.")
    if raw_setup_command:
        return raw_setup_command
    if not raw_hosted_url:
        return raw_setup_command

    hosted_url = _normalize_hosted_mcp_url(raw_hosted_url)
    if _contains_placeholder_reference(hosted_url):
        raise ValueError("--mcp-client-hosted-url must be a real hosted /mcp URL.")
    if client == "codex":
        return f"codex mcp add policynim --url {hosted_url} --bearer-token-env-var POLICYNIM_TOKEN"
    return (
        f"claude mcp add --transport http policynim {hosted_url} "
        '--header "Authorization: Bearer $POLICYNIM_TOKEN"'
    )


def write_mcp_client_evidence_template(
    path: Path,
    *,
    client: str,
    transport: str,
    force: bool,
) -> int:
    """Write a safe client-session evidence template for maintainers to complete."""
    if path.exists() and not force:
        print(
            f"Error: {path} already exists. Pass --force only when replacing a template.",
            file=sys.stderr,
        )
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = mcp_client_evidence_template(client=client, transport=transport)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote MCP client evidence template: {path}")
    return 0


def write_mcp_client_evidence_record(
    path: Path,
    *,
    client: str,
    transport: str,
    setup_command: str,
    hosted_mcp_url: str = "",
    reference: str,
    force: bool,
) -> int:
    """Write a completed client-session evidence record for maintainer review."""
    if (
        path.exists()
        and not force
        and not _is_replacable_mcp_client_template(
            path,
            client=client,
            transport=transport,
        )
    ):
        print(
            (f"Error: {path} already exists. Pass --force only when replacing a nonblank record."),
            file=sys.stderr,
        )
        return 1
    try:
        payload = mcp_client_evidence_record(
            client=client,
            transport=transport,
            setup_command=setup_command,
            hosted_mcp_url=hosted_mcp_url,
            reference=reference,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote MCP client evidence record: {path}")
    return 0


def _is_replacable_mcp_client_template(
    path: Path,
    *,
    client: str,
    transport: str,
) -> bool:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return existing == mcp_client_evidence_template(client=client, transport=transport)


def expected_release_assets(release_tag: str) -> list[str]:
    """Return the public release asset contract for a tag."""
    version = release_tag.removeprefix("v")
    return [
        "RELEASE_MANIFEST.json",
        "SHA256SUMS",
        "install.ps1",
        "install.sh",
        f"policynim-{version}-py3-none-any.whl",
        f"policynim-{version}.tar.gz",
        f"policynim-{release_tag}-darwin-amd64.tar.gz",
        f"policynim-{release_tag}-darwin-arm64.tar.gz",
        f"policynim-{release_tag}-linux-amd64.tar.gz",
        f"policynim-{release_tag}-windows-amd64.zip",
    ]


def expected_hosted_smoke_tests() -> tuple[str, ...]:
    """Return the hosted live tests required for launch evidence."""
    return _EXPECTED_HOSTED_SMOKE_TESTS


def write_evidence_file(
    path: Path,
    *,
    evidence: dict[str, dict[str, str]],
    force: bool,
    merge_existing: bool = False,
    probes: dict[str, dict[str, Any]] | None = None,
    emit_message: bool = True,
) -> int:
    """Write collected external evidence in the readiness-check input format."""
    path_existed = path.exists()
    output_evidence = evidence
    if path_existed and merge_existing:
        existing_evidence, error = _load_mergeable_evidence(path)
        if error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        output_evidence = _merge_external_evidence(
            existing_evidence,
            evidence,
            probes=probes,
        )
    elif path_existed and not force:
        print(
            f"Error: {_existing_evidence_file_error(path)}",
            file=sys.stderr,
        )
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output_evidence, indent=2) + "\n", encoding="utf-8")
    if emit_message:
        if merge_existing and path_existed:
            print(f"Merged collected external evidence: {path}")
        else:
            print(f"Wrote collected external evidence: {path}")
    return 0


def _evidence_write_mode(path: Path, *, merge_existing: bool) -> str:
    if merge_existing and path.exists():
        return "merged"
    return "written"


def _existing_evidence_file_error(path: Path) -> str:
    return (
        f"{path} already exists. Pass --merge-existing to preserve current "
        "records or --force to overwrite it."
    )


def _load_mergeable_evidence(
    path: Path,
) -> tuple[dict[str, dict[str, str]], str]:
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, (f"{path} could not be loaded for --merge-existing: {type(exc).__name__}: {exc}")
    normalized, errors = _normalize_evidence_object(raw_payload, allow_missing_checks=True)
    if errors:
        return {}, (
            f"{path} cannot be merged because it is invalid: {_format_validation_errors(errors)}."
        )
    return normalized, ""


def _merge_external_evidence(
    existing: dict[str, dict[str, str]],
    collected: dict[str, dict[str, str]],
    *,
    probes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, str]]:
    merged = blank_external_evidence()
    for name in sorted(EXTERNAL_CHECK_NAMES):
        collected_record = collected.get(name, {})
        existing_record = existing.get(name, {})
        if _is_complete_evidence_record(collected_record):
            merged[name] = {
                field: collected_record[field].strip() for field in EVIDENCE_RECORD_FIELDS
            }
        elif _is_complete_evidence_record(existing_record):
            if _probe_invalidates_existing_record(probes, name):
                continue
            merged[name] = {
                field: existing_record[field].strip() for field in EVIDENCE_RECORD_FIELDS
            }
    return merged


def _probe_invalidates_existing_record(
    probes: dict[str, dict[str, Any]] | None,
    name: str,
) -> bool:
    """Return whether a current probe proves an existing record has drifted."""
    if probes is None:
        return False
    probe = probes.get(name)
    if not isinstance(probe, dict):
        return False
    return str(probe.get("status", "")).strip() in _MERGE_INVALIDATING_PROBE_STATUSES


def _normalize_evidence_object(
    raw_payload: object,
    *,
    allow_missing_checks: bool = False,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    if not isinstance(raw_payload, dict):
        return {}, ["evidence file must contain a JSON object"]

    loaded_names = {key for key in raw_payload if isinstance(key, str)}
    unknown_names = sorted(loaded_names - EXTERNAL_CHECK_NAMES)
    missing_names = sorted(EXTERNAL_CHECK_NAMES - loaded_names)
    errors: list[str] = []
    if unknown_names:
        errors.append(f"unknown checks: {', '.join(unknown_names)}")
    if missing_names and not allow_missing_checks:
        errors.append(f"missing checks: {', '.join(missing_names)}")

    normalized = blank_external_evidence()
    for name in sorted(EXTERNAL_CHECK_NAMES):
        if name not in raw_payload:
            continue
        record, record_errors = _normalize_evidence_record(name, raw_payload.get(name))
        if record_errors:
            errors.extend(record_errors)
            continue
        normalized[name] = record
    return normalized, errors


def _normalize_evidence_record(
    check_name: str,
    raw_value: object,
) -> tuple[dict[str, str], list[str]]:
    if not isinstance(raw_value, dict):
        return {}, [(f"{check_name} must be an object with {', '.join(EVIDENCE_RECORD_FIELDS)}")]

    unknown_fields = sorted(
        field
        for field in raw_value
        if isinstance(field, str) and field not in EVIDENCE_RECORD_FIELDS
    )
    missing_fields = [field for field in EVIDENCE_RECORD_FIELDS if field not in raw_value]
    errors: list[str] = []
    if unknown_fields:
        errors.append(f"{check_name} has unknown fields: {', '.join(unknown_fields)}")
    if missing_fields:
        errors.append(f"{check_name} is missing fields: {', '.join(missing_fields)}")
    if errors:
        return {}, errors

    normalized: dict[str, str] = {}
    non_string_fields: list[str] = []
    for field in EVIDENCE_RECORD_FIELDS:
        raw_field_value = raw_value.get(field)
        if not isinstance(raw_field_value, str):
            non_string_fields.append(field)
            continue
        normalized[field] = raw_field_value.strip()
    if non_string_fields:
        errors.append(f"{check_name} has non-string fields: {', '.join(non_string_fields)}")
        return {}, errors
    if _is_blank_evidence_record(normalized):
        return {field: "" for field in EVIDENCE_RECORD_FIELDS}, []
    if not _is_complete_evidence_record(normalized):
        empty_fields = [field for field in EVIDENCE_RECORD_FIELDS if not normalized.get(field, "")]
        return {}, [f"{check_name} has partial evidence fields: {', '.join(empty_fields)}"]
    if not _is_timezone_aware_iso_timestamp(normalized["verified_at"]):
        return {}, [f"{check_name}.verified_at must be an ISO 8601 timestamp with timezone"]
    return normalized, []


def _is_blank_evidence_record(record: dict[str, str]) -> bool:
    return all(not record.get(field, "").strip() for field in EVIDENCE_RECORD_FIELDS)


def _is_complete_evidence_record(record: dict[str, str]) -> bool:
    return all(
        isinstance(record.get(field), str) and bool(record[field].strip())
        for field in EVIDENCE_RECORD_FIELDS
    )


def _is_timezone_aware_iso_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _format_validation_errors(errors: list[str]) -> str:
    if len(errors) <= 3:
        return "; ".join(errors)
    shown_errors = "; ".join(errors[:3])
    return f"{shown_errors}; ... {len(errors) - 3} more error(s)"


def _probe_github_release(
    *,
    release_tag: str,
    verified_by: str,
    verified_at: str,
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    command = ["gh", "release", "view", release_tag, "--json", GITHUB_RELEASE_FIELDS]
    completed = runner(command)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return {
            "status": "release_view_failed",
            "command": command,
            "next_step": f"Fix `gh release view {release_tag}` before collecting release evidence.",
            "detail": detail or f"gh exited with status {completed.returncode}",
        }, None

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "status": "release_view_invalid_json",
            "command": command,
            "next_step": "Return valid JSON from gh release view.",
            "detail": str(exc),
        }, None

    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        assets = []
    asset_names = {
        str(asset.get("name", ""))
        for asset in assets
        if isinstance(asset, dict) and asset.get("name")
    }
    missing_assets = sorted(set(expected_release_assets(release_tag)) - asset_names)
    release_url = str(payload.get("url", ""))
    if payload.get("isDraft") is True:
        return {
            "status": "draft_release",
            "reference": release_url,
            "next_step": "Publish the GitHub release before using it as public launch evidence.",
        }, None
    if missing_assets:
        return {
            "status": "missing_assets",
            "reference": release_url,
            "missing_assets": missing_assets,
            "next_step": "Attach every required release asset, then rerun this collector.",
        }, None

    metadata_probe = _probe_github_release_metadata(
        release_tag=release_tag,
        release_url=release_url,
        expected_assets=expected_release_assets(release_tag),
        runner=runner,
    )
    if metadata_probe["status"] != "passed":
        return metadata_probe, None

    summary = (
        f"GitHub release {release_tag} contains required assets: "
        f"{', '.join(expected_release_assets(release_tag))}; manifest and "
        f"SHA256SUMS metadata cross-check {metadata_probe['payload_asset_count']} "
        "payload assets."
    )
    return {
        "status": "passed",
        "reference": release_url,
        "asset_count": len(asset_names),
        "payload_asset_count": metadata_probe["payload_asset_count"],
        "target_commitish": str(payload.get("targetCommitish", "")),
    }, _evidence_record(
        summary=summary,
        reference=release_url,
        verified_by=verified_by,
        verified_at=verified_at,
    )


def _probe_github_release_metadata(
    *,
    release_tag: str,
    release_url: str,
    expected_assets: list[str],
    runner: Runner,
) -> dict[str, Any]:
    try:
        repo_slug = _repo_slug_from_release_url(release_url)
    except ValueError as exc:
        return {
            "status": "missing_release_reference",
            "reference": release_url,
            "next_step": str(exc),
        }

    with tempfile.TemporaryDirectory(prefix="policynim-release-metadata.") as temp_dir:
        command = [
            "gh",
            "release",
            "download",
            release_tag,
            "--pattern",
            "RELEASE_MANIFEST.json",
            "--pattern",
            "SHA256SUMS",
            "--dir",
            temp_dir,
            "--repo",
            repo_slug,
        ]
        completed = runner(command)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            return {
                "status": "metadata_download_failed",
                "command": command,
                "reference": release_url,
                "download_dir": temp_dir,
                "next_step": (
                    "Download RELEASE_MANIFEST.json and SHA256SUMS from the "
                    "GitHub release before collecting release evidence."
                ),
                "detail": detail or f"gh exited with status {completed.returncode}",
            }

        manifest_path = Path(temp_dir) / "RELEASE_MANIFEST.json"
        checksums_path = Path(temp_dir) / "SHA256SUMS"
        missing_metadata = [
            path.name for path in (manifest_path, checksums_path) if not path.is_file()
        ]
        if missing_metadata:
            return {
                "status": "release_metadata_missing",
                "command": command,
                "reference": release_url,
                "download_dir": temp_dir,
                "missing_assets": missing_metadata,
                "next_step": (
                    "Ensure the release includes downloadable RELEASE_MANIFEST.json "
                    "and SHA256SUMS files."
                ),
            }

        errors = _validate_release_metadata_files(
            manifest_path=manifest_path,
            checksums_path=checksums_path,
            release_tag=release_tag,
            expected_assets=expected_assets,
        )
        if errors:
            return {
                "status": "release_metadata_invalid",
                "command": command,
                "reference": release_url,
                "download_dir": temp_dir,
                "next_step": (
                    "Regenerate RELEASE_MANIFEST.json and SHA256SUMS from the "
                    "same release asset directory, then rerun this collector."
                ),
                "detail": _format_validation_errors(errors),
            }

        return {
            "status": "passed",
            "command": command,
            "reference": release_url,
            "payload_asset_count": len(_release_payload_asset_names(expected_assets)),
        }


def _validate_release_metadata_files(
    *,
    manifest_path: Path,
    checksums_path: Path,
    release_tag: str,
    expected_assets: list[str],
) -> list[str]:
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"RELEASE_MANIFEST.json is not valid JSON: {type(exc).__name__}: {exc}"]
    if not isinstance(manifest, dict):
        return ["RELEASE_MANIFEST.json must contain a JSON object"]
    if manifest.get("schema_version") != "1":
        errors.append("RELEASE_MANIFEST.json schema_version must be 1")
    if manifest.get("release_tag") != release_tag:
        errors.append(f"RELEASE_MANIFEST.json release_tag must be {release_tag}")

    assets = manifest.get("assets")
    if not isinstance(assets, list):
        errors.append("RELEASE_MANIFEST.json assets must be a list")
        assets = []
    manifest_checksums: dict[str, str] = {}
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            errors.append(f"RELEASE_MANIFEST.json assets[{index}] must be an object")
            continue
        name = asset.get("name")
        sha256 = asset.get("sha256")
        size_bytes = asset.get("size_bytes")
        if not isinstance(name, str) or not name:
            errors.append(f"RELEASE_MANIFEST.json assets[{index}].name must be a string")
            continue
        if isinstance(sha256, str) and _is_sha256_digest(sha256):
            manifest_checksums[name] = sha256
        else:
            errors.append(f"RELEASE_MANIFEST.json asset {name} has invalid sha256")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
            errors.append(f"RELEASE_MANIFEST.json asset {name} has invalid size_bytes")

    try:
        checksums_text = checksums_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"SHA256SUMS could not be read: {type(exc).__name__}: {exc}"]
    checksum_entries, checksum_errors = _parse_sha256sums(checksums_text)
    errors.extend(checksum_errors)

    expected_payload_assets = _release_payload_asset_names(expected_assets)
    missing_manifest_assets = sorted(set(expected_payload_assets) - set(manifest_checksums))
    missing_checksum_assets = sorted(set(expected_payload_assets) - set(checksum_entries))
    if missing_manifest_assets:
        errors.append(
            "RELEASE_MANIFEST.json is missing assets: " + ", ".join(missing_manifest_assets)
        )
    if missing_checksum_assets:
        errors.append("SHA256SUMS is missing assets: " + ", ".join(missing_checksum_assets))

    extra_manifest_assets = sorted(set(manifest_checksums) - set(expected_payload_assets))
    extra_checksum_assets = sorted(set(checksum_entries) - set(expected_payload_assets))
    if extra_manifest_assets:
        errors.append(
            "RELEASE_MANIFEST.json has unexpected assets: " + ", ".join(extra_manifest_assets)
        )
    if extra_checksum_assets:
        errors.append("SHA256SUMS has unexpected assets: " + ", ".join(extra_checksum_assets))

    for name in expected_payload_assets:
        manifest_digest = manifest_checksums.get(name)
        checksum_digest = checksum_entries.get(name)
        if (
            manifest_digest is not None
            and checksum_digest is not None
            and manifest_digest != checksum_digest
        ):
            errors.append(f"{name} sha256 differs between RELEASE_MANIFEST.json and SHA256SUMS")
    return errors


def _parse_sha256sums(text: str) -> tuple[dict[str, str], list[str]]:
    entries: dict[str, str] = {}
    errors: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            errors.append(f"SHA256SUMS line {line_number} must contain digest and filename")
            continue
        digest, filename = parts
        if not _is_sha256_digest(digest):
            errors.append(f"SHA256SUMS line {line_number} has invalid sha256")
            continue
        filename = filename.strip()
        if not filename:
            errors.append(f"SHA256SUMS line {line_number} has an empty filename")
            continue
        entries[filename] = digest
    return entries, errors


def _release_payload_asset_names(expected_assets: list[str]) -> list[str]:
    return sorted(set(expected_assets) - set(RELEASE_METADATA_ASSET_NAMES))


def _is_sha256_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value.lower())


def _probe_github_artifact_attestation(
    *,
    release_tag: str,
    asset_name: str,
    release_url: str,
    verified_by: str,
    verified_at: str,
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    normalized_asset_name = asset_name.strip()
    if not normalized_asset_name:
        return {
            "status": "manual_required",
            "next_step": (
                "Pass --release-attestation-asset-name with a public release asset "
                "after the Release workflow has generated GitHub artifact attestations."
            ),
        }, None
    if normalized_asset_name not in expected_release_assets(release_tag):
        return {
            "status": "unknown_asset",
            "asset_name": normalized_asset_name,
            "next_step": (
                "Use one of the public release assets listed in RELEASE_MANIFEST.json, "
                "for example install.sh or the Linux standalone archive."
            ),
        }, None
    try:
        repo_slug = _repo_slug_from_release_url(release_url)
    except ValueError as exc:
        return {
            "status": "missing_release_reference",
            "asset_name": normalized_asset_name,
            "next_step": str(exc),
        }, None

    with tempfile.TemporaryDirectory(prefix="policynim-attestation-") as temp_dir:
        download_command = [
            "gh",
            "release",
            "download",
            release_tag,
            "--pattern",
            normalized_asset_name,
            "--dir",
            temp_dir,
            "--repo",
            repo_slug,
        ]
        completed_download = runner(download_command)
        if completed_download.returncode != 0:
            detail = (completed_download.stderr or completed_download.stdout).strip()
            return {
                "status": "download_failed",
                "command": download_command,
                "asset_name": normalized_asset_name,
                "download_dir": temp_dir,
                "next_step": "Download the release asset before verifying its attestation.",
                "detail": detail or f"gh exited with status {completed_download.returncode}",
            }, None

        asset_path = Path(temp_dir) / normalized_asset_name
        if not asset_path.is_file():
            return {
                "status": "download_missing_asset",
                "command": download_command,
                "asset_name": normalized_asset_name,
                "download_dir": temp_dir,
                "next_step": (
                    "Ensure the named release asset exists and gh release download "
                    "writes it to the requested directory."
                ),
            }, None

        verify_command = [
            "gh",
            "attestation",
            "verify",
            str(asset_path),
            "--repo",
            repo_slug,
            "--format",
            "json",
        ]
        completed_verify = runner(verify_command)
        if completed_verify.returncode != 0:
            detail = (completed_verify.stderr or completed_verify.stdout).strip()
            return {
                "status": "verification_failed",
                "command": verify_command,
                "asset_name": normalized_asset_name,
                "download_dir": temp_dir,
                "next_step": (
                    "Publish release attestations from the Release workflow, then "
                    "rerun gh attestation verify for this asset."
                ),
                "detail": detail or f"gh exited with status {completed_verify.returncode}",
            }, None
        try:
            attestation_payload = json.loads(completed_verify.stdout)
        except json.JSONDecodeError as exc:
            return {
                "status": "invalid_attestation_json",
                "command": verify_command,
                "asset_name": normalized_asset_name,
                "download_dir": temp_dir,
                "next_step": "Return valid JSON from gh attestation verify --format json.",
                "detail": str(exc),
            }, None
        if not isinstance(attestation_payload, list) or not attestation_payload:
            return {
                "status": "empty_attestation_result",
                "command": verify_command,
                "asset_name": normalized_asset_name,
                "download_dir": temp_dir,
                "next_step": (
                    "Use an asset with at least one verified GitHub artifact attestation."
                ),
            }, None
        subject_names = _attestation_subject_names(attestation_payload)
        if not subject_names:
            return {
                "status": "attestation_missing_subjects",
                "command": verify_command,
                "asset_name": normalized_asset_name,
                "download_dir": temp_dir,
                "next_step": (
                    "Use a gh attestation verify JSON result with at least one "
                    "verificationResult.statement.subject entry."
                ),
            }, None
        if normalized_asset_name not in subject_names:
            return {
                "status": "attestation_subject_mismatch",
                "command": verify_command,
                "asset_name": normalized_asset_name,
                "download_dir": temp_dir,
                "subject_names": subject_names,
                "next_step": (
                    "Use a gh attestation verify JSON result whose attested "
                    f"subjects include {normalized_asset_name}."
                ),
            }, None

        reference = f"{release_url}#{normalized_asset_name}"
        subject_count = len(subject_names)
        summary = (
            f"GitHub artifact attestation verifies for {normalized_asset_name} "
            f"from release {release_tag} with {subject_count} attested subject."
        )
        return {
            "status": "passed",
            "reference": reference,
            "asset_name": normalized_asset_name,
            "download_dir": temp_dir,
            "attestation_count": len(attestation_payload),
            "subject_count": subject_count,
            "subject_names": subject_names,
        }, _evidence_record(
            summary=summary,
            reference=reference,
            verified_by=verified_by,
            verified_at=verified_at,
        )


def _attestation_subject_names(attestation_payload: object) -> list[str]:
    if not isinstance(attestation_payload, list):
        return []
    subject_names: list[str] = []
    for attestation in attestation_payload:
        if not isinstance(attestation, dict):
            continue
        verification = attestation.get("verificationResult")
        if not isinstance(verification, dict):
            continue
        statement = verification.get("statement")
        if not isinstance(statement, dict):
            continue
        subjects = statement.get("subject")
        if not isinstance(subjects, list):
            continue
        for subject in subjects:
            if not isinstance(subject, dict):
                continue
            name = subject.get("name")
            if isinstance(name, str) and name:
                subject_names.append(name)
    return sorted(set(subject_names))


def _repo_slug_from_release_url(release_url: str) -> str:
    parsed = urllib.parse.urlparse(release_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc != "github.com" or len(path_parts) < 2:
        raise ValueError(
            "Collect GitHub release evidence first so the release URL can identify "
            "the owner/repository for gh attestation verify."
        )
    return f"{path_parts[0]}/{path_parts[1]}"


def _probe_pypi_project(
    *,
    release_tag: str,
    release_target_commitish: str,
    pypi_payload: dict[str, Any] | None,
    pypi_publish_run_url: str,
    verified_by: str,
    verified_at: str,
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    if pypi_payload is None:
        return {
            "status": "not_checked",
            "next_step": "Run the collector with PyPI network access to inspect the project.",
        }, None
    info = pypi_payload.get("info")
    if not isinstance(info, dict):
        return {
            "status": "error",
            "next_step": "PyPI response did not include project info.",
        }, None
    version = str(info.get("version", ""))
    project_url = str(info.get("project_url", "https://pypi.org/project/policynim/"))
    expected_version = release_tag.removeprefix("v")
    if version != expected_version:
        return {
            "status": "version_mismatch",
            "reference": project_url,
            "expected_version": expected_version,
            "actual_version": version,
            "next_step": "Publish or verify the matching PyPI version before adding evidence.",
        }, None
    expected_files = expected_pypi_distribution_files(release_tag)
    filenames = _pypi_release_file_names(pypi_payload, version)
    missing_files = [filename for filename in expected_files if filename not in filenames]
    if missing_files:
        return {
            "status": "missing_distribution_files",
            "reference": project_url,
            "version": version,
            "expected_files": expected_files,
            "filenames": filenames,
            "missing_files": missing_files,
            "next_step": (
                "Publish or wait for the PyPI release page to list the expected "
                "wheel and sdist before claiming package-install availability."
            ),
        }, None
    if pypi_publish_run_url.strip():
        return _probe_pypi_publish_run(
            pypi_publish_run_url=pypi_publish_run_url,
            project_url=project_url,
            release_target_commitish=release_target_commitish,
            version=version,
            filenames=filenames,
            verified_by=verified_by,
            verified_at=verified_at,
            runner=runner,
        )
    return {
        "status": "manual_required",
        "reference": project_url,
        "version": version,
        "file_count": len(filenames),
        "filenames": filenames,
        "next_step": (
            "PyPI project exists for the release version. Trusted publishing state "
            "is not exposed by public PyPI JSON; pass --pypi-publish-run-url "
            "with the successful Release workflow run before filling "
            "pypi_project evidence."
        ),
    }, None


def _probe_pypi_install_smoke(
    *,
    release_tag: str,
    requested: bool,
    verified_by: str,
    verified_at: str,
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    version = release_tag.removeprefix("v")
    if not requested:
        return {
            "status": "manual_required",
            "next_step": (
                "Run the collector with --pypi-install-smoke after the release "
                "version is available on PyPI."
            ),
        }, None

    with tempfile.TemporaryDirectory(prefix="policynim-pypi-smoke-") as temp_dir:
        venv_dir = Path(temp_dir) / "venv"
        python_path = _venv_executable(venv_dir, "python")
        policynim_path = _venv_executable(venv_dir, "policynim")
        steps = [
            ("python -m venv", [sys.executable, "-m", "venv", str(venv_dir)], False),
            (
                "python -m pip install --upgrade pip",
                [str(python_path), "-m", "pip", "install", "--upgrade", "pip"],
                False,
            ),
            (
                f"python -m pip install policynim=={version}",
                [str(python_path), "-m", "pip", "install", f"policynim=={version}"],
                False,
            ),
            *_installed_cli_first_run_smoke_steps(policynim_path),
        ]
        completed_labels: list[str] = []
        for label, command, expects_json in steps:
            completed = runner(command)
            if completed.returncode != 0:
                return _install_smoke_failure(
                    status="command_failed",
                    label=label,
                    completed=completed,
                    channel="PyPI",
                    rerun_flag="--pypi-install-smoke",
                ), None
            if expects_json:
                try:
                    parsed_json = json.loads(completed.stdout)
                except json.JSONDecodeError as exc:
                    return {
                        "status": "invalid_json",
                        "command": label,
                        "detail": str(exc),
                        "next_step": (
                            f"Rerun the public PyPI install smoke and inspect {label} output."
                        ),
                    }, None
                contract_errors = _installed_cli_first_run_json_contract_errors(
                    label=label,
                    payload=parsed_json,
                )
                if contract_errors:
                    return {
                        "status": "invalid_first_run_contract",
                        "command": label,
                        "detail": "; ".join(contract_errors),
                        "next_step": (
                            "Publish a new PyPI release built from the current "
                            "first-run contract, then rerun --pypi-install-smoke."
                        ),
                    }, None
            completed_labels.append(label)

    version_url = f"https://pypi.org/project/policynim/{version}/"
    return {
        "status": "passed",
        "reference": version_url,
        "version": version,
        "commands": completed_labels,
    }, _evidence_record(
        summary=(
            f"Clean PyPI install smoke passed for policynim=={version} with "
            "--help, primary command help, semantic first-run JSON, "
            "support-bundle hosted client_commands for Codex and Claude Code, "
            "doctor JSON, and local MCP config JSON."
        ),
        reference=version_url,
        verified_by=verified_by,
        verified_at=verified_at,
    )


def _probe_github_release_install_smoke(
    *,
    release_tag: str,
    requested: bool,
    verified_by: str,
    verified_at: str,
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    version = release_tag.removeprefix("v")
    release_url = f"https://github.com/nnennandukwe/policyNIM/releases/tag/{release_tag}"
    installer_url = (
        f"https://github.com/nnennandukwe/policyNIM/releases/download/{release_tag}/install.sh"
    )
    if not requested:
        return {
            "status": "manual_required",
            "next_step": (
                "Run the collector with --github-install-smoke after the release "
                "version is available on GitHub Releases."
            ),
        }, None

    if os.name == "nt":
        return {
            "status": "unsupported_platform",
            "next_step": (
                "--github-install-smoke currently verifies the Unix install.sh path. "
                "Run it from macOS or Linux, or attach reviewed Windows installer smoke evidence."
            ),
        }, None

    with tempfile.TemporaryDirectory(prefix="policynim-github-install-smoke-") as temp_dir:
        temp_path = Path(temp_dir)
        temp_home = temp_path / "home"
        temp_home.mkdir()
        installer_path = temp_path / "install.sh"
        policynim_path = temp_home / ".local" / "bin" / "policynim"
        steps = [
            (
                "download install.sh",
                ["curl", "-fsSL", "-o", str(installer_path), installer_url],
                False,
            ),
            (
                "install.sh",
                [
                    "sh",
                    "-c",
                    (
                        f"HOME={shlex.quote(str(temp_home))} "
                        f"POLICYNIM_VERSION={shlex.quote(version)} "
                        f"sh {shlex.quote(str(installer_path))}"
                    ),
                ],
                False,
            ),
            *_installed_cli_first_run_smoke_steps(policynim_path),
        ]
        completed_labels: list[str] = []
        for label, command, expects_json in steps:
            completed = runner(command)
            if completed.returncode != 0:
                return _install_smoke_failure(
                    status="command_failed",
                    label=label,
                    completed=completed,
                    channel="GitHub",
                    rerun_flag="--github-install-smoke",
                ), None
            if label == "install.sh":
                guidance_errors = _installer_guidance_contract_errors(completed.stdout)
                if guidance_errors:
                    return {
                        "status": "invalid_installer_guidance",
                        "command": label,
                        "detail": "; ".join(guidance_errors),
                        "next_step": (
                            "Publish a new GitHub release whose install.sh output "
                            "matches the current hosted and local first-run guidance, "
                            "then rerun --github-install-smoke."
                        ),
                    }, None
            if expects_json:
                try:
                    parsed_json = json.loads(completed.stdout)
                except json.JSONDecodeError as exc:
                    return {
                        "status": "invalid_json",
                        "command": label,
                        "detail": str(exc),
                        "next_step": (
                            f"Rerun the public GitHub installer smoke and inspect {label} output."
                        ),
                    }, None
                contract_errors = _installed_cli_first_run_json_contract_errors(
                    label=label,
                    payload=parsed_json,
                )
                if contract_errors:
                    return {
                        "status": "invalid_first_run_contract",
                        "command": label,
                        "detail": "; ".join(contract_errors),
                        "next_step": (
                            "Publish a new GitHub release built from the current "
                            "first-run contract, then rerun --github-install-smoke."
                        ),
                    }, None
            completed_labels.append(label)

    return {
        "status": "passed",
        "reference": release_url,
        "version": version,
        "installer_url": installer_url,
        "commands": completed_labels,
    }, _evidence_record(
        summary=(
            f"Clean GitHub release installer smoke passed for {release_tag} with "
            "install.sh guidance, --help, primary command help, semantic first-run "
            "JSON, support-bundle hosted client_commands for Codex and Claude Code, "
            "doctor JSON, and local MCP config JSON."
        ),
        reference=release_url,
        verified_by=verified_by,
        verified_at=verified_at,
    )


def _installed_cli_first_run_smoke_steps(
    policynim_path: Path,
) -> list[tuple[str, list[str], bool]]:
    return [
        ("policynim --help", [str(policynim_path), "--help"], False),
        ("policynim init --help", [str(policynim_path), "init", "--help"], False),
        ("policynim ingest --help", [str(policynim_path), "ingest", "--help"], False),
        (
            "policynim preflight --help",
            [str(policynim_path), "preflight", "--help"],
            False,
        ),
        (
            "policynim quickstart --format json",
            [str(policynim_path), "quickstart", "--format", "json"],
            True,
        ),
        (
            "policynim quickstart --target local-cli --format json",
            [
                str(policynim_path),
                "quickstart",
                "--target",
                "local-cli",
                "--format",
                "json",
            ],
            True,
        ),
        (
            "policynim quickstart --target local-mcp --format json",
            [
                str(policynim_path),
                "quickstart",
                "--target",
                "local-mcp",
                "--format",
                "json",
            ],
            True,
        ),
        (
            "policynim doctor --format json",
            [str(policynim_path), "doctor", "--format", "json"],
            True,
        ),
        ("policynim support-bundle", [str(policynim_path), "support-bundle"], True),
        (
            "policynim mcp-config --client codex --target local-stdio --format json",
            [
                str(policynim_path),
                "mcp-config",
                "--client",
                "codex",
                "--target",
                "local-stdio",
                "--format",
                "json",
            ],
            True,
        ),
        (
            "policynim mcp-config --client claude-code --target local-stdio --format json",
            [
                str(policynim_path),
                "mcp-config",
                "--client",
                "claude-code",
                "--target",
                "local-stdio",
                "--format",
                "json",
            ],
            True,
        ),
    ]


def _installer_guidance_contract_errors(stdout: str) -> list[str]:
    errors: list[str] = []
    for token in (
        "Run `policynim quickstart` to choose a first-run path.",
        "Hosted MCP does not require `policynim init` or `policynim ingest`.",
        "For local CLI or local MCP, run `policynim init` then `policynim ingest`.",
        "Run `policynim doctor` to inspect first-run setup.",
        "Run `policynim support-bundle` before opening an issue.",
    ):
        if token not in stdout:
            errors.append(f"install.sh output must include: {token}")
    return errors


def _installed_cli_first_run_json_contract_errors(
    *,
    label: str,
    payload: object,
) -> list[str]:
    if not isinstance(payload, dict):
        return [f"{label} output must be a JSON object"]
    if label == "policynim quickstart --format json":
        return _hosted_quickstart_contract_errors(payload, label="hosted quickstart")
    if label == "policynim quickstart --target local-cli --format json":
        return _quickstart_target_contract_errors(
            payload,
            expected_target="local-cli",
            expected_requires_local_setup=True,
            label="local CLI quickstart",
        )
    if label == "policynim quickstart --target local-mcp --format json":
        errors = _quickstart_target_contract_errors(
            payload,
            expected_target="local-mcp",
            expected_requires_local_setup=True,
            label="local MCP quickstart",
        )
        if payload.get("local_launch_mode") != "installed-cli":
            errors.append("local MCP quickstart local_launch_mode must be 'installed-cli'")
        return errors
    if label == "policynim support-bundle":
        return _support_bundle_first_run_contract_errors(payload)
    if label in (
        "policynim mcp-config --client codex --target local-stdio --format json",
        "policynim mcp-config --client claude-code --target local-stdio --format json",
    ):
        return _local_mcp_config_contract_errors(payload, label=label)
    return []


def _quickstart_target_contract_errors(
    payload: dict[Any, Any],
    *,
    expected_target: str,
    expected_requires_local_setup: bool,
    label: str,
) -> list[str]:
    errors: list[str] = []
    if payload.get("target") != expected_target:
        errors.append(f"{label} target must be {expected_target!r}")
    if payload.get("requires_local_setup") is not expected_requires_local_setup:
        errors.append(f"{label} requires_local_setup must be {expected_requires_local_setup!r}")
    if payload.get("calls_external_services") is not False:
        errors.append(f"{label} calls_external_services must be False")
    errors.extend(_agent_workflows_contract_errors(payload, label=f"{label}.agent_workflows"))
    return errors


def _hosted_quickstart_contract_errors(
    payload: dict[Any, Any],
    *,
    label: str,
) -> list[str]:
    errors = _quickstart_target_contract_errors(
        payload,
        expected_target="hosted-mcp",
        expected_requires_local_setup=False,
        label=label,
    )
    errors.extend(
        _hosted_client_commands_contract_errors(
            payload,
            label=f"{label}.client_commands",
        )
    )
    errors.extend(_hosted_quickstart_token_flow_contract_errors(payload, label=label))
    return errors


def _hosted_quickstart_token_flow_contract_errors(
    payload: dict[Any, Any],
    *,
    label: str,
) -> list[str]:
    errors: list[str] = []
    hosted_url = payload.get("hosted_url")
    beta_portal_url = payload.get("beta_portal_url")
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

    steps = _string_list(payload.get("steps"))
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


def _support_bundle_first_run_contract_errors(payload: dict[Any, Any]) -> list[str]:
    first_run = _object_dict(payload.get("first_run"))
    if first_run is None:
        return ["support-bundle first_run must be a JSON object"]
    targets = _object_dict(first_run.get("targets"))
    if targets is None:
        return ["support-bundle first_run.targets must be a JSON object"]
    hosted_mcp = _object_dict(targets.get("hosted_mcp"))
    if hosted_mcp is None:
        return ["support-bundle first_run.targets.hosted_mcp must be a JSON object"]

    errors: list[str] = []
    quickstart_command = hosted_mcp.get("quickstart_command")
    if quickstart_command != "policynim quickstart --target hosted-mcp --format json":
        errors.append(
            "first_run.targets.hosted_mcp.quickstart_command must use the installed "
            "hosted quickstart command"
        )
    errors.extend(
        _hosted_all_client_commands_contract_errors(
            hosted_mcp,
            label="first_run.targets.hosted_mcp.client_commands",
        )
    )
    errors.extend(
        _hosted_quickstart_token_flow_contract_errors(
            hosted_mcp,
            label="first_run.targets.hosted_mcp",
        )
    )
    errors.extend(
        _agent_workflows_contract_errors(
            hosted_mcp,
            label="first_run.targets.hosted_mcp.agent_workflows",
        )
    )
    return errors


def _hosted_client_commands_contract_errors(
    payload: dict[Any, Any],
    *,
    label: str,
) -> list[str]:
    client_commands = _string_list(payload.get("client_commands"))
    if client_commands is None or not client_commands:
        return [f"{label} must be a non-empty string list"]
    command_text = " ".join(client_commands)
    errors: list[str] = []
    for token in ("/mcp", "POLICYNIM_TOKEN"):
        if token not in command_text:
            errors.append(f"{label} must include {token!r}")
    if "<generated-beta-token>" in command_text:
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
    payload: dict[Any, Any],
    *,
    label: str,
) -> list[str]:
    errors = _hosted_client_commands_contract_errors(payload, label=label)
    client_commands = _string_list(payload.get("client_commands"))
    if client_commands is None:
        return errors

    command_text = " ".join(client_commands)
    if "codex mcp add policynim" not in command_text:
        errors.append(f"{label} must include a Codex MCP add command")
    if "claude mcp add" not in command_text:
        errors.append(f"{label} must include a Claude Code MCP add command")
    return errors


def _agent_workflows_contract_errors(
    payload: dict[Any, Any],
    *,
    label: str,
) -> list[str]:
    workflows = payload.get("agent_workflows")
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
        "policy_preflight",
        "Before editing",
        "cited constraints",
        "insufficient_context",
        "policy_search",
        "cited policy lines",
        "Verify MCP tool availability",
        "before starting implementation",
    ):
        if token not in workflow_text:
            errors.append(f"{label} must mention {token!r}")
    return errors


def _local_mcp_config_contract_errors(
    payload: dict[Any, Any],
    *,
    label: str,
) -> list[str]:
    errors: list[str] = []
    if payload.get("target") != "local-stdio":
        errors.append(f"{label} target must be 'local-stdio'")
    if payload.get("server_name") != "policynim":
        errors.append(f"{label} server_name must be 'policynim'")
    if payload.get("local_launch_mode") != "installed-cli":
        errors.append(f"{label} local_launch_mode must be 'installed-cli'")
    if "repo_root" in payload:
        errors.append(f"{label} must not include repo_root for public PyPI installs")
    return errors


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return value


def _object_dict(value: object) -> dict[Any, Any] | None:
    if not isinstance(value, dict):
        return None
    return value


def _install_smoke_failure(
    *,
    status: str,
    label: str,
    completed: subprocess.CompletedProcess[str],
    channel: str,
    rerun_flag: str,
) -> dict[str, Any]:
    detail = (completed.stderr or completed.stdout).strip()
    if not detail:
        detail = f"command exited with status {completed.returncode}"
    if label.startswith("policynim ") and "No such command" in detail:
        return {
            "status": "missing_first_run_command",
            "command": label,
            "detail": detail,
            "next_step": (
                f"Publish a new {channel} release built from the current CLI, then "
                f"rerun {rerun_flag}."
            ),
        }
    return {
        "status": status,
        "command": label,
        "detail": detail,
        "next_step": (f"Rerun the public {channel} install smoke and inspect the failing command."),
    }


def _venv_executable(venv_dir: Path, name: str) -> Path:
    if os.name == "nt":
        suffix = ".exe"
        return venv_dir / "Scripts" / f"{name}{suffix}"
    return venv_dir / "bin" / name


def expected_pypi_distribution_files(release_tag: str) -> list[str]:
    version = release_tag.removeprefix("v")
    return [
        f"{PYPI_PROJECT_NAME}-{version}-py3-none-any.whl",
        f"{PYPI_PROJECT_NAME}-{version}.tar.gz",
    ]


def _pypi_release_file_names(pypi_payload: dict[str, Any], version: str) -> list[str]:
    releases = pypi_payload.get("releases")
    if not isinstance(releases, dict):
        return []
    release_files = releases.get(version)
    if not isinstance(release_files, list):
        return []
    filenames: list[str] = []
    for release_file in release_files:
        if not isinstance(release_file, dict):
            continue
        filename = release_file.get("filename")
        if isinstance(filename, str) and filename:
            filenames.append(filename)
    return sorted(set(filenames))


def _probe_pypi_publish_run(
    *,
    pypi_publish_run_url: str,
    project_url: str,
    release_target_commitish: str,
    version: str,
    filenames: list[str],
    verified_by: str,
    verified_at: str,
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    raw_reference = pypi_publish_run_url.strip()
    try:
        run_id = _extract_github_run_id(raw_reference)
    except ValueError as exc:
        return {
            "status": "invalid_run_url",
            "reference": raw_reference,
            "project_url": project_url,
            "version": version,
            "next_step": str(exc),
        }, None

    command = ["gh", "run", "view", run_id, "--json", GITHUB_RUN_FIELDS]
    completed = runner(command)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return {
            "status": "error",
            "command": command,
            "project_url": project_url,
            "version": version,
            "next_step": "Authenticate gh and verify the PyPI publish run URL.",
            "detail": detail or f"gh exited with status {completed.returncode}",
        }, None

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "command": command,
            "project_url": project_url,
            "version": version,
            "next_step": "Return valid JSON from gh run view.",
            "detail": str(exc),
        }, None

    workflow_name = str(payload.get("workflowName", ""))
    if workflow_name != "Release":
        return {
            "status": "wrong_workflow",
            "reference": raw_reference,
            "workflow_name": workflow_name,
            "project_url": project_url,
            "version": version,
            "next_step": "Use a GitHub Actions run from the Release workflow.",
        }, None
    if payload.get("event") not in {"push", "workflow_dispatch"}:
        return {
            "status": "wrong_event",
            "reference": raw_reference,
            "event": payload.get("event"),
            "project_url": project_url,
            "version": version,
            "next_step": "Use a tag push or manual Release workflow run.",
        }, None
    if payload.get("status") != "completed" or payload.get("conclusion") != "success":
        return {
            "status": "run_not_successful",
            "reference": raw_reference,
            "run_status": payload.get("status"),
            "conclusion": payload.get("conclusion"),
            "project_url": project_url,
            "version": version,
            "next_step": (
                "Use a Release workflow run with completed status and success conclusion."
            ),
        }, None
    expected_head_sha = release_target_commitish.strip()
    actual_head_sha = str(payload.get("headSha", "")).strip()
    if not expected_head_sha:
        return {
            "status": "release_sha_missing",
            "reference": raw_reference,
            "project_url": project_url,
            "version": version,
            "next_step": (
                "Collect GitHub release evidence first so the trusted-publish run "
                "can be matched to the release target commit."
            ),
        }, None
    if actual_head_sha != expected_head_sha:
        return {
            "status": "release_sha_mismatch",
            "reference": raw_reference,
            "project_url": project_url,
            "version": version,
            "expected_head_sha": expected_head_sha,
            "actual_head_sha": actual_head_sha,
            "next_step": (
                "Use a Release workflow run from the same commit as the GitHub "
                "release target before filling PyPI trusted-publishing evidence."
            ),
        }, None

    jobs = payload.get("jobs")
    publish_job = _find_job(jobs, "publish-pypi")
    if not _is_successful_job(publish_job):
        result: dict[str, Any] = {
            "status": "job_not_successful",
            "reference": raw_reference,
            "project_url": project_url,
            "version": version,
            "next_step": "Use a Release workflow run where the publish-pypi job passed.",
        }
        if publish_job is None:
            result["available_jobs"] = _job_summaries(jobs)
        else:
            result.update(
                {
                    "job_name": publish_job.get("name"),
                    "job_status": publish_job.get("status"),
                    "job_conclusion": publish_job.get("conclusion"),
                }
            )
        return result, None

    reference = str(payload.get("url", "")) or raw_reference
    file_count = len(filenames)
    summary = (
        f"PyPI project policynim version {version} exposes {file_count} release "
        f"files and Release run {run_id} completed publish-pypi successfully "
        f"with trusted publishing for release commit {expected_head_sha}."
    )
    return {
        "status": "passed",
        "reference": reference,
        "project_url": project_url,
        "version": version,
        "file_count": file_count,
        "filenames": filenames,
        "run_id": run_id,
        "head_sha": actual_head_sha,
    }, _evidence_record(
        summary=summary,
        reference=reference,
        verified_by=verified_by,
        verified_at=verified_at,
    )


def _probe_github_labels(
    *,
    repo_root: Path,
    verified_by: str,
    verified_at: str,
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    labels_file = repo_root / ".github" / "labels.yml"
    try:
        desired = load_label_taxonomy(labels_file)
    except LabelSyncError as exc:
        return {
            "status": "error",
            "next_step": "Restore .github/labels.yml before collecting label evidence.",
            "detail": str(exc),
        }, None

    command = ["gh", "label", "list", "--json", "name,color,description", "--limit", "1000"]
    completed = runner(command)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return {
            "status": "error",
            "command": command,
            "next_step": "Authenticate gh and rerun the collector from the repository root.",
            "detail": detail or f"gh exited with status {completed.returncode}",
        }, None
    try:
        raw_existing = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "command": command,
            "next_step": "Return valid JSON from gh label list.",
            "detail": str(exc),
        }, None
    if not isinstance(raw_existing, list):
        raw_existing = []
    existing = [
        {
            "name": str(label.get("name", "")),
            "color": str(label.get("color", "")),
            "description": str(label.get("description", "")),
        }
        for label in raw_existing
        if isinstance(label, dict)
    ]
    plan = plan_label_sync(desired, existing)
    drift = [entry for entry in plan if entry["action"] != "noop"]
    if drift:
        return {
            "status": "label_drift",
            "missing_or_changed": [entry["name"] for entry in drift],
            "next_step": (
                "Run `gh auth status`, then "
                "`uv run python scripts/sync_github_labels.py --live --format json`, "
                "then `uv run python scripts/sync_github_labels.py --apply --format json`, "
                "then rerun this collector."
            ),
        }, None
    return {
        "status": "passed",
        "label_count": len(desired),
        "reference": "gh label list --json name,color,description --limit 1000",
    }, _evidence_record(
        summary="GitHub labels match .github/labels.yml.",
        reference="gh label list --json name,color,description --limit 1000",
        verified_by=verified_by,
        verified_at=verified_at,
    )


def _probe_github_topics(
    *,
    repo_root: Path,
    verified_by: str,
    verified_at: str,
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    topics_file = repo_root / ".github" / "topics.yml"
    try:
        desired = load_topic_taxonomy(topics_file)
    except TopicSyncError as exc:
        return {
            "status": "error",
            "next_step": "Restore .github/topics.yml before collecting topic evidence.",
            "detail": str(exc),
        }, None

    command = ["gh", "repo", "view", "--json", "repositoryTopics,nameWithOwner"]
    completed = runner(command)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return {
            "status": "error",
            "command": command,
            "next_step": "Authenticate gh and rerun the collector from the repository root.",
            "detail": detail or f"gh exited with status {completed.returncode}",
        }, None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "command": command,
            "next_step": "Return valid JSON from gh repo view.",
            "detail": str(exc),
        }, None
    if not isinstance(payload, dict):
        payload = {}
    raw_topics = payload.get("repositoryTopics")
    if not isinstance(raw_topics, list):
        raw_topics = []
    existing = [
        str(topic.get("name", ""))
        for topic in raw_topics
        if isinstance(topic, dict) and topic.get("name")
    ]
    plan = plan_topic_sync(desired, existing)
    drift = [entry for entry in plan if entry["action"] != "noop"]
    if drift:
        return {
            "status": "topic_drift",
            "missing_or_changed": [entry["topic"] for entry in drift],
            "next_step": (
                "Run `gh auth status`, then "
                "`uv run python scripts/sync_github_topics.py --live --format json`, "
                "then `uv run python scripts/sync_github_topics.py --apply --format json`, "
                "then rerun this collector."
            ),
        }, None
    return {
        "status": "passed",
        "topic_count": len(desired),
        "reference": "gh repo view --json repositoryTopics,nameWithOwner",
    }, _evidence_record(
        summary="GitHub repository topics match .github/topics.yml.",
        reference="gh repo view --json repositoryTopics,nameWithOwner",
        verified_by=verified_by,
        verified_at=verified_at,
    )


def _probe_hosted_mcp_domain(
    *,
    hosted_mcp_url: str,
    verified_by: str,
    verified_at: str,
    urlopen: UrlOpener,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    raw_url = hosted_mcp_url.strip()
    if not raw_url:
        return {
            "status": "manual_required",
            "next_step": _manual_next_step("hosted_mcp_domain"),
        }, None

    try:
        mcp_url = _normalize_hosted_mcp_url(raw_url)
    except ValueError as exc:
        return {
            "status": "invalid_url",
            "reference": raw_url,
            "next_step": str(exc),
        }, None

    health_url = _same_origin_url(mcp_url, "/healthz")
    health_status, health_payload, health_error = _fetch_json(
        health_url,
        urlopen=urlopen,
        timeout=15,
    )
    if health_error:
        return {
            "status": "error",
            "reference": health_url,
            "next_step": "Fix hosted /healthz before collecting hosted MCP evidence.",
            "detail": health_error,
        }, None
    if health_status != 200:
        return {
            "status": "healthz_unhealthy",
            "reference": health_url,
            "status_code": health_status,
            "next_step": "Hosted /healthz must return 200 before launch evidence is valid.",
        }, None
    if not isinstance(health_payload, dict):
        return {
            "status": "invalid_healthz_payload",
            "reference": health_url,
            "next_step": "Hosted /healthz must return a JSON object.",
        }, None

    row_count = health_payload.get("row_count")
    health_mcp_url = str(health_payload.get("mcp_url", ""))
    if (
        health_payload.get("ready") is not True
        or health_payload.get("status") != "ok"
        or not isinstance(row_count, int)
        or isinstance(row_count, bool)
        or row_count <= 0
        or health_mcp_url != mcp_url
    ):
        return {
            "status": "healthz_not_ready",
            "reference": health_url,
            "next_step": (
                "Hosted /healthz must report ready=true, status=ok, row_count > 0, "
                "and an mcp_url matching the requested /mcp endpoint."
            ),
            "payload": health_payload,
        }, None

    mcp_request = urllib.request.Request(
        mcp_url,
        headers={
            "Accept": "text/event-stream",
            "Authorization": "Bearer invalid-token",
        },
    )
    mcp_status, _, mcp_error = _fetch_json(mcp_request, urlopen=urlopen, timeout=15)
    if mcp_error:
        return {
            "status": "error",
            "reference": mcp_url,
            "next_step": "Fix hosted /mcp invalid-token probing before collecting evidence.",
            "detail": mcp_error,
        }, None
    if mcp_status != 401:
        return {
            "status": "mcp_auth_not_enforced",
            "reference": mcp_url,
            "status_code": mcp_status,
            "next_step": "Hosted /mcp must return 401 for an invalid bearer token.",
        }, None

    origin = _same_origin_url(mcp_url, "")
    summary = (
        f"Hosted MCP domain {origin} reports ready /healthz with row_count {row_count} "
        "and rejects invalid bearer tokens on /mcp."
    )
    return {
        "status": "passed",
        "reference": health_url,
        "row_count": row_count,
        "mcp_url": mcp_url,
    }, _evidence_record(
        summary=summary,
        reference=health_url,
        verified_by=verified_by,
        verified_at=verified_at,
    )


def _probe_hosted_smoke_run(
    *,
    hosted_smoke_run_url: str,
    release_target_commitish: str,
    verified_by: str,
    verified_at: str,
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    raw_reference = hosted_smoke_run_url.strip()
    if not raw_reference:
        return {
            "status": "manual_required",
            "next_step": _manual_next_step("hosted_beta_live_smoke"),
        }, None

    try:
        run_id = _extract_github_run_id(raw_reference)
    except ValueError as exc:
        return {
            "status": "invalid_run_url",
            "reference": raw_reference,
            "next_step": str(exc),
        }, None

    command = ["gh", "run", "view", run_id, "--json", GITHUB_RUN_FIELDS]
    completed = runner(command)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return {
            "status": "error",
            "command": command,
            "next_step": "Authenticate gh and verify the Hosted Beta Smoke run URL.",
            "detail": detail or f"gh exited with status {completed.returncode}",
        }, None

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "command": command,
            "next_step": "Return valid JSON from gh run view.",
            "detail": str(exc),
        }, None

    workflow_name = str(payload.get("workflowName", ""))
    if workflow_name != "Hosted Beta Smoke":
        return {
            "status": "wrong_workflow",
            "reference": raw_reference,
            "workflow_name": workflow_name,
            "next_step": "Use a GitHub Actions run from the Hosted Beta Smoke workflow.",
        }, None
    if payload.get("event") != "workflow_dispatch":
        return {
            "status": "wrong_event",
            "reference": raw_reference,
            "event": payload.get("event"),
            "next_step": "Use a manually dispatched Hosted Beta Smoke run.",
        }, None
    if payload.get("status") != "completed" or payload.get("conclusion") != "success":
        return {
            "status": "run_not_successful",
            "reference": raw_reference,
            "run_status": payload.get("status"),
            "conclusion": payload.get("conclusion"),
            "next_step": (
                "Use a Hosted Beta Smoke run with completed status and success conclusion."
            ),
        }, None
    expected_head_sha = release_target_commitish.strip()
    actual_head_sha = str(payload.get("headSha", "")).strip()
    if not expected_head_sha:
        return {
            "status": "release_sha_missing",
            "reference": raw_reference,
            "next_step": (
                "Collect GitHub release evidence first so the hosted smoke run "
                "can be matched to the release target commit."
            ),
        }, None
    if actual_head_sha != expected_head_sha:
        return {
            "status": "release_sha_mismatch",
            "reference": raw_reference,
            "expected_head_sha": expected_head_sha,
            "actual_head_sha": actual_head_sha,
            "next_step": (
                "Use a Hosted Beta Smoke run from the same commit as the GitHub "
                "release target before filling hosted smoke evidence."
            ),
        }, None

    jobs = payload.get("jobs")
    if not _has_successful_hosted_smoke_job(jobs):
        return {
            "status": "job_not_successful",
            "reference": raw_reference,
            "next_step": "Use a Hosted Beta Smoke run where the hosted-smoke job passed.",
        }, None

    artifact_probe = _download_and_validate_hosted_smoke_artifact(
        run_id=run_id,
        runner=runner,
    )
    if artifact_probe["status"] != "passed":
        artifact_probe["reference"] = raw_reference
        return artifact_probe, None

    reference = str(payload.get("url", "")) or raw_reference
    test_count = artifact_probe["test_count"]
    summary = (
        f"Hosted Beta Smoke run {run_id} completed successfully with "
        f"{HOSTED_SMOKE_ARTIFACT_NAME}/{HOSTED_SMOKE_JUNIT_FILENAME} covering "
        f"{test_count} live MCP checks from release commit {expected_head_sha}."
    )
    return {
        "status": "passed",
        "reference": reference,
        "run_id": run_id,
        "workflow_name": workflow_name,
        "head_sha": actual_head_sha,
        "artifact": HOSTED_SMOKE_ARTIFACT_NAME,
        "junit_file": HOSTED_SMOKE_JUNIT_FILENAME,
        "test_count": test_count,
    }, _evidence_record(
        summary=summary,
        reference=reference,
        verified_by=verified_by,
        verified_at=verified_at,
    )


def _download_and_validate_hosted_smoke_artifact(
    *,
    run_id: str,
    runner: Runner,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="policynim-hosted-smoke.") as download_tmp:
        download_dir = Path(download_tmp)
        command = [
            "gh",
            "run",
            "download",
            run_id,
            "--name",
            HOSTED_SMOKE_ARTIFACT_NAME,
            "--dir",
            str(download_dir),
        ]
        completed = runner(command)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            return {
                "status": "artifact_download_failed",
                "command": command,
                "artifact": HOSTED_SMOKE_ARTIFACT_NAME,
                "next_step": (
                    "Download the hosted-smoke-evidence artifact from the Hosted "
                    "Beta Smoke run before collecting hosted smoke evidence."
                ),
                "detail": detail or f"gh exited with status {completed.returncode}",
            }

        candidates = sorted(download_dir.rglob(HOSTED_SMOKE_JUNIT_FILENAME))
        if not candidates:
            return {
                "status": "junit_missing",
                "artifact": HOSTED_SMOKE_ARTIFACT_NAME,
                "junit_file": HOSTED_SMOKE_JUNIT_FILENAME,
                "next_step": (
                    "Use a Hosted Beta Smoke run that uploaded "
                    f"{HOSTED_SMOKE_ARTIFACT_NAME}/{HOSTED_SMOKE_JUNIT_FILENAME}."
                ),
            }
        return _validate_hosted_smoke_junit(candidates[0])


def _validate_hosted_smoke_junit(junit_path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(junit_path).getroot()
    except (OSError, ET.ParseError) as exc:
        return {
            "status": "junit_invalid",
            "artifact": HOSTED_SMOKE_ARTIFACT_NAME,
            "junit_file": HOSTED_SMOKE_JUNIT_FILENAME,
            "next_step": "Use a parseable Hosted Beta Smoke JUnit XML artifact.",
            "detail": str(exc),
        }

    testcases = [element for element in root.iter() if _xml_local_name(element.tag) == "testcase"]
    present_tests = {
        str(testcase.attrib.get("name", ""))
        for testcase in testcases
        if testcase.attrib.get("name")
    }
    missing_tests = [
        test_name for test_name in expected_hosted_smoke_tests() if test_name not in present_tests
    ]
    if missing_tests:
        return {
            "status": "junit_missing_tests",
            "artifact": HOSTED_SMOKE_ARTIFACT_NAME,
            "junit_file": HOSTED_SMOKE_JUNIT_FILENAME,
            "missing_tests": missing_tests,
            "next_step": (
                "Use a Hosted Beta Smoke JUnit artifact that includes every "
                "required live MCP smoke test."
            ),
        }

    unsuccessful_tests = [
        str(testcase.attrib.get("name", ""))
        for testcase in testcases
        if str(testcase.attrib.get("name", "")) in expected_hosted_smoke_tests()
        and any(
            _xml_local_name(child.tag) in {"failure", "error", "skipped"}
            for child in list(testcase)
        )
    ]
    if unsuccessful_tests:
        return {
            "status": "junit_not_successful",
            "artifact": HOSTED_SMOKE_ARTIFACT_NAME,
            "junit_file": HOSTED_SMOKE_JUNIT_FILENAME,
            "unsuccessful_tests": unsuccessful_tests,
            "next_step": "Use a Hosted Beta Smoke JUnit artifact where every live test passed.",
        }

    return {
        "status": "passed",
        "artifact": HOSTED_SMOKE_ARTIFACT_NAME,
        "junit_file": HOSTED_SMOKE_JUNIT_FILENAME,
        "test_count": len(expected_hosted_smoke_tests()),
    }


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _probe_mcp_client_evidence_file(
    *,
    mcp_client_evidence_file: Path | None,
    verified_by: str,
    verified_at: str,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    if mcp_client_evidence_file is None:
        return {
            "status": "manual_required",
            "next_step": _manual_next_step("real_mcp_client_session"),
        }, None
    try:
        payload = json.loads(mcp_client_evidence_file.read_text(encoding="utf-8"))
    except OSError as exc:
        return {
            "status": "error",
            "reference": str(mcp_client_evidence_file),
            "next_step": "Read the MCP client-session evidence JSON file.",
            "detail": str(exc),
        }, None
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "reference": str(mcp_client_evidence_file),
            "next_step": "Provide valid JSON for MCP client-session evidence.",
            "detail": str(exc),
        }, None
    if not isinstance(payload, dict):
        return {
            "status": "invalid_evidence",
            "reference": str(mcp_client_evidence_file),
            "next_step": "MCP client-session evidence must be a JSON object.",
        }, None

    validation_error = _validate_mcp_client_evidence_payload(payload)
    if validation_error is not None:
        return {
            "status": validation_error["status"],
            "reference": str(mcp_client_evidence_file),
            "next_step": validation_error["next_step"],
        }, None

    client = str(payload["client"])
    transport = str(payload["transport"])
    reference = str(payload["reference"])
    client_label = "Codex" if client == "codex" else "Claude Code"
    summary = (
        f"{client_label} loaded the policynim MCP server over {transport} using a "
        "reviewed setup command, listed policy_preflight and policy_search, and "
        "called policy_preflight."
    )
    return {
        "status": "passed",
        "reference": reference,
        "client": client,
        "transport": transport,
    }, _evidence_record(
        summary=summary,
        reference=reference,
        verified_by=verified_by,
        verified_at=verified_at,
    )


def _validate_mcp_client_evidence_payload(payload: dict[str, Any]) -> dict[str, str] | None:
    client = payload.get("client")
    if client not in {"codex", "claude-code"}:
        return {
            "status": "unsupported_client",
            "next_step": "Use client 'codex' or 'claude-code' in MCP client evidence.",
        }
    transport = payload.get("transport")
    if transport not in {"hosted-http", "local-stdio"}:
        return {
            "status": "unsupported_transport",
            "next_step": "Use transport 'hosted-http' or 'local-stdio' in MCP client evidence.",
        }
    if payload.get("server_name") != "policynim":
        return {
            "status": "wrong_server",
            "next_step": "MCP client evidence must show the policynim server entry.",
        }
    reference = payload.get("reference")
    if not isinstance(reference, str) or not reference.strip():
        return {
            "status": "missing_reference",
            "next_step": "MCP client evidence must include a non-empty sanitized reference.",
        }
    if _contains_placeholder_reference(reference):
        return {
            "status": "placeholder_reference",
            "next_step": (
                "MCP client evidence must include a real, sanitized reference "
                "instead of an example or placeholder value."
            ),
        }
    setup_error = _validate_mcp_client_setup_command(
        client=str(client),
        transport=str(transport),
        setup_command=payload.get("setup_command"),
    )
    if setup_error is not None:
        return setup_error
    tools = payload.get("tools")
    if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools):
        return {
            "status": "invalid_tools",
            "next_step": "MCP client evidence must include a tools string list.",
        }
    missing_tools = [tool for tool in _expected_mcp_tools() if tool not in tools]
    if missing_tools:
        return {
            "status": "missing_tools",
            "next_step": f"MCP client evidence is missing tools: {', '.join(missing_tools)}.",
        }
    if payload.get("called_tool") != "policy_preflight":
        return {
            "status": "preflight_not_called",
            "next_step": "MCP client evidence must show a policy_preflight call.",
        }
    if payload.get("secrets_included") is not False:
        return {
            "status": "secrets_included",
            "next_step": (
                "Attach a redacted MCP client-session reference with secrets_included=false."
            ),
        }
    return None


def _validate_mcp_client_setup_command(
    *,
    client: str,
    transport: str,
    setup_command: object,
) -> dict[str, str] | None:
    if not isinstance(setup_command, str) or not setup_command.strip():
        return {
            "status": "missing_setup_command",
            "next_step": (
                "MCP client evidence must include the secret-safe setup command "
                "used to add the policynim MCP server."
            ),
        }

    normalized = " ".join(setup_command.split())
    if _contains_placeholder_reference(normalized):
        return {
            "status": "placeholder_setup_command",
            "next_step": (
                "MCP client evidence must include a real setup command instead "
                "of a placeholder hosted URL, example reference, or TODO value."
            ),
        }

    secret_markers = (
        "<generated-beta-token>",
        "<issued-beta-token>",
        "generated-beta-token",
        "issued-beta-token",
        "POLICYNIM_TOKEN=",
        "POLICYNIM_BETA_MCP_TOKEN=",
    )
    if any(marker in normalized for marker in secret_markers):
        return {
            "status": "setup_command_secrets_included",
            "next_step": (
                "Use a redacted setup command with token environment-variable "
                "references instead of token values."
            ),
        }

    missing_tokens = _mcp_client_setup_command_missing_tokens(
        client=client,
        transport=transport,
        normalized_command=normalized,
    )
    if missing_tokens:
        return {
            "status": "setup_command_mismatch",
            "next_step": (
                "MCP client setup command does not match the declared client and "
                f"transport. Missing: {', '.join(missing_tokens)}."
            ),
        }
    return None


def _mcp_client_setup_command_missing_tokens(
    *,
    client: str,
    transport: str,
    normalized_command: str,
) -> list[str]:
    if client == "codex" and transport == "hosted-http":
        required = (
            "codex mcp add",
            "policynim",
            "--url",
            "/mcp",
            "--bearer-token-env-var",
            "POLICYNIM_TOKEN",
        )
    elif client == "claude-code" and transport == "hosted-http":
        required = (
            "claude mcp add",
            "--transport http",
            "policynim",
            "/mcp",
            "--header",
            "Authorization: Bearer $POLICYNIM_TOKEN",
        )
    elif client == "codex" and transport == "local-stdio":
        required = (
            "codex mcp add",
            "policynim",
            "NVIDIA_API_KEY",
            "mcp",
            "--transport",
            "stdio",
        )
    else:
        required = (
            "claude mcp add-json",
            "policynim",
            "stdio",
            "command",
            "args",
            "NVIDIA_API_KEY",
        )
    return [token for token in required if token not in normalized_command]


def _contains_placeholder_reference(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in PLACEHOLDER_REFERENCE_MARKERS)


def _expected_mcp_tools() -> tuple[str, str]:
    return ("policy_preflight", "policy_search")


def _extract_github_run_id(value: str) -> str:
    if value.isdigit():
        return value
    parts = urllib.parse.urlsplit(value)
    path_parts = [part for part in parts.path.split("/") if part]
    if parts.scheme != "https" or parts.netloc != "github.com" or len(path_parts) < 5:
        raise ValueError(
            "Hosted Beta Smoke evidence requires a GitHub run URL like "
            "https://github.com/<owner>/<repo>/actions/runs/<run-id> or a run id."
        )
    try:
        actions_index = path_parts.index("actions")
    except ValueError as exc:
        raise ValueError("Hosted Beta Smoke evidence requires a GitHub Actions run URL.") from exc
    if (
        actions_index + 2 >= len(path_parts)
        or path_parts[actions_index + 1] != "runs"
        or not path_parts[actions_index + 2].isdigit()
    ):
        raise ValueError(
            "Hosted Beta Smoke evidence requires a GitHub Actions run URL ending "
            "in /actions/runs/<run-id>."
        )
    return path_parts[actions_index + 2]


def _has_successful_hosted_smoke_job(jobs: object) -> bool:
    return _has_successful_job(jobs, "hosted-smoke")


def _has_successful_job(jobs: object, name: str) -> bool:
    return _is_successful_job(_find_job(jobs, name))


def _find_job(jobs: object, name: str) -> dict[str, Any] | None:
    if not isinstance(jobs, list):
        return None
    for job in jobs:
        if isinstance(job, dict) and job.get("name") == name:
            return job
    return None


def _is_successful_job(job: dict[str, Any] | None) -> bool:
    return (
        job is not None and job.get("status") == "completed" and job.get("conclusion") == "success"
    )


def _job_summaries(jobs: object) -> list[dict[str, object]]:
    if not isinstance(jobs, list):
        return []
    summaries: list[dict[str, object]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        summaries.append(
            {
                "conclusion": job.get("conclusion"),
                "name": job.get("name"),
                "status": job.get("status"),
            }
        )
    return summaries


def _normalize_hosted_mcp_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("Hosted MCP launch evidence requires an absolute https://.../mcp URL.")
    if parts.path.rstrip("/") != "/mcp":
        raise ValueError("Hosted MCP launch evidence URL must point to the /mcp route.")
    if parts.query or parts.fragment:
        raise ValueError("Hosted MCP launch evidence URL must not include a query or fragment.")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/mcp", "", ""))


def _same_origin_url(url: str, path: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _fetch_json(
    request: urllib.request.Request | str,
    *,
    urlopen: UrlOpener,
    timeout: float,
) -> tuple[int, object | None, str]:
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), json.load(response), ""
    except urllib.error.HTTPError as exc:
        return exc.code, _load_json_bytes(exc.read()), ""
    except json.JSONDecodeError as exc:
        return 0, None, f"Invalid JSON response: {exc}"
    except (OSError, urllib.error.URLError) as exc:
        return 0, None, str(exc)


def _load_json_bytes(payload: bytes) -> object | None:
    if not payload:
        return None
    try:
        return json.loads(payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _fetch_pypi_payload() -> dict[str, Any] | None:
    url = "https://pypi.org/pypi/policynim/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _evidence_record(
    *,
    summary: str,
    reference: str,
    verified_by: str,
    verified_at: str,
) -> dict[str, str]:
    return {
        "summary": summary,
        "reference": reference,
        "verified_by": verified_by,
        "verified_at": verified_at,
    }


def _default_release_tag(repo_root: Path) -> str:
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(project["project"]["version"])
    return f"v{version}"


def _manual_next_step(name: str) -> str:
    return {
        "hosted_mcp_domain": (
            "Attach public /healthz and /mcp evidence from the deployed hosted MCP domain."
        ),
        "hosted_beta_live_smoke": (
            "Run Hosted Beta Smoke with secrets and attach the GitHub Actions run URL."
        ),
        "real_mcp_client_session": (
            "Attach a real Codex or Claude Code MCP client session transcript or screenshot "
            "showing the secret-safe setup command and policy_preflight call."
        ),
    }[name]


def _render_text(payload: dict[str, Any]) -> str:
    lines = [
        "PolicyNIM launch evidence collection",
        f"Release tag: {payload['release_tag']}",
        "",
        "Probes:",
    ]
    probes = payload.get("probes", {})
    if isinstance(probes, dict):
        for name, probe in probes.items():
            if isinstance(probe, dict):
                lines.append(f"- {name}: {probe.get('status', 'unknown')}")
                job_detail = _render_probe_job_detail(probe)
                if job_detail:
                    lines.append(f"  job: {job_detail}")
                available_jobs = _render_available_jobs(probe.get("available_jobs"))
                if available_jobs:
                    lines.append(f"  available jobs: {available_jobs}")
                detail = probe.get("detail")
                if isinstance(detail, str) and detail:
                    lines.append(f"  detail: {detail}")
                next_step = probe.get("next_step")
                if next_step:
                    lines.append(f"  next: {next_step}")
    requested_failures = _render_requested_probe_failures(payload.get("requested_probe_failures"))
    if requested_failures:
        lines.extend(["", "Requested probe failures:", *requested_failures])
    return "\n".join(lines)


def _render_requested_probe_failures(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    lines: list[str] = []
    for failure in value:
        if not isinstance(failure, dict):
            continue
        name = failure.get("name")
        status = failure.get("status")
        next_step = failure.get("next_step")
        if not isinstance(name, str) or not isinstance(status, str):
            continue
        lines.append(f"- {name}: {status}")
        if isinstance(next_step, str) and next_step:
            lines.append(f"  next: {next_step}")
    return lines


def _render_probe_job_detail(probe: dict[str, Any]) -> str:
    job_name = probe.get("job_name")
    job_status = probe.get("job_status")
    job_conclusion = probe.get("job_conclusion")
    job_fields = (job_name, job_status, job_conclusion)
    if not all(isinstance(value, str) and value for value in job_fields):
        return ""
    return f"{job_name} {job_status}/{job_conclusion}"


def _render_available_jobs(value: object) -> str:
    if not isinstance(value, list):
        return ""
    rendered_jobs: list[str] = []
    for job in value:
        if not isinstance(job, dict):
            continue
        name = job.get("name")
        status = job.get("status")
        conclusion = job.get("conclusion")
        if not all(isinstance(item, str) and item for item in (name, status, conclusion)):
            continue
        rendered_jobs.append(f"{name} {status}/{conclusion}")
    return ", ".join(rendered_jobs)


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
