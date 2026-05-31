"""OSS readiness evidence check contract tests."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "oss_readiness_check.py"
COLLECTOR_SCRIPT = REPO_ROOT / "scripts" / "collect_launch_evidence.py"
EXAMPLE_EVIDENCE = REPO_ROOT / "docs" / "launch-evidence.example.json"
MCP_CLIENT_EVIDENCE_EXAMPLE = REPO_ROOT / "docs" / "mcp-client-evidence.example.json"
EXTERNAL_CHECK_NAMES = {
    "github_release_artifacts",
    "github_release_install_smoke",
    "github_artifact_attestations",
    "pypi_install_smoke",
    "pypi_project",
    "hosted_mcp_domain",
    "hosted_beta_live_smoke",
    "github_labels_applied",
    "github_topics_applied",
    "real_mcp_client_session",
}
EVIDENCE_RECORD_FIELDS = {"summary", "reference", "verified_by", "verified_at"}


def _project_version() -> str:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    assert isinstance(version, str)
    return version


def _release_tag() -> str:
    return f"v{_project_version()}"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("oss_readiness_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_collector_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("collect_launch_evidence", COLLECTOR_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fresh_verified_at(*, minutes_ago: int = 5) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def _complete_external_evidence(
    *,
    hosted_reference: str = "https://policynim.railway.app/healthz",
) -> dict[str, dict[str, str]]:
    version = _project_version()
    release_tag = _release_tag()
    release_asset_summary = (
        f"GitHub release {release_tag} contains required assets: RELEASE_MANIFEST.json, "
        f"SHA256SUMS, install.ps1, install.sh, policynim-{version}-py3-none-any.whl, "
        f"policynim-{version}.tar.gz, policynim-{release_tag}-darwin-amd64.tar.gz, "
        f"policynim-{release_tag}-darwin-arm64.tar.gz, "
        f"policynim-{release_tag}-linux-amd64.tar.gz, "
        f"policynim-{release_tag}-windows-amd64.zip; manifest and SHA256SUMS metadata "
        "cross-check 8 payload assets."
    )
    return {
        "github_artifact_attestations": {
            "summary": "GitHub artifact attestation verifies for the Linux standalone bundle.",
            "reference": (
                "https://github.com/nnennandukwe/policyNIM/releases/tag/"
                f"{release_tag}#policynim-{release_tag}-linux-amd64.tar.gz"
            ),
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
        "github_release_artifacts": {
            "summary": release_asset_summary,
            "reference": f"https://github.com/nnennandukwe/policyNIM/releases/tag/{release_tag}",
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
        "github_release_install_smoke": {
            "summary": f"Clean GitHub release installer smoke passed for {release_tag}.",
            "reference": f"https://github.com/nnennandukwe/policyNIM/releases/tag/{release_tag}",
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
        "pypi_project": {
            "summary": f"PyPI project and trusted publishing are configured for {release_tag}.",
            "reference": "https://github.com/nnennandukwe/policyNIM/actions/runs/456",
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
        "pypi_install_smoke": {
            "summary": f"Clean PyPI install smoke passed for policynim=={version}.",
            "reference": f"https://pypi.org/project/policynim/{version}/",
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
        "hosted_mcp_domain": {
            "summary": "Hosted /healthz returned 200 for the public MCP origin.",
            "reference": hosted_reference,
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
        "hosted_beta_live_smoke": {
            "summary": "Hosted Beta Smoke workflow passed policy_search and policy_preflight.",
            "reference": "https://github.com/nnennandukwe/policyNIM/actions/runs/123",
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
        "github_labels_applied": {
            "summary": "Label taxonomy was applied with scripts/sync_github_labels.py.",
            "reference": "gh label list --json name,color,description --limit 1000",
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
        "github_topics_applied": {
            "summary": "Topic taxonomy was applied with scripts/sync_github_topics.py.",
            "reference": "gh repo view --json repositoryTopics,nameWithOwner",
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
        "real_mcp_client_session": {
            "summary": "Codex loaded generated MCP config and policy_preflight was callable.",
            "reference": "launch-notes/codex-mcp-session.md",
            "verified_by": "maintainer@example.com",
            "verified_at": _fresh_verified_at(),
        },
    }


def test_oss_readiness_json_separates_local_readiness_from_external_proof() -> None:
    """Keep public-launch state machine-readable without claiming external proof."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "json"],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "local_ready_external_missing"
    assert payload["local_required_passed"] is True
    assert payload["external_required_passed"] is False
    assert checks["release_check_script"]["status"] == "passed"
    assert checks["maintainer_metadata"]["status"] == "passed"
    assert "high-value PR lane" in checks["maintainer_metadata"]["evidence"]
    assert "all first-run quickstart targets" in checks["release_check_script"]["evidence"]
    assert "semantic quickstart contracts" in checks["release_check_script"]["evidence"]
    assert "hosted client_commands" in checks["release_check_script"]["evidence"]
    assert "hosted URL/beta portal token flow" in checks["release_check_script"]["evidence"]
    assert "copyable agent_workflows" in checks["release_check_script"]["evidence"]
    assert "primary CLI help smoke" in checks["release_check_script"]["evidence"]
    assert "support-bundle first-run contract" in checks["release_check_script"]["evidence"]
    assert "MCP stdio tool-list smoke" in checks["release_check_script"]["evidence"]
    assert "generated-config MCP smoke" in checks["release_check_script"]["evidence"]
    assert "semantic MCP config contracts" in checks["release_check_script"]["evidence"]
    assert "Codex/Claude Code installed local stdio" in checks["release_check_script"]["evidence"]
    assert "paste-ready launch issue renderer" in checks["release_check_script"]["evidence"]
    assert "strict public-launch evidence gate" in checks["release_check_script"]["evidence"]
    assert checks["pull_request_template_metadata"]["status"] == "passed"
    assert "high-value PR lanes" in checks["pull_request_template_metadata"]["evidence"]
    assert "package-smoke-evidence" in checks["pull_request_template_metadata"]["evidence"]
    assert "secret-safety" in checks["pull_request_template_metadata"]["evidence"]
    assert checks["ci_clean_wheel_hosted_mcp_config_smoke"]["status"] == "passed"
    assert (
        "all first-run quickstart targets"
        in (checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"])
    )
    assert "init --help" in checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"]
    assert "primary CLI help" in checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"]
    assert (
        "Codex/Claude Code hosted MCP config JSON"
        in (checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"])
    )
    assert (
        "generated-config MCP smoke"
        in (checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"])
    )
    assert (
        "semantic Codex/Claude Code MCP config contracts"
        in (checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"])
    )
    assert (
        "no-clone installed local stdio"
        in (checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"])
    )
    assert (
        "package-smoke-evidence" in (checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"])
    )
    assert (
        "paste-ready launch issue" in (checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"])
    )
    assert "PR reviewers" in checks["ci_clean_wheel_hosted_mcp_config_smoke"]["evidence"]
    assert checks["release_workflow_hosted_mcp_config_smoke"]["status"] == "passed"
    assert (
        "all first-run quickstart targets"
        in (checks["release_workflow_hosted_mcp_config_smoke"]["evidence"])
    )
    assert "init --help" in checks["release_workflow_hosted_mcp_config_smoke"]["evidence"]
    assert "primary CLI help" in checks["release_workflow_hosted_mcp_config_smoke"]["evidence"]
    assert (
        "Codex/Claude Code hosted MCP config"
        in (checks["release_workflow_hosted_mcp_config_smoke"]["evidence"])
    )
    assert (
        "generated-config MCP smoke"
        in (checks["release_workflow_hosted_mcp_config_smoke"]["evidence"])
    )
    assert (
        "semantic MCP config contracts"
        in (checks["release_workflow_hosted_mcp_config_smoke"]["evidence"])
    )
    assert (
        "hosted placeholder/env-var safety"
        in (checks["release_workflow_hosted_mcp_config_smoke"]["evidence"])
    )
    assert checks["release_workflow_reviewable_smoke_evidence"]["status"] == "passed"
    assert (
        "release-wheel-smoke-evidence"
        in (checks["release_workflow_reviewable_smoke_evidence"]["evidence"])
    )
    assert (
        "standalone local stdio MCP config evidence"
        in (checks["release_workflow_reviewable_smoke_evidence"]["evidence"])
    )
    assert (
        "standalone MCP stdio smoke evidence"
        in (checks["release_workflow_reviewable_smoke_evidence"]["evidence"])
    )
    assert (
        "generated-config smoke evidence"
        in (checks["release_workflow_reviewable_smoke_evidence"]["evidence"])
    )
    assert (
        "Standalone smoke runs from an empty cwd"
        in (checks["release_workflow_reviewable_smoke_evidence"]["evidence"])
    )
    assert (
        "semantic MCP config contracts"
        in (checks["release_workflow_reviewable_smoke_evidence"]["evidence"])
    )
    assert (
        "does not download those evidence artifacts into public release assets"
        in (checks["release_workflow_reviewable_smoke_evidence"]["evidence"])
    )
    assert checks["release_workflow_public_launch_mode"]["status"] == "passed"
    assert (
        "public_launch=true requires publish_pypi=true"
        in checks["release_workflow_public_launch_mode"]["evidence"]
    )
    assert (
        "GitHub-only release candidate" in checks["release_workflow_public_launch_mode"]["evidence"]
    )
    assert checks["release_artifact_attestations"]["status"] == "passed"
    assert "actions/attest" in checks["release_artifact_attestations"]["evidence"]
    assert "SHA256SUMS" in checks["release_artifact_attestations"]["evidence"]
    assert "install.sh" in checks["release_artifact_attestations"]["evidence"]
    assert "release-attestation-evidence" in checks["release_artifact_attestations"]["evidence"]
    assert checks["installer_provenance_controls"]["status"] == "passed"
    assert "POLICYNIM_VERIFY_ATTESTATION" in checks["installer_provenance_controls"]["evidence"]
    assert checks["installer_powershell_provenance_controls"]["status"] == "passed"
    assert (
        "POLICYNIM_VERIFY_ATTESTATION"
        in (checks["installer_powershell_provenance_controls"]["evidence"])
    )
    assert checks["hosted_smoke_workflow"]["status"] == "passed"
    assert "hosted-smoke-evidence" in checks["hosted_smoke_workflow"]["evidence"]
    assert "policynim-hosted-smoke-junit.xml" in checks["hosted_smoke_workflow"]["evidence"]
    assert checks["support_bundle_public_redaction"]["status"] == "passed"
    assert "redacts local path prefixes" in checks["support_bundle_public_redaction"]["evidence"]
    assert (
        "first-run quickstart target context"
        in checks["support_bundle_public_redaction"]["evidence"]
    )
    assert "hosted client_commands" in checks["support_bundle_public_redaction"]["evidence"]
    assert "Codex and Claude Code" in checks["support_bundle_public_redaction"]["evidence"]
    assert "hosted_url and beta_portal_url" in checks["support_bundle_public_redaction"]["evidence"]
    assert "copyable agent_workflows" in checks["support_bundle_public_redaction"]["evidence"]
    assert checks["launch_evidence_collector"]["status"] == "passed"
    assert (
        "opt-in checks live GitHub/PyPI publish and hosted MCP domain/smoke/client facts"
        in checks["launch_evidence_collector"]["evidence"]
    )
    assert "release manifests and SHA256SUMS" in checks["launch_evidence_collector"]["evidence"]
    assert "labels and topics" in checks["launch_evidence_collector"]["evidence"]
    assert "hosted smoke JUnit artifacts" in checks["launch_evidence_collector"]["evidence"]
    assert "attested subjects" in checks["launch_evidence_collector"]["evidence"]
    assert "selected release asset" in checks["launch_evidence_collector"]["evidence"]
    assert (
        "trusted-publish run SHA to the release target"
        in (checks["launch_evidence_collector"]["evidence"])
    )
    assert "public PyPI install smoke" in checks["launch_evidence_collector"]["evidence"]
    assert "install.sh guidance" in checks["launch_evidence_collector"]["evidence"]
    assert "semantic first-run JSON" in checks["launch_evidence_collector"]["evidence"]
    assert (
        "support-bundle hosted client_commands" in checks["launch_evidence_collector"]["evidence"]
    )
    assert "Codex and Claude Code" in checks["launch_evidence_collector"]["evidence"]
    assert (
        "support-bundle hosted_url/beta_portal_url token flow"
        in (checks["launch_evidence_collector"]["evidence"])
    )
    assert "local MCP config JSON" in checks["launch_evidence_collector"]["evidence"]
    assert (
        "hosted-smoke run SHA to the release target"
        in (checks["launch_evidence_collector"]["evidence"])
    )
    assert (
        "placeholder client-session references" in checks["launch_evidence_collector"]["evidence"]
    )
    assert "safe client-session templates" in checks["launch_evidence_collector"]["evidence"]
    assert "can fail on requested probe failures" in checks["launch_evidence_collector"]["evidence"]
    assert checks["external_evidence_freshness_gate"]["status"] == "passed"
    assert (
        "stale or future-dated external launch evidence"
        in checks["external_evidence_freshness_gate"]["evidence"]
    )
    assert checks["public_launch_runbook"]["status"] == "passed"
    assert checks["github_topic_metadata"]["status"] == "passed"
    assert "discoverability topics" in checks["github_topic_metadata"]["evidence"]
    assert "launch evidence" in checks["github_triage_metadata"]["evidence"]
    assert checks["public_launch_issue_template"]["status"] == "passed"
    assert "public launch evidence issue form" in checks["public_launch_issue_template"]["evidence"]
    assert "strict public readiness" in checks["public_launch_issue_template"]["evidence"]
    assert checks["topic_sync_script"]["status"] == "passed"
    assert (
        "offline dry-run, live dry-run, authenticated apply"
        in checks["label_sync_script"]["evidence"]
    )
    assert "actionable gh recovery guidance" in checks["label_sync_script"]["evidence"]
    assert (
        "offline dry-run, live dry-run, authenticated apply"
        in checks["topic_sync_script"]["evidence"]
    )
    assert "actionable gh recovery guidance" in checks["topic_sync_script"]["evidence"]
    assert (
        "No complete PyPI package and trusted-publishing evidence record"
        in checks["pypi_project"]["evidence"]
    )
    assert "publish-pypi job evidence" in checks["pypi_project"]["next_step"]

    for name in (*sorted(EXTERNAL_CHECK_NAMES),):
        assert checks[name]["status"] == "missing_external"
        assert checks[name]["next_step"]
        if "collect_launch_evidence.py" in checks[name]["next_step"]:
            assert (
                "--write-external-evidence-file docs/launch-evidence.json"
                in checks[name]["next_step"]
            )
            assert "--format json" in checks[name]["next_step"]

    attestation_next_step = checks["github_artifact_attestations"]["next_step"]
    assert (
        "uv run python scripts/collect_launch_evidence.py "
        f"--release-tag {_release_tag()} "
        "--release-attestation-asset-name install.sh "
        "--require-requested-probes "
        "--write-external-evidence-file docs/launch-evidence.json --merge-existing "
        "--format json"
    ) in attestation_next_step
    assert (
        "uv run python scripts/collect_launch_evidence.py "
        f"--release-tag {_release_tag()} "
        "--release-attestation-asset-name install.sh "
        "--merge-existing`"
    ) not in attestation_next_step


def test_oss_readiness_strict_public_holds_on_missing_external_evidence() -> None:
    """Give maintainers a non-zero public-launch gate before external proof exists."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--strict-public", "--format", "json"],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["decision"] == "hold_external_missing"
    assert payload["external_required_passed"] is False


def test_oss_readiness_help_names_external_evidence_contract() -> None:
    """Keep strict public evidence requirements discoverable from the command."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert "fresh verified_at" in result.stdout
    assert "reference shapes" in result.stdout
    assert "public-launch-runbook.md" in result.stdout


def test_oss_readiness_markdown_output_is_launch_issue_ready() -> None:
    """Let maintainers paste readiness evidence into release notes or launch issues."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "markdown"],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("# PolicyNIM OSS Readiness\n")
    assert "- Decision: `local_ready_external_missing`" in result.stdout
    assert "- Local required passed: `true`" in result.stdout
    assert "- External required passed: `false`" in result.stdout
    assert "## Passed Local Checks" in result.stdout
    assert "## Missing External Evidence" in result.stdout
    assert "### public_launch_runbook" in result.stdout
    assert "### github_release_artifacts" in result.stdout
    assert "### github_release_install_smoke" in result.stdout
    assert "Publish or inspect the draft release" in result.stdout


def test_oss_readiness_launch_issue_output_tracks_external_blockers() -> None:
    """Turn the strict readiness hold into an issue-ready external proof checklist."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "launch-issue"],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("# PolicyNIM Public Launch Evidence\n")
    assert "Decision: `local_ready_external_missing`" in result.stdout
    assert "- [ ] `pypi_project`" in result.stdout
    assert "- [ ] `pypi_install_smoke`" in result.stdout
    assert "- [ ] `github_release_install_smoke`" in result.stdout
    assert "- [ ] `github_artifact_attestations`" in result.stdout
    assert "- [ ] `hosted_mcp_domain`" in result.stdout
    assert "- [ ] `github_topics_applied`" in result.stdout
    assert "- [ ] `real_mcp_client_session`" in result.stdout
    assert "uv run python scripts/collect_launch_evidence.py" in result.stdout
    assert "--merge-existing" in result.stdout
    assert "--format json" in result.stdout
    assert "## Missing Evidence Collection Commands" in result.stdout
    assert "--release-attestation-asset-name install.sh" in result.stdout
    assert "--github-install-smoke" in result.stdout
    assert (
        "--pypi-publish-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>'"
        in result.stdout
    )
    assert "--pypi-install-smoke" in result.stdout
    assert "--hosted-mcp-url 'https://<railway-domain>/mcp'" in result.stdout
    assert (
        "--hosted-smoke-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>'"
        in result.stdout
    )
    label_block = result.stdout.split("### github_labels_applied", maxsplit=1)[1].split(
        "### github_topics_applied",
        maxsplit=1,
    )[0]
    topic_block = result.stdout.split("### github_topics_applied", maxsplit=1)[1].split(
        "### real_mcp_client_session",
        maxsplit=1,
    )[0]
    for block, script_name in (
        (label_block, "sync_github_labels.py"),
        (topic_block, "sync_github_topics.py"),
    ):
        assert "gh auth status" in block
        assert f"uv run python scripts/{script_name} --live --format json" in block
        assert f"uv run python scripts/{script_name} --apply --format json" in block
        assert block.index("gh auth status") < block.index(
            f"uv run python scripts/{script_name} --live --format json"
        )
        assert block.index(f"uv run python scripts/{script_name} --live --format json") < (
            block.index(f"uv run python scripts/{script_name} --apply --format json")
        )
    assert (
        "--write-mcp-client-evidence-template launch-notes/codex-mcp-session.json" in result.stdout
    )
    assert "--mcp-client-hosted-url 'https://<railway-domain>/mcp'" in result.stdout
    assert "https://policynim.dev/mcp" not in result.stdout
    assert "--mcp-client-evidence-file launch-notes/codex-mcp-session.json" in result.stdout
    assert "--external-evidence-file docs/launch-evidence.json --format launch-issue" in (
        result.stdout
    )
    assert "--strict-public --external-evidence-file docs/launch-evidence.json" in result.stdout
    generic_commands = result.stdout.split("## Evidence Commands", 1)[1]
    assert (
        "uv run python scripts/collect_launch_evidence.py "
        "--write-external-evidence-file docs/launch-evidence.json --merge-existing "
        "--format json"
    ) in generic_commands
    assert (
        "uv run python scripts/collect_launch_evidence.py "
        "--write-external-evidence-file docs/launch-evidence.json --merge-existing\n"
    ) not in generic_commands
    assert (
        "Use the `github_artifact_attestations` command block in Missing Evidence "
        "Collection Commands."
    ) in result.stdout
    assert (
        "Run `uv run python scripts/collect_launch_evidence.py "
        "--release-attestation-asset-name install.sh "
        "--merge-existing` after"
    ) not in result.stdout
    assert "--require-requested-probes" in result.stdout
    assert "Do not include API keys, bearer tokens, cookies, or raw MCP transcripts." in (
        result.stdout
    )


def test_launch_issue_commands_derive_release_asset_from_project_version(
    tmp_path: Path,
) -> None:
    """Keep generated launch commands current when the package version changes."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "policynim"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--format",
            "launch-issue",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    assert "Decision: `local_blocked`" in result.stdout
    assert "--release-tag v9.8.7" in result.stdout
    assert "--release-attestation-asset-name install.sh" in result.stdout
    assert "policynim-v0.1.0-linux-amd64.tar.gz" not in result.stdout


def test_oss_readiness_launch_issue_output_reports_evidence_file_failures(
    tmp_path: Path,
) -> None:
    """Show evidence-file validation failures before maintainers paste a launch issue."""
    evidence_file = tmp_path / "launch-evidence.json"
    evidence_file.write_text("not json", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--external-evidence-file",
            str(evidence_file),
            "--format",
            "launch-issue",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert "## Evidence File" in result.stdout
    assert "- Status: `failed`" in result.stdout
    assert "could not be loaded" in result.stdout
    assert "summary, reference, verified_by, and verified_at" in result.stdout


def test_oss_readiness_launch_issue_commands_skip_passed_external_evidence(
    tmp_path: Path,
) -> None:
    """Keep launch-issue commands focused on evidence that is still missing."""
    evidence = {
        check_name: {field: "" for field in EVIDENCE_RECORD_FIELDS}
        for check_name in EXTERNAL_CHECK_NAMES
    }
    for check_name, reference in (
        (
            "github_release_artifacts",
            f"https://github.com/nnennandukwe/policyNIM/releases/tag/{_release_tag()}",
        ),
        ("github_labels_applied", "gh label list --json name,color,description --limit 1000"),
        ("github_topics_applied", "gh repo view --json repositoryTopics,nameWithOwner"),
    ):
        summary = f"{check_name} verified."
        if check_name == "github_release_artifacts":
            summary = _complete_external_evidence()["github_release_artifacts"]["summary"]
        evidence[check_name] = {
            "summary": summary,
            "reference": reference,
            "verified_by": "maintainer",
            "verified_at": _fresh_verified_at(),
        }
    evidence_file = tmp_path / "launch-evidence.json"
    evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--external-evidence-file",
            str(evidence_file),
            "--format",
            "launch-issue",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert "- [x] `github_release_artifacts`" in result.stdout
    assert "- [x] `github_labels_applied`" in result.stdout
    assert "- [x] `github_topics_applied`" in result.stdout
    assert "### github_release_artifacts" not in result.stdout
    assert "### github_labels_applied" not in result.stdout
    assert "### github_topics_applied" not in result.stdout
    assert "### pypi_project" in result.stdout
    assert "### pypi_install_smoke" in result.stdout
    assert "--pypi-publish-run-url" in result.stdout
    assert "--pypi-install-smoke" in result.stdout


def test_launch_evidence_example_lists_required_keys_without_claiming_readiness() -> None:
    """Keep the evidence-file template useful without allowing placeholder proof."""
    module = _load_script_module()
    evidence = json.loads(EXAMPLE_EVIDENCE.read_text(encoding="utf-8"))

    assert set(evidence) == EXTERNAL_CHECK_NAMES
    for value in evidence.values():
        assert set(value) == EVIDENCE_RECORD_FIELDS
        assert all(field_value == "" for field_value in value.values())

    payload = module.run_oss_readiness_check(
        repo_root=REPO_ROOT,
        strict_public=True,
        external_evidence_file=EXAMPLE_EVIDENCE,
    )

    assert payload["decision"] == "hold_external_missing"
    assert payload["external_required_passed"] is False


def test_launch_evidence_example_matches_generated_template() -> None:
    """Keep the checked-in template aligned with the script-generated template."""
    module = _load_script_module()
    evidence = json.loads(EXAMPLE_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence == module._external_evidence_template()


def test_mcp_client_evidence_example_is_safe_but_not_collectable() -> None:
    """Keep the checked-in real-client evidence example from proving launch readiness."""
    module = _load_collector_module()
    evidence = json.loads(MCP_CLIENT_EVIDENCE_EXAMPLE.read_text(encoding="utf-8"))

    assert evidence["client"] in {"codex", "claude-code"}
    assert evidence["transport"] in {"hosted-http", "local-stdio"}
    assert evidence["server_name"] == "policynim"
    assert "policy_preflight" in evidence["tools"]
    assert "policy_search" in evidence["tools"]
    assert evidence["called_tool"] == "policy_preflight"
    assert evidence["reference"] == ""
    assert evidence["secrets_included"] is False
    validation_error = module._validate_mcp_client_evidence_payload(evidence)
    assert validation_error is not None
    assert validation_error["status"] == "missing_reference"


def test_write_external_evidence_template_creates_reviewable_placeholder(
    tmp_path: Path,
) -> None:
    """Let maintainers generate the launch evidence template without hand-copying JSON."""
    target = tmp_path / "launch" / "launch-evidence.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--write-external-evidence-template",
            str(target),
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )
    evidence = json.loads(target.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert result.stderr == ""
    assert f"Wrote external evidence template: {target}" in result.stdout
    assert set(evidence) == EXTERNAL_CHECK_NAMES
    for value in evidence.values():
        assert set(value) == EVIDENCE_RECORD_FIELDS
        assert all(field_value == "" for field_value in value.values())


def test_write_external_evidence_template_refuses_to_overwrite_without_force(
    tmp_path: Path,
) -> None:
    """Protect real launch evidence from accidental template rewrites."""
    target = tmp_path / "launch-evidence.json"
    target.write_text("existing evidence", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--write-external-evidence-template",
            str(target),
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert "Pass --force" in result.stderr
    assert target.read_text(encoding="utf-8") == "existing evidence"


def test_write_external_evidence_template_force_overwrites_existing_file(
    tmp_path: Path,
) -> None:
    """Allow an explicit template reset when maintainers ask for it."""
    target = tmp_path / "launch-evidence.json"
    target.write_text("existing evidence", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--write-external-evidence-template",
            str(target),
            "--force",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )
    evidence = json.loads(target.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert result.stderr == ""
    assert set(evidence) == EXTERNAL_CHECK_NAMES


def test_oss_readiness_rejects_legacy_string_external_evidence(tmp_path: Path) -> None:
    """Require reviewable evidence records instead of unstructured strings."""
    module = _load_script_module()
    evidence_file = tmp_path / "legacy-evidence.json"
    evidence_file.write_text(
        json.dumps(
            {
                "github_release_artifacts": (
                    "https://github.com/example/policyNIM/releases/tag/v0.1.0"
                ),
            }
        ),
        encoding="utf-8",
    )

    payload = module.run_oss_readiness_check(
        repo_root=REPO_ROOT,
        strict_public=True,
        external_evidence_file=evidence_file,
    )
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "hold_external_missing"
    assert checks["external_evidence_file"]["status"] == "failed"
    assert (
        "summary, reference, verified_by, verified_at"
        in checks["external_evidence_file"]["evidence"]
    )


def test_oss_readiness_accepts_partial_external_evidence_file(tmp_path: Path) -> None:
    """Allow collected launch evidence to track partial proof without a file-level failure."""
    module = _load_script_module()
    evidence_file = tmp_path / "partial-evidence.json"
    evidence = module._external_evidence_template()
    evidence["github_release_artifacts"] = {
        "summary": _complete_external_evidence()["github_release_artifacts"]["summary"],
        "reference": f"https://github.com/nnennandukwe/policyNIM/releases/tag/{_release_tag()}",
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

    payload = module.run_oss_readiness_check(
        repo_root=REPO_ROOT,
        strict_public=True,
        external_evidence_file=evidence_file,
    )
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "hold_external_missing"
    assert checks["external_evidence_file"]["status"] == "passed"
    assert checks["github_release_artifacts"]["status"] == "passed"
    assert checks["hosted_mcp_domain"]["status"] == "missing_external"


def test_oss_readiness_rejects_stale_external_evidence(tmp_path: Path) -> None:
    """Do not accept old launch proof as current public-readiness evidence."""
    module = _load_script_module()
    evidence_file = tmp_path / "stale-evidence.json"
    evidence = module._external_evidence_template()
    evidence["github_release_artifacts"] = {
        "summary": "GitHub release contains every required asset.",
        "reference": "https://github.com/nnennandukwe/policyNIM/releases/tag/v0.1.0",
        "verified_by": "maintainer@example.com",
        "verified_at": "2026-01-01T00:00:00Z",
    }
    evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

    payload = module.run_oss_readiness_check(
        repo_root=REPO_ROOT,
        strict_public=True,
        external_evidence_file=evidence_file,
    )
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "hold_external_missing"
    assert checks["external_evidence_file"]["status"] == "failed"
    assert (
        "github_release_artifacts.verified_at is older than"
        in checks["external_evidence_file"]["evidence"]
    )
    assert checks["github_release_artifacts"]["status"] == "missing_external"


def test_oss_readiness_rejects_placeholder_external_references(tmp_path: Path) -> None:
    """Do not let example or template references satisfy strict public readiness."""
    module = _load_script_module()
    evidence_file = tmp_path / "placeholder-evidence.json"
    evidence = module._external_evidence_template()
    evidence["hosted_mcp_domain"] = {
        "summary": "Hosted /healthz returned 200 for the public MCP origin.",
        "reference": "https://<railway-domain>/healthz",
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence["github_artifact_attestations"] = {
        "summary": "GitHub artifact attestation verifies for the Linux standalone bundle.",
        "reference": (
            "https://github.com/example/policyNIM/releases/tag/"
            "v0.1.0#policynim-v0.1.0-linux-amd64.tar.gz"
        ),
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

    payload = module.run_oss_readiness_check(
        repo_root=REPO_ROOT,
        strict_public=True,
        external_evidence_file=evidence_file,
    )
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "hold_external_missing"
    assert checks["external_evidence_file"]["status"] == "failed"
    assert (
        "hosted_mcp_domain.reference contains placeholder text"
        in checks["external_evidence_file"]["evidence"]
    )
    assert (
        "github_artifact_attestations.reference contains placeholder text"
        in checks["external_evidence_file"]["evidence"]
    )


def test_oss_readiness_rejects_external_reference_shape_mismatches(tmp_path: Path) -> None:
    """Keep strict public proof tied to the expected external evidence surface."""
    module = _load_script_module()
    evidence_file = tmp_path / "wrong-reference-shape-evidence.json"
    evidence = module._external_evidence_template()
    evidence["pypi_project"] = {
        "summary": "PyPI project and trusted publishing are configured for v0.1.0.",
        "reference": "https://pypi.org/project/policynim/0.1.0/",
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence["pypi_install_smoke"] = {
        "summary": "A clean environment installed policynim==0.1.0 from PyPI.",
        "reference": "https://github.com/nnennandukwe/policyNIM/releases/tag/v0.1.0",
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence["github_release_install_smoke"] = {
        "summary": "A clean GitHub release installer smoke passed for v0.1.0.",
        "reference": "https://pypi.org/project/policynim/0.1.0/",
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence["hosted_beta_live_smoke"] = {
        "summary": "Hosted Beta Smoke workflow passed policy_search and policy_preflight.",
        "reference": "https://github.com/nnennandukwe/policyNIM/releases/tag/v0.1.0",
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence["github_labels_applied"] = {
        "summary": "Label taxonomy was applied with scripts/sync_github_labels.py.",
        "reference": "https://github.com/nnennandukwe/policyNIM/issues/123",
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence["github_topics_applied"] = {
        "summary": "Topic taxonomy was applied with scripts/sync_github_topics.py.",
        "reference": "https://github.com/nnennandukwe/policyNIM/topics",
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

    payload = module.run_oss_readiness_check(
        repo_root=REPO_ROOT,
        strict_public=True,
        external_evidence_file=evidence_file,
    )
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "hold_external_missing"
    assert checks["external_evidence_file"]["status"] == "failed"
    assert (
        "pypi_project.reference must be a GitHub Actions run URL"
        in checks["external_evidence_file"]["evidence"]
    )
    assert (
        "pypi_install_smoke.reference must be a PyPI project version URL"
        in checks["external_evidence_file"]["evidence"]
    )
    assert (
        "github_release_install_smoke.reference must be a GitHub release tag URL"
        in checks["external_evidence_file"]["evidence"]
    )
    assert (
        "hosted_beta_live_smoke.reference must be a GitHub Actions run URL"
        in checks["external_evidence_file"]["evidence"]
    )
    assert (
        "github_labels_applied.reference must be the gh label list command"
        in checks["external_evidence_file"]["evidence"]
    )
    assert (
        "github_topics_applied.reference must be the gh repo view command"
        in checks["external_evidence_file"]["evidence"]
    )


def test_oss_readiness_rejects_release_artifact_evidence_missing_current_assets(
    tmp_path: Path,
) -> None:
    """Do not let older release evidence pass after the asset contract changes."""
    module = _load_script_module()
    evidence_file = tmp_path / "stale-release-asset-evidence.json"
    evidence = module._external_evidence_template()
    evidence["github_release_artifacts"] = {
        "summary": (
            "GitHub release v0.1.0 contains required assets: RELEASE_MANIFEST.json, "
            "SHA256SUMS, install.ps1, install.sh, policynim-0.1.0-py3-none-any.whl, "
            "policynim-0.1.0.tar.gz, policynim-v0.1.0-darwin-arm64.tar.gz, "
            "policynim-v0.1.0-linux-amd64.tar.gz, "
            "policynim-v0.1.0-windows-amd64.zip."
        ),
        "reference": "https://github.com/nnennandukwe/policyNIM/releases/tag/v0.1.0",
        "verified_by": "maintainer@example.com",
        "verified_at": _fresh_verified_at(),
    }
    evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

    payload = module.run_oss_readiness_check(
        repo_root=REPO_ROOT,
        strict_public=True,
        external_evidence_file=evidence_file,
    )
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "hold_external_missing"
    assert checks["external_evidence_file"]["status"] == "failed"
    assert (
        "github_release_artifacts.summary must mention current expected release asset "
        f"policynim-{_release_tag()}-darwin-amd64.tar.gz"
    ) in checks["external_evidence_file"]["evidence"]


def test_oss_readiness_strict_public_passes_with_complete_external_evidence(
    tmp_path: Path,
) -> None:
    """Allow maintainers to attach verified external proof without live network calls."""
    module = _load_script_module()
    repo_copy = tmp_path / "policyNIM"
    shutil.copytree(
        REPO_ROOT,
        repo_copy,
        ignore=shutil.ignore_patterns(".git", ".venv", "dist", ".pytest_cache"),
    )
    for relative_path in (
        "README.md",
        "docs/agent-workflows.md",
        "examples/codex/README.md",
        "examples/claude-code/README.md",
    ):
        path = repo_copy / relative_path
        text = path.read_text(encoding="utf-8").replace(
            "https://<railway-domain>",
            "https://policynim.railway.app",
        )
        path.write_text(text, encoding="utf-8")

    evidence_file = tmp_path / "oss-evidence.json"
    evidence = _complete_external_evidence()
    evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

    payload = module.run_oss_readiness_check(
        repo_root=repo_copy,
        strict_public=True,
        external_evidence_file=evidence_file,
    )
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "public_ready"
    assert payload["external_required_passed"] is True
    for name, expected_evidence in evidence.items():
        assert checks[name]["status"] == "passed"
        assert expected_evidence["summary"] in checks[name]["evidence"]
        assert expected_evidence["reference"] in checks[name]["evidence"]
        assert expected_evidence["verified_by"] in checks[name]["evidence"]
        assert expected_evidence["verified_at"] in checks[name]["evidence"]
        assert checks[name]["next_step"] == ""
    assert checks["strict_public_hosted_onboarding_docs"]["status"] == "passed"


def test_oss_readiness_strict_public_requires_verified_hosted_url_in_onboarding_docs(
    tmp_path: Path,
) -> None:
    """Do not claim public readiness while docs still send users to hosted placeholders."""
    module = _load_script_module()
    evidence_file = tmp_path / "oss-evidence.json"
    evidence = _complete_external_evidence()
    evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

    payload = module.run_oss_readiness_check(
        repo_root=REPO_ROOT,
        strict_public=True,
        external_evidence_file=evidence_file,
    )
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "local_blocked"
    assert payload["local_required_passed"] is False
    assert payload["external_required_passed"] is True
    assert checks["strict_public_hosted_onboarding_docs"]["status"] == "failed"
    assert (
        "https://policynim.railway.app/mcp"
        in checks["strict_public_hosted_onboarding_docs"]["evidence"]
    )
    assert "README.md" in checks["strict_public_hosted_onboarding_docs"]["evidence"]
    assert (
        "Replace hosted URL placeholders"
        in checks["strict_public_hosted_onboarding_docs"]["next_step"]
    )


def test_oss_readiness_reports_missing_local_evidence(tmp_path: Path) -> None:
    """Fail local readiness when required repo evidence is absent."""
    repo_copy = tmp_path / "policyNIM"
    shutil.copytree(
        REPO_ROOT,
        repo_copy,
        ignore=shutil.ignore_patterns(".git", ".venv", "dist", ".pytest_cache"),
    )
    (repo_copy / "scripts" / "release_check.py").unlink()

    module = _load_script_module()
    payload = module.run_oss_readiness_check(repo_root=repo_copy)
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["decision"] == "local_blocked"
    assert payload["local_required_passed"] is False
    assert checks["release_check_script"]["status"] == "failed"
    assert "scripts/release_check.py" in checks["release_check_script"]["evidence"]
