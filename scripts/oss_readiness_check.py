"""Report PolicyNIM OSS readiness without making external-service claims."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

Status = Literal["passed", "failed", "missing_external"]
Decision = Literal[
    "local_ready_external_missing",
    "hold_external_missing",
    "local_blocked",
    "public_ready",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
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
MAX_EXTERNAL_EVIDENCE_AGE_DAYS = 14
MAX_EXTERNAL_EVIDENCE_FUTURE_SKEW_MINUTES = 10
GITHUB_LABEL_LIST_REFERENCE = "gh label list --json name,color,description --limit 1000"
GITHUB_TOPIC_LIST_REFERENCE = "gh repo view --json repositoryTopics,nameWithOwner"
DEFAULT_ATTESTATION_ASSET_NAME = "install.sh"
PLACEHOLDER_REFERENCE_MARKERS = (
    "<",
    ">",
    "example.",
    "example/",
    ".invalid",
    "todo",
    "placeholder",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--format",
        choices=("text", "json", "markdown", "launch-issue"),
        default="text",
        help="Output format for the OSS readiness summary.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to inspect. Defaults to this script's repository.",
    )
    parser.add_argument(
        "--strict-public",
        action="store_true",
        help="Exit non-zero until external public-launch evidence is present.",
    )
    parser.add_argument(
        "--external-evidence-file",
        type=Path,
        default=None,
        help=(
            "Optional JSON object mapping external check names to evidence records "
            "with summary, reference, verified_by, and fresh verified_at fields; "
            "strict public mode also validates reference shapes. See "
            "public-launch-runbook.md."
        ),
    )
    parser.add_argument(
        "--write-external-evidence-template",
        type=Path,
        default=None,
        help="Write a blank external evidence template JSON file and exit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --write-external-evidence-template to overwrite an existing file.",
    )
    args = parser.parse_args()

    if args.write_external_evidence_template is not None:
        return _write_external_evidence_template(
            args.write_external_evidence_template,
            force=args.force,
        )

    result = run_oss_readiness_check(
        repo_root=args.repo_root.resolve(),
        strict_public=args.strict_public,
        external_evidence_file=args.external_evidence_file,
    )
    if args.format == "json":
        print(json.dumps(result, indent=2))
    elif args.format == "markdown":
        print(_render_markdown_summary(result))
    elif args.format == "launch-issue":
        print(_render_launch_issue(result))
    else:
        print(_render_text_summary(result))
    return _exit_code(result)


def run_oss_readiness_check(
    *,
    repo_root: Path,
    strict_public: bool = False,
    external_evidence_file: Path | None = None,
) -> dict[str, Any]:
    """Return a deterministic summary of local and external OSS-readiness evidence."""
    local_checks = _local_checks(repo_root)
    external_evidence, external_evidence_records, evidence_file_check = _load_external_evidence(
        external_evidence_file,
        repo_root=repo_root,
    )
    if strict_public:
        local_checks.extend(
            _strict_public_local_checks(
                repo_root=repo_root,
                external_evidence_records=external_evidence_records,
            ),
        )
    external_checks = _external_checks(repo_root, external_evidence)
    if evidence_file_check is not None:
        external_checks.insert(0, evidence_file_check)
    checks = [*local_checks, *external_checks]
    local_required_passed = all(check["status"] == "passed" for check in local_checks)
    external_required_passed = all(check["status"] == "passed" for check in external_checks)

    decision: Decision
    if not local_required_passed:
        decision = "local_blocked"
    elif external_required_passed:
        decision = "public_ready"
    elif strict_public:
        decision = "hold_external_missing"
    else:
        decision = "local_ready_external_missing"

    return {
        "schema_version": "1",
        "decision": decision,
        "repo_root": str(repo_root),
        "strict_public": strict_public,
        "external_evidence_file": str(external_evidence_file) if external_evidence_file else None,
        "local_required_passed": local_required_passed,
        "external_required_passed": external_required_passed,
        "checks": checks,
    }


def _local_checks(repo_root: Path) -> list[dict[str, str]]:
    return [
        _file_contains_check(
            name="release_check_script",
            repo_root=repo_root,
            path=Path("scripts/release_check.py"),
            tokens=[
                "installed_cli_quickstart_json",
                "installed_cli_quickstart_local_cli_json",
                "installed_cli_quickstart_local_mcp_json",
                "quickstart_json_parse",
                "quickstart_contract",
                "quickstart_local_cli_json_parse",
                "quickstart_local_cli_contract",
                "quickstart_local_mcp_json_parse",
                "quickstart_local_mcp_contract",
                "client_commands",
                "agent_workflows",
                "installed_cli_doctor_json",
                "support_bundle_json_parse",
                "support_bundle_contract",
                "installed_cli_mcp_smoke_json",
                "mcp_smoke_json_parse",
                "installed_cli_mcp_config_json",
                "installed_cli_claude_mcp_config_json",
                "installed_cli_hosted_mcp_config_json",
                "installed_cli_claude_hosted_mcp_config_json",
                "mcp_config_contract",
                "claude_mcp_config_contract",
                "hosted_mcp_config_contract",
                "claude_hosted_mcp_config_contract",
                "mcp-config-codex-local-stdio",
                "mcp-config-claude-code-local-stdio",
                "mcp-config-codex-hosted-http",
                "mcp-config-claude-code-hosted-http",
                "installed_cli_init_help",
                "installed_cli_ingest_help",
                "installed_cli_preflight_help",
                "init_help_contract",
                "ingest_help_contract",
                "preflight_help_contract",
                "--validate-init-help-contract",
                "--validate-help-contract",
                "hosted_mcp_config_json_parse",
                "claude_hosted_mcp_config_json_parse",
                "--mcp-config-file",
                "installed_cli_mcp_smoke_from_codex_config_json",
                "installed_cli_mcp_smoke_from_claude_config_json",
                "oss_readiness_strict_public_json",
                "--strict-public",
                "external_evidence_file",
                "release_notes_check",
                "oss_readiness_launch_issue",
            ],
            evidence=(
                "scripts/release_check.py runs deterministic gates, clean wheel CLI "
                "smoke, all first-run quickstart targets, semantic quickstart "
                "contracts including hosted client_commands, hosted URL/beta "
                "portal token flow, and copyable agent_workflows, primary CLI help "
                "smoke (`init --help`, `ingest --help`, "
                "`preflight --help`), support-bundle first-run contract, MCP "
                "stdio tool-list smoke, generated-config MCP smoke, semantic MCP "
                "config contracts for "
                "Codex/Claude Code installed local stdio and hosted placeholder "
                "configs, plus the paste-ready launch issue renderer and optional strict "
                "public-launch evidence gate."
            ),
            next_step=(
                "Restore scripts/release_check.py with installed CLI quickstart "
                "semantic checks, support-bundle first-run contract checks, MCP "
                "stdio, Codex/Claude Code MCP config semantic contracts, the "
                "launch-issue smoke, and the strict public-launch evidence mode."
            ),
        ),
        _file_contains_check(
            name="release_manifest_script",
            repo_root=repo_root,
            path=Path("scripts/release_manifest.py"),
            tokens=["RELEASE_MANIFEST.json", "sha256", "size_bytes"],
            evidence="scripts/release_manifest.py defines the release asset manifest contract.",
            next_step="Restore release manifest generation before publishing release artifacts.",
        ),
        _file_contains_check(
            name="release_notes_check",
            repo_root=repo_root,
            path=Path("scripts/check_release_notes.py"),
            tokens=[
                "CHANGELOG.md",
                "current_version_section",
                "unreleased_section",
                "project",
                "version",
                "--write-github-release-notes",
            ],
            evidence=(
                "scripts/check_release_notes.py verifies CHANGELOG.md covers the "
                "current package version and renders GitHub release notes before "
                "release packaging."
            ),
            next_step="Restore the release notes checker before publishing packages.",
        ),
        _file_contains_check(
            name="changelog_metadata",
            repo_root=repo_root,
            path=Path("CHANGELOG.md"),
            tokens=[
                "# Changelog",
                "## [Unreleased]",
                "## [0.1.0]",
                "OSS readiness",
                "MCP",
                "CLI",
                "release gate",
            ],
            evidence="CHANGELOG.md exposes versioned release history and pending changes.",
            next_step="Restore CHANGELOG.md before publishing release artifacts.",
        ),
        _file_contains_check(
            name="ci_clean_wheel_hosted_mcp_config_smoke",
            repo_root=repo_root,
            path=Path(".github/workflows/ci.yml"),
            tokens=[
                "uv build --out-dir",
                "policynim init --help",
                "policynim ingest --help",
                "policynim preflight --help",
                "Usage: policynim init",
                "Usage: policynim ingest",
                "Usage: policynim preflight",
                "policynim quickstart --target local-cli --format json",
                "policynim quickstart --target local-mcp --format json",
                "python -m json.tool /tmp/policynim-quickstart-local-cli.json",
                "python -m json.tool /tmp/policynim-quickstart-local-mcp.json",
                "policynim mcp-smoke --format json",
                "python -m json.tool /tmp/policynim-mcp-smoke.json",
                "policynim mcp-config --target hosted-http",
                "python -m json.tool /tmp/policynim-hosted-mcp-config.json",
                "--client claude-code --hosted-url https://example.invalid/mcp",
                "python -m json.tool /tmp/policynim-claude-hosted-mcp-config.json",
                "--validate-json-contract mcp-config-codex-local-stdio",
                "--validate-json-contract mcp-config-claude-code-local-stdio",
                "--validate-json-contract mcp-config-codex-hosted-http",
                "--validate-json-contract mcp-config-claude-code-hosted-http",
                "python3 scripts/oss_readiness_check.py --format launch-issue",
                "## Missing Evidence Collection Commands",
                "Upload package smoke evidence",
                "name: package-smoke-evidence",
                "/tmp/policynim-oss-readiness.json",
                "/tmp/policynim-launch-issue.md",
                "/tmp/policynim-init-help.txt",
                "/tmp/policynim-ingest-help.txt",
                "/tmp/policynim-preflight-help.txt",
                "/tmp/policynim-quickstart.json",
                "/tmp/policynim-quickstart-local-cli.json",
                "/tmp/policynim-quickstart-local-mcp.json",
                "/tmp/policynim-doctor.json",
                "/tmp/policynim-support-bundle.json",
                "/tmp/policynim-mcp-smoke.json",
                "/tmp/policynim-mcp-config.json",
                "/tmp/policynim-claude-mcp-config.json",
                "/tmp/policynim-mcp-smoke-from-codex-config.json",
                "/tmp/policynim-mcp-smoke-from-claude-config.json",
                "/tmp/policynim-hosted-mcp-config.json",
                "/tmp/policynim-claude-hosted-mcp-config.json",
            ],
            evidence=(
                ".github/workflows/ci.yml builds the wheel, parses all first-run "
                "quickstart targets, checks primary CLI help (`init --help`, "
                "`ingest --help`, `preflight --help`), runs MCP stdio smoke, "
                "generated-config MCP smoke, and parses Codex/Claude Code hosted "
                "MCP config JSON. It also validates semantic Codex/Claude Code "
                "MCP config contracts for no-clone installed local stdio and "
                "hosted placeholder/env-var safety, then uploads package-smoke-evidence "
                "for PR reviewers, including the paste-ready launch issue."
            ),
            next_step=(
                "Restore CI clean wheel smoke for all quickstart targets, MCP stdio, "
                "hosted MCP config JSON, semantic MCP config contracts, and reviewable "
                "package-smoke-evidence."
            ),
        ),
        _file_contains_check(
            name="release_workflow_hosted_mcp_config_smoke",
            repo_root=repo_root,
            path=Path(".github/workflows/release.yml"),
            tokens=[
                "policynim quickstart --target local-cli --format json",
                "policynim quickstart --target local-mcp --format json",
                "policynim init --help",
                "policynim ingest --help",
                "policynim preflight --help",
                "Usage: policynim init",
                "Usage: policynim ingest",
                "Usage: policynim preflight",
                "python -m json.tool /tmp/policynim-quickstart-local-cli.json",
                "python -m json.tool /tmp/policynim-quickstart-local-mcp.json",
                "policynim mcp-smoke --format json",
                "python -m json.tool /tmp/policynim-mcp-smoke.json",
                "policynim mcp-smoke --mcp-config-file /tmp/policynim-mcp-config.json",
                "python -m json.tool /tmp/policynim-mcp-smoke-from-codex-config.json",
                "python -m json.tool /tmp/policynim-mcp-smoke-from-claude-config.json",
                "policynim mcp-config --target hosted-http",
                "python -m json.tool /tmp/policynim-hosted-mcp-config.json",
                "--client claude-code --hosted-url https://example.invalid/mcp",
                "python -m json.tool /tmp/policynim-claude-hosted-mcp-config.json",
                "--bearer-token-env-var POLICYNIM_TOKEN",
                "--validate-json-contract mcp-config-codex-local-stdio",
                "--validate-json-contract mcp-config-claude-code-local-stdio",
                "--validate-json-contract mcp-config-codex-hosted-http",
                "--validate-json-contract mcp-config-claude-code-hosted-http",
                "RELEASE_MANIFEST.json",
                "SHA256SUMS",
            ],
            evidence=(
                ".github/workflows/release.yml smokes all first-run quickstart "
                "targets, primary CLI help (`init --help`, `ingest --help`, "
                "`preflight --help`), MCP stdio, generated-config MCP smoke, "
                "Codex/Claude Code hosted MCP config, semantic MCP config contracts "
                "for installed local stdio and hosted "
                "placeholder/env-var safety, and release artifacts."
            ),
            next_step=(
                "Restore release workflow quickstart, MCP stdio smoke, hosted MCP "
                "config smoke, semantic MCP config contracts, and artifact gates."
            ),
        ),
        _file_contains_check(
            name="release_workflow_reviewable_smoke_evidence",
            repo_root=repo_root,
            path=Path(".github/workflows/release.yml"),
            tokens=[
                "Upload release wheel smoke evidence",
                "name: release-wheel-smoke-evidence",
                "/tmp/policynim-init-help.txt",
                "/tmp/policynim-ingest-help.txt",
                "/tmp/policynim-preflight-help.txt",
                "/tmp/policynim-doctor.json",
                "/tmp/policynim-support-bundle.json",
                "Upload standalone smoke evidence",
                "name: standalone-smoke-evidence-${{ matrix.platform }}",
                "smoke-evidence/policynim-standalone-init-help.txt",
                "smoke-evidence/policynim-standalone-ingest-help.txt",
                "smoke-evidence/policynim-standalone-preflight-help.txt",
                "smoke-evidence/policynim-standalone-doctor.json",
                "smoke-evidence/policynim-standalone-support-bundle.json",
                "smoke-evidence/policynim-standalone-mcp-smoke.json",
                "smoke-evidence/policynim-standalone-mcp-config.json",
                "smoke-evidence/policynim-standalone-claude-mcp-config.json",
                "smoke-evidence/policynim-standalone-mcp-smoke-from-codex-config.json",
                "smoke-evidence/policynim-standalone-mcp-smoke-from-claude-config.json",
                'STANDALONE_SMOKE_CWD="$PWD/standalone-smoke-cwd"',
                'cd "$STANDALONE_SMOKE_CWD"',
                "--validate-json-contract mcp-config-codex-local-stdio "
                '"$SMOKE_EVIDENCE/policynim-standalone-mcp-config.json"',
                "--validate-json-contract mcp-config-claude-code-local-stdio "
                '"$SMOKE_EVIDENCE/policynim-standalone-claude-mcp-config.json"',
                "--validate-json-contract mcp-config-codex-hosted-http "
                '"$SMOKE_EVIDENCE/policynim-standalone-hosted-mcp-config.json"',
                "--validate-json-contract mcp-config-claude-code-hosted-http "
                '"$SMOKE_EVIDENCE/policynim-standalone-claude-hosted-mcp-config.json"',
                "Download Python distribution for release",
                "name: python-dist",
                "Download Linux standalone bundle for release",
                "name: standalone-linux-amd64",
                "Download Apple Silicon macOS standalone bundle for release",
                "name: standalone-darwin-arm64",
                "Download Intel macOS standalone bundle for release",
                "name: standalone-darwin-amd64",
                "Download Windows standalone bundle for release",
                "name: standalone-windows-amd64",
            ],
            evidence=(
                ".github/workflows/release.yml uploads release-wheel-smoke-evidence "
                "and standalone-smoke-evidence artifacts, including primary CLI help, "
                "standalone local stdio MCP config evidence, standalone MCP "
                "stdio smoke evidence, and generated-config smoke evidence. "
                "Standalone smoke runs from an empty cwd and "
                "validates semantic MCP config contracts across the four standalone "
                "release platforms before publishing artifacts, "
                "then does not "
                "download those evidence artifacts into public release assets."
            ),
            next_step=(
                "Restore release smoke evidence artifact uploads, standalone empty-cwd "
                "MCP config contract checks, and named public payload downloads before "
                "publishing release candidates."
            ),
        ),
        _file_contains_check(
            name="release_workflow_public_launch_mode",
            repo_root=repo_root,
            path=Path(".github/workflows/release.yml"),
            tokens=[
                "public_launch:",
                "Validate public launch inputs",
                "PUBLIC_LAUNCH: ${{ inputs.public_launch }}",
                "PUBLISH_PYPI: ${{ inputs.publish_pypi }}",
                "public_launch=true requires publish_pypi=true",
                "Record release mode",
                "## PolicyNIM release mode",
                "GitHub-only release candidate",
                "Public launch candidate",
            ],
            evidence=(
                ".github/workflows/release.yml records whether a manual run is a "
                "GitHub-only release candidate or Public launch candidate, and "
                "fails with public_launch=true requires publish_pypi=true so "
                "PyPI trusted-publishing evidence cannot be skipped accidentally."
            ),
            next_step=(
                "Restore the release workflow public-launch mode guard so manual "
                "public launch dispatches require publish_pypi=true and record "
                "the selected release mode in the workflow summary."
            ),
        ),
        _file_contains_check(
            name="release_artifact_attestations",
            repo_root=repo_root,
            path=Path(".github/workflows/release.yml"),
            tokens=[
                "id-token: write",
                "attestations: write",
                "actions/attest@",
                "subject-checksums: release-assets/SHA256SUMS",
                "show-summary: true",
                "Verify release asset attestation",
                'ASSET_PATH="release-assets/install.sh"',
                "gh attestation verify",
                "attestation-evidence/install-sh-attestation.json",
                "Upload release attestation evidence",
                "name: release-attestation-evidence",
            ],
            evidence=(
                ".github/workflows/release.yml uses actions/attest to generate "
                "artifact attestations from the release SHA256SUMS file, verifies "
                "the install.sh release asset with gh attestation verify, and uploads "
                "release-attestation-evidence for maintainer review."
            ),
            next_step=(
                "Restore release artifact attestations and post-attest verification "
                "so users can verify download provenance with GitHub artifact attestations."
            ),
        ),
        _file_contains_check(
            name="installer_provenance_controls",
            repo_root=repo_root,
            path=Path("scripts/install.sh"),
            tokens=[
                "POLICYNIM_VERIFY_ATTESTATION",
                "gh attestation verify",
                "repository_slug",
                "verify_attestation",
            ],
            evidence=(
                "scripts/install.sh supports opt-in install-time provenance "
                "verification with POLICYNIM_VERIFY_ATTESTATION and GitHub CLI."
            ),
            next_step=(
                "Restore installer opt-in attestation verification so users can "
                "require provenance checks before extracting release bundles."
            ),
        ),
        _file_contains_check(
            name="installer_powershell_provenance_controls",
            repo_root=repo_root,
            path=Path("scripts/install.ps1"),
            tokens=[
                "POLICYNIM_VERIFY_ATTESTATION",
                "gh attestation verify",
                "Get-RepositorySlug",
                "Verify-Attestation",
            ],
            evidence=(
                "scripts/install.ps1 supports opt-in install-time provenance "
                "verification with POLICYNIM_VERIFY_ATTESTATION and GitHub CLI."
            ),
            next_step=(
                "Restore PowerShell installer opt-in attestation verification "
                "before publishing Windows standalone install guidance."
            ),
        ),
        _file_contains_check(
            name="hosted_smoke_workflow",
            repo_root=repo_root,
            path=Path(".github/workflows/hosted-smoke.yml"),
            tokens=[
                "POLICYNIM_BETA_MCP_URL",
                "POLICYNIM_BETA_MCP_TOKEN",
                "pytest -q -m live tests/test_hosted_mcp_live.py",
                "--junitxml hosted-smoke-evidence/policynim-hosted-smoke-junit.xml",
                "Upload hosted smoke evidence",
                "name: hosted-smoke-evidence",
            ],
            evidence=(
                ".github/workflows/hosted-smoke.yml keeps deployed MCP smoke opt-in "
                "and uploads hosted-smoke-evidence/policynim-hosted-smoke-junit.xml "
                "for maintainer review."
            ),
            next_step="Restore secret-gated hosted MCP live smoke workflow.",
        ),
        _file_contains_check(
            name="support_bundle_public_redaction",
            repo_root=repo_root,
            path=Path("src/policynim/interfaces/cli.py"),
            tokens=[
                "--include-local-paths",
                "_build_support_bundle_first_run_report",
                "quickstart_command",
                "client_commands",
                "agent_workflows",
                "hosted_mcp",
                "local_mcp",
                "_redact_support_bundle_paths",
                "<repo-root>",
                "<python-executable>",
                "local path prefixes are redacted",
            ],
            evidence=(
                "policynim support-bundle redacts local path prefixes by default "
                "for public issues, includes first-run quickstart target context, "
                "hosted client_commands for Codex and Claude Code, hosted_url and "
                "beta_portal_url, copyable agent_workflows, and keeps exact paths "
                "behind --include-local-paths."
            ),
            next_step=(
                "Restore public-safe support-bundle first-run context and path "
                "redaction before broad OSS issue intake."
            ),
        ),
        _file_contains_check(
            name="maintainer_metadata",
            repo_root=repo_root,
            path=Path("CONTRIBUTING.md"),
            tokens=[
                "uv run policynim doctor --format json",
                "uv run python scripts/release_check.py",
                "docs/oss-readiness-audit.md#high-value-pr-sequence",
                "one user-facing thesis",
                "one primary evidence surface",
                "bounded rollback story",
                "No API keys",
            ],
            evidence=(
                "CONTRIBUTING.md sets first-run, high-value PR lane, release, "
                "and secret-safety expectations."
            ),
            next_step=(
                "Restore contributor verification, high-value PR lane, and secret-safety guidance."
            ),
        ),
        _file_contains_check(
            name="pull_request_template_metadata",
            repo_root=repo_root,
            path=Path(".github/PULL_REQUEST_TEMPLATE.md"),
            tokens=[
                "docs/oss-readiness-audit.md#high-value-pr-sequence",
                "one user-facing thesis",
                "one primary evidence surface",
                "bounded rollback story",
                "First-run and hosted MCP onboarding",
                "Local CLI and MCP verification loop",
                "Installability and release trust",
                "SQLite migration and storage contract",
                "Maintainer trust and public launch proof",
                "package-smoke-evidence",
                "No API keys",
            ],
            evidence=(
                ".github/PULL_REQUEST_TEMPLATE.md keeps high-value PR lanes, "
                "review evidence, package-smoke-evidence, and secret-safety "
                "expectations visible before review."
            ),
            next_step=(
                "Restore the PR template lane selector, evidence checklist, and "
                "secret-safety checklist before broad public contribution intake."
            ),
        ),
        _file_contains_check(
            name="github_triage_metadata",
            repo_root=repo_root,
            path=Path(".github/labels.yml"),
            tokens=[
                "status/blocked-external",
                "surface/hosted-mcp",
                "needs/codeowner-review",
                "needs/live-check",
                "needs/launch-evidence",
            ],
            evidence=(
                ".github/labels.yml tracks external blockers, hosted MCP, "
                "live checks, and launch evidence."
            ),
            next_step="Restore the GitHub label taxonomy before opening broad public triage.",
        ),
        _file_contains_check(
            name="github_topic_metadata",
            repo_root=repo_root,
            path=Path(".github/topics.yml"),
            tokens=[
                "ai-agents",
                "mcp",
                "model-context-protocol",
                "nvidia-nim",
                "preflight",
                "verification",
            ],
            evidence=(
                ".github/topics.yml tracks discoverability topics for MCP, CLI, "
                "NVIDIA NIM, policy preflight, and verification users."
            ),
            next_step="Restore the GitHub topic taxonomy before broad public promotion.",
        ),
        _file_contains_check(
            name="public_launch_issue_template",
            repo_root=repo_root,
            path=Path(".github/ISSUE_TEMPLATE/public_launch_evidence.yml"),
            tokens=[
                "Public Launch Evidence",
                "type/launch",
                "needs/launch-evidence",
                "Release tag",
                "Strict public readiness output",
                "Generated launch issue",
                "Attached evidence records",
                "Remaining external proof",
                "scripts/release_check.py --strict-public --external-evidence-file",
                "scripts/oss_readiness_check.py --external-evidence-file",
                "scripts/collect_launch_evidence.py",
                "docs/launch-evidence.json",
            ],
            evidence=(
                ".github/ISSUE_TEMPLATE/public_launch_evidence.yml provides a "
                "public launch evidence issue form for strict public readiness, "
                "generated launch issue text, attached evidence records, and "
                "remaining external proof."
            ),
            next_step=(
                "Restore the public launch evidence issue form before launch-ready "
                "claims are routed through public GitHub issues."
            ),
        ),
        _file_contains_check(
            name="codeowners_metadata",
            repo_root=repo_root,
            path=Path(".github/CODEOWNERS"),
            tokens=[
                "* @nnennandukwe",
                "/.github/workflows/ @nnennandukwe",
                "/pyproject.toml @nnennandukwe",
                "/scripts/release_check.py @nnennandukwe",
                "/src/policynim/interfaces/cli.py @nnennandukwe",
                "/src/policynim/interfaces/mcp.py @nnennandukwe",
                "/SECURITY.md @nnennandukwe",
            ],
            evidence=".github/CODEOWNERS maps release, MCP, CLI, and security ownership.",
            next_step="Restore CODEOWNERS before broad public contribution intake.",
        ),
        _file_contains_check(
            name="dependabot_metadata",
            repo_root=repo_root,
            path=Path(".github/dependabot.yml"),
            tokens=[
                'package-ecosystem: "uv"',
                'package-ecosystem: "github-actions"',
                'interval: "weekly"',
                "open-pull-requests-limit: 3",
                "needs/codeowner-review",
            ],
            evidence=".github/dependabot.yml bounds uv and GitHub Actions update flow.",
            next_step="Restore bounded Dependabot updates for dependencies and workflows.",
        ),
        _file_contains_check(
            name="label_sync_script",
            repo_root=repo_root,
            path=Path("scripts/sync_github_labels.py"),
            tokens=[
                "Dry-run or apply",
                "--apply",
                "--live",
                "live/apply label sync",
                "`--live` to inspect",
                "`--apply` only when ready",
                "gh label list --json name,color,description --limit 1000",
            ],
            evidence=(
                "scripts/sync_github_labels.py supports offline dry-run, live "
                "dry-run, authenticated apply, and actionable gh recovery guidance "
                "for label changes."
            ),
            next_step="Restore dry-run-first GitHub label sync tooling.",
        ),
        _file_contains_check(
            name="topic_sync_script",
            repo_root=repo_root,
            path=Path("scripts/sync_github_topics.py"),
            tokens=[
                "Dry-run or apply",
                "--apply",
                "--live",
                "live/apply topic sync",
                "`--live` to inspect",
                "`--apply` only when ready",
                "gh repo view",
                "_apply_plan_entry",
                "--add-topic",
                "--remove-topic",
            ],
            evidence=(
                "scripts/sync_github_topics.py supports offline dry-run, live "
                "dry-run, authenticated apply, and actionable gh recovery guidance "
                "for topic changes."
            ),
            next_step="Restore dry-run-first GitHub topic sync tooling.",
        ),
        _file_contains_check(
            name="external_evidence_freshness_gate",
            repo_root=repo_root,
            path=Path("scripts/oss_readiness_check.py"),
            tokens=[
                "MAX_EXTERNAL_EVIDENCE_AGE_DAYS",
                "MAX_EXTERNAL_EVIDENCE_FUTURE_SKEW_MINUTES",
                "_verified_at_freshness_error",
                "verified_at is older than",
                "_reference_format_error",
                "GitHub Actions run URL",
                "gh label list command",
            ],
            evidence=(
                "scripts/oss_readiness_check.py rejects stale or future-dated "
                "external launch evidence and wrong reference shapes before strict "
                "public readiness can pass."
            ),
            next_step=(
                "Restore external evidence freshness and reference-shape validation "
                "before accepting public launch proof."
            ),
        ),
        _file_contains_check(
            name="launch_evidence_collector",
            repo_root=repo_root,
            path=Path("scripts/collect_launch_evidence.py"),
            tokens=[
                "gh release view",
                "gh label list --json name,color,description --limit 1000",
                "gh repo view --json repositoryTopics,nameWithOwner",
                "RELEASE_MANIFEST.json",
                "Trusted publishing state",
                "--pypi-publish-run-url",
                "--pypi-install-smoke",
                "pypi_install_smoke",
                "--github-install-smoke",
                "github_release_install_smoke",
                "_installer_guidance_contract_errors",
                "_installed_cli_first_run_json_contract_errors",
                "invalid_first_run_contract",
                "expected_pypi_distribution_files",
                "missing_distribution_files",
                "headSha",
                "release_sha_mismatch",
                "--release-attestation-asset-name",
                "gh attestation verify",
                "_attestation_subject_names",
                "RELEASE_METADATA_ASSET_NAMES",
                "_validate_release_metadata_files",
                "release_metadata_invalid",
                "--hosted-mcp-url",
                "--hosted-smoke-run-url",
                "HOSTED_SMOKE_ARTIFACT_NAME",
                "HOSTED_SMOKE_JUNIT_FILENAME",
                "expected_hosted_smoke_tests",
                "--mcp-client-evidence-file",
                "--write-mcp-client-evidence-template",
                "--write-mcp-client-evidence-record",
                "--mcp-client-setup-command",
                "--mcp-client-hosted-url",
                "--mcp-client-reference",
                "PLACEHOLDER_REFERENCE_MARKERS",
                "placeholder_reference",
                "placeholder_setup_command",
                "setup_command_mismatch",
                "github_topics_applied",
                "--require-requested-probes",
                "requested_probe_failures",
                "--write-external-evidence-file",
                "--merge-existing",
            ],
            evidence=(
                "scripts/collect_launch_evidence.py opt-in checks live GitHub/PyPI publish "
                "and hosted MCP domain/smoke/client facts, cross-checks GitHub "
                "labels and topics, release manifests and SHA256SUMS, verifies attested subjects, "
                "requires the selected release asset to appear in those subjects, "
                "PyPI wheel/sdist files, matches trusted-publish run SHA to the "
                "release target, runs public PyPI install smoke and "
                "github_release_install_smoke on request "
                "for install.sh guidance, primary command help, semantic first-run "
                "JSON across all quickstart targets, support-bundle hosted "
                "client_commands for Codex and Claude Code, support-bundle "
                "hosted_url/beta_portal_url token flow, doctor JSON, and local MCP "
                "config JSON, "
                "matches hosted-smoke run SHA to the release target, hosted smoke "
                "JUnit artifacts, placeholder client-session references, and "
                "client setup-command mismatches, "
                "writes safe client-session templates or completed records with "
                "hosted URL-derived setup commands, can fail on requested probe "
                "failures, then safely merges launch evidence."
            ),
            next_step="Restore opt-in launch evidence collection before public launch.",
        ),
        _file_contains_check(
            name="oss_readiness_audit_doc",
            repo_root=repo_root,
            path=Path("docs/oss-readiness-audit.md"),
            tokens=[
                "Hosted MCP first run",
                "Installed CLI first run",
                "Release and CI trust path",
                "Maintainer trust path",
                "scripts/release_check.py",
                "--format launch-issue",
            ],
            evidence="docs/oss-readiness-audit.md tracks the developer journey and evidence map.",
            next_step="Restore the OSS readiness audit before public launch planning.",
        ),
        _file_contains_check(
            name="public_launch_runbook",
            repo_root=repo_root,
            path=Path("docs/public-launch-runbook.md"),
            tokens=[
                "uv run python scripts/release_check.py --dry-run --format json",
                "uv run python scripts/oss_readiness_check.py --format markdown",
                "uv run python scripts/oss_readiness_check.py --format launch-issue",
                "--write-external-evidence-template docs/launch-evidence.json",
                "--strict-public --external-evidence-file docs/launch-evidence.json",
                "public_launch=true",
                "publish_pypi=true",
                "GitHub release artifacts",
                "PyPI",
                "hosted MCP domain",
                "Hosted Beta Smoke",
                "GitHub labels",
                "real MCP client session",
                "do not include tokens",
                "public_ready",
                "hold_external_missing",
            ],
            evidence=(
                "docs/public-launch-runbook.md turns local readiness into an "
                "external proof workflow."
            ),
            next_step=(
                "Restore the public launch runbook so maintainers can collect "
                "external proof before broad launch claims."
            ),
        ),
        _file_contains_check(
            name="release_guide_local_gate",
            repo_root=repo_root,
            path=Path("docs/release.md"),
            tokens=[
                "policynim quickstart",
                "uv run python scripts/release_check.py",
                "public_launch=true",
                "publish_pypi=true",
                "SHIP only when all required evidence is present",
                "HOLD when any release artifact is missing",
            ],
            evidence="docs/release.md documents the local ship/hold release gate.",
            next_step="Restore release guide alignment with scripts/release_check.py.",
        ),
    ]


def _project_version(repo_root: Path) -> str:
    try:
        pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "<pyproject-version>"
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return "<pyproject-version>"
    version = project.get("version")
    if not isinstance(version, str) or not version:
        return "<pyproject-version>"
    return version


def _launch_evidence_commands(repo_root: Path) -> dict[str, list[str]]:
    version = _project_version(repo_root)
    release_tag = f"v{version}"
    release_tag_option = f"--release-tag {release_tag} "
    write_evidence_options = (
        "--write-external-evidence-file docs/launch-evidence.json --merge-existing --format json"
    )
    return {
        "github_release_artifacts": [
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            f"{write_evidence_options}",
        ],
        "github_release_install_smoke": [
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            "--github-install-smoke "
            "--require-requested-probes "
            f"{write_evidence_options}",
        ],
        "github_artifact_attestations": [
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            f"--release-attestation-asset-name {DEFAULT_ATTESTATION_ASSET_NAME} "
            "--require-requested-probes "
            f"{write_evidence_options}",
        ],
        "pypi_project": [
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            "--pypi-publish-run-url "
            "'https://github.com/<owner>/<repo>/actions/runs/<run-id>' "
            "--require-requested-probes "
            f"{write_evidence_options}",
        ],
        "pypi_install_smoke": [
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            "--pypi-install-smoke "
            "--require-requested-probes "
            f"{write_evidence_options}",
        ],
        "hosted_mcp_domain": [
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            "--hosted-mcp-url 'https://<railway-domain>/mcp' "
            "--require-requested-probes "
            f"{write_evidence_options}",
        ],
        "hosted_beta_live_smoke": [
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            "--hosted-smoke-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>' "
            "--require-requested-probes "
            f"{write_evidence_options}",
        ],
        "github_labels_applied": [
            "gh auth status",
            "uv run python scripts/sync_github_labels.py --live --format json",
            "uv run python scripts/sync_github_labels.py --apply --format json",
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            f"{write_evidence_options}",
        ],
        "github_topics_applied": [
            "gh auth status",
            "uv run python scripts/sync_github_topics.py --live --format json",
            "uv run python scripts/sync_github_topics.py --apply --format json",
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            f"{write_evidence_options}",
        ],
        "real_mcp_client_session": [
            "uv run python scripts/collect_launch_evidence.py "
            "--write-mcp-client-evidence-template launch-notes/codex-mcp-session.json "
            "--mcp-client-template-client codex --mcp-client-template-transport hosted-http",
            "uv run python scripts/collect_launch_evidence.py "
            "--write-mcp-client-evidence-record launch-notes/codex-mcp-session.json "
            "--mcp-client-template-client codex --mcp-client-template-transport hosted-http "
            "--mcp-client-hosted-url 'https://<railway-domain>/mcp' "
            "--mcp-client-reference '<sanitized-session-reference>'",
            "uv run python scripts/collect_launch_evidence.py "
            f"{release_tag_option}"
            "--mcp-client-evidence-file launch-notes/codex-mcp-session.json "
            "--require-requested-probes "
            f"{write_evidence_options}",
        ],
    }


def _external_next_step(*, repo_root: Path, name: str, guidance: str) -> str:
    commands = _launch_evidence_commands(repo_root).get(name, [])
    if not commands:
        return guidance
    command_label = (
        "Evidence collection command" if len(commands) == 1 else "Evidence collection commands"
    )
    formatted_commands = "; ".join(f"`{command}`" for command in commands)
    return f"{guidance} {command_label}: {formatted_commands}."


def _strict_public_local_checks(
    *,
    repo_root: Path,
    external_evidence_records: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    hosted_record = external_evidence_records.get("hosted_mcp_domain")
    if hosted_record is None:
        return []

    mcp_url, beta_url = _hosted_onboarding_urls(hosted_record["reference"])
    required_docs = {
        Path("README.md"): (mcp_url, beta_url),
        Path("docs/agent-workflows.md"): (mcp_url,),
        Path("examples/codex/README.md"): (mcp_url, beta_url),
        Path("examples/claude-code/README.md"): (mcp_url, beta_url),
    }
    missing: list[str] = []
    for relative_path, tokens in required_docs.items():
        absolute_path = repo_root / relative_path
        try:
            text = absolute_path.read_text(encoding="utf-8")
        except OSError as exc:
            missing.append(f"{relative_path} could not be read: {type(exc).__name__}: {exc}")
            continue
        missing_tokens = [token for token in tokens if token not in text]
        if missing_tokens:
            missing.append(f"{relative_path} missing {', '.join(missing_tokens)}")

    if missing:
        return [
            _failed_check(
                name="strict_public_hosted_onboarding_docs",
                evidence=(
                    f"Verified hosted MCP URL {mcp_url} is not published through "
                    f"the first-run onboarding docs: {'; '.join(missing)}."
                ),
                next_step=(
                    "Replace hosted URL placeholders in README.md, docs/agent-workflows.md, "
                    "examples/codex/README.md, and examples/claude-code/README.md after "
                    "hosted_mcp_domain evidence verifies the public /mcp origin."
                ),
            ),
        ]
    return [
        {
            "name": "strict_public_hosted_onboarding_docs",
            "status": "passed",
            "scope": "local",
            "evidence": (
                f"First-run README, agent workflow, Codex, and Claude Code docs "
                f"publish the verified hosted MCP URL {mcp_url} and beta portal {beta_url}."
            ),
            "next_step": "",
        },
    ]


def _hosted_onboarding_urls(reference: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(reference)
    return (
        urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/mcp", "", "", "")),
        urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/beta", "", "", "")),
    )


def _external_checks(repo_root: Path, external_evidence: dict[str, str]) -> list[dict[str, str]]:
    checks = [
        _missing_external_check(
            name="github_release_artifacts",
            evidence="No live GitHub release asset set is verified by this local command.",
            next_step=_external_next_step(
                repo_root=repo_root,
                name="github_release_artifacts",
                guidance=(
                    "Publish or inspect the draft release and attach RELEASE_MANIFEST.json, "
                    "SHA256SUMS, wheel, sdist, installers, and standalone archive evidence."
                ),
            ),
        ),
        _missing_external_check(
            name="github_release_install_smoke",
            evidence=("No clean GitHub release installer smoke is verified by this local command."),
            next_step=_external_next_step(
                repo_root=repo_root,
                name="github_release_install_smoke",
                guidance=(
                    "Run the published GitHub install.sh in a clean HOME and "
                    "attach first-run CLI smoke evidence."
                ),
            ),
        ),
        _missing_external_check(
            name="github_artifact_attestations",
            evidence=(
                "No live GitHub artifact attestation verification is verified "
                "by this local command."
            ),
            next_step=_external_next_step(
                repo_root=repo_root,
                name="github_artifact_attestations",
                guidance="Wait until the Release workflow has generated attestations.",
            ),
        ),
        _missing_external_check(
            name="pypi_project",
            evidence=(
                "No complete PyPI package and trusted-publishing evidence record "
                "is verified by this local command."
            ),
            next_step=_external_next_step(
                repo_root=repo_root,
                name="pypi_project",
                guidance=(
                    "Attach public PyPI project/version details and publish-pypi job evidence "
                    "from the Release workflow."
                ),
            ),
        ),
        _missing_external_check(
            name="pypi_install_smoke",
            evidence=("No clean public PyPI install smoke is verified by this local command."),
            next_step=_external_next_step(
                repo_root=repo_root,
                name="pypi_install_smoke",
                guidance=(
                    "Install the public PyPI package in a clean environment and "
                    "attach first-run CLI smoke evidence."
                ),
            ),
        ),
        _missing_external_check(
            name="hosted_mcp_domain",
            evidence="No public hosted MCP domain is verified by this local command.",
            next_step=_external_next_step(
                repo_root=repo_root,
                name="hosted_mcp_domain",
                guidance=(
                    "Attach the deployed hosted /mcp URL and /healthz result from the "
                    "current Railway or hosted environment, then update first-run docs "
                    "to the verified hosted origin."
                ),
            ),
        ),
        _missing_external_check(
            name="hosted_beta_live_smoke",
            evidence="No live hosted MCP workflow run is verified by this local command.",
            next_step=_external_next_step(
                repo_root=repo_root,
                name="hosted_beta_live_smoke",
                guidance=(
                    "Run the Hosted Beta Smoke workflow with POLICYNIM_BETA_MCP_URL "
                    "and POLICYNIM_BETA_MCP_TOKEN."
                ),
            ),
        ),
        _missing_external_check(
            name="github_labels_applied",
            evidence="No authenticated GitHub label application is verified by this local command.",
            next_step=_external_next_step(
                repo_root=repo_root,
                name="github_labels_applied",
                guidance=("Apply the label taxonomy from an authenticated maintainer session."),
            ),
        ),
        _missing_external_check(
            name="github_topics_applied",
            evidence="No authenticated GitHub topic application is verified by this local command.",
            next_step=_external_next_step(
                repo_root=repo_root,
                name="github_topics_applied",
                guidance=(
                    "Apply the repository topic taxonomy from an authenticated maintainer session."
                ),
            ),
        ),
        _missing_external_check(
            name="real_mcp_client_session",
            evidence=(
                "No real Codex or Claude Code client session is verified by this local command."
            ),
            next_step=_external_next_step(
                repo_root=repo_root,
                name="real_mcp_client_session",
                guidance=(
                    "Attach a real client session transcript or screenshot showing generated "
                    "hosted or local MCP config loaded through a secret-safe setup command "
                    "and policy_preflight callable."
                ),
            ),
        ),
    ]
    return [_with_external_evidence(check, external_evidence) for check in checks]


def _load_external_evidence(
    external_evidence_file: Path | None,
    *,
    repo_root: Path,
) -> tuple[dict[str, str], dict[str, dict[str, str]], dict[str, str] | None]:
    if external_evidence_file is None:
        return {}, {}, None
    try:
        raw_payload = json.loads(external_evidence_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (
            {},
            {},
            {
                "name": "external_evidence_file",
                "status": "failed",
                "scope": "external",
                "evidence": (
                    f"{external_evidence_file} could not be loaded: {type(exc).__name__}: {exc}"
                ),
                "next_step": (
                    "Provide a JSON object that maps each external check name to "
                    "an evidence record with summary, reference, verified_by, and verified_at."
                ),
            },
        )
    if not isinstance(raw_payload, dict):
        return (
            {},
            {},
            {
                "name": "external_evidence_file",
                "status": "failed",
                "scope": "external",
                "evidence": f"{external_evidence_file} must contain a JSON object.",
                "next_step": (
                    "Replace the evidence file with an object keyed by external check name."
                ),
            },
        )
    loaded_names = {key for key in raw_payload if isinstance(key, str)}
    unknown_names = sorted(loaded_names - EXTERNAL_CHECK_NAMES)
    missing_names = sorted(EXTERNAL_CHECK_NAMES - loaded_names)
    validation_errors: list[str] = []
    if unknown_names:
        validation_errors.append(f"unknown checks: {', '.join(unknown_names)}")
    if missing_names:
        validation_errors.append(f"missing checks: {', '.join(missing_names)}")
    evidence: dict[str, str] = {}
    evidence_records: dict[str, dict[str, str]] = {}
    for key, raw_value in raw_payload.items():
        if not isinstance(key, str):
            continue
        if key not in EXTERNAL_CHECK_NAMES:
            continue
        normalized_record, normalized_fields, record_errors = _normalize_evidence_record(
            key,
            raw_value,
            repo_root=repo_root,
        )
        if record_errors:
            validation_errors.extend(record_errors)
            continue
        if normalized_record:
            evidence[key] = normalized_record
            evidence_records[key] = normalized_fields
    if validation_errors:
        return (
            evidence,
            evidence_records,
            {
                "name": "external_evidence_file",
                "status": "failed",
                "scope": "external",
                "evidence": (
                    f"{external_evidence_file} is invalid: "
                    f"{_format_validation_errors(validation_errors)}."
                ),
                "next_step": (
                    "Use docs/launch-evidence.example.json and provide non-empty "
                    "summary, reference, verified_by, and verified_at fields for each check."
                ),
            },
        )
    return (
        evidence,
        evidence_records,
        {
            "name": "external_evidence_file",
            "status": "passed",
            "scope": "external",
            "evidence": f"Loaded external evidence from {external_evidence_file}.",
            "next_step": "",
        },
    )


def _format_validation_errors(errors: list[str]) -> str:
    return "; ".join(errors)


def _expected_release_assets(repo_root: Path) -> list[str]:
    version = _project_version(repo_root)
    if version == "<pyproject-version>":
        return []
    release_tag = f"v{version}"
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


def _summary_contract_errors(
    *,
    check_name: str,
    summary: str,
    repo_root: Path,
) -> list[str]:
    if check_name != "github_release_artifacts":
        return []
    return [
        f"{check_name}.summary must mention current expected release asset {asset}"
        for asset in _expected_release_assets(repo_root)
        if asset not in summary
    ]


def _write_external_evidence_template(path: Path, *, force: bool) -> int:
    if path.exists() and not force:
        print(
            (f"Error: {path} already exists. Pass --force to overwrite it with a blank template."),
            file=sys.stderr,
        )
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_external_evidence_template(), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote external evidence template: {path}")
    return 0


def _external_evidence_template() -> dict[str, dict[str, str]]:
    return {
        check_name: {field: "" for field in EVIDENCE_RECORD_FIELDS}
        for check_name in sorted(EXTERNAL_CHECK_NAMES)
    }


def _normalize_evidence_record(
    check_name: str,
    raw_value: object,
    *,
    repo_root: Path,
) -> tuple[str, dict[str, str], list[str]]:
    if not isinstance(raw_value, dict):
        return (
            "",
            {},
            [(f"{check_name} must be an object with {', '.join(EVIDENCE_RECORD_FIELDS)}")],
        )

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
    if not errors and _is_blank_evidence_record(raw_value):
        return "", {}, []

    normalized: dict[str, str] = {}
    empty_or_invalid_fields: list[str] = []
    for field in EVIDENCE_RECORD_FIELDS:
        raw_field_value = raw_value.get(field)
        if not isinstance(raw_field_value, str) or not raw_field_value.strip():
            empty_or_invalid_fields.append(field)
            continue
        normalized[field] = raw_field_value.strip()
    if empty_or_invalid_fields:
        errors.append(
            f"{check_name} has empty or non-string fields: {', '.join(empty_or_invalid_fields)}"
        )

    verified_at = normalized.get("verified_at")
    if verified_at is not None:
        parsed_verified_at = _parse_timezone_aware_iso_timestamp(verified_at)
        if parsed_verified_at is None:
            errors.append(f"{check_name}.verified_at must be an ISO 8601 timestamp with timezone")
        else:
            freshness_error = _verified_at_freshness_error(
                check_name=check_name,
                verified_at=parsed_verified_at,
            )
            if freshness_error:
                errors.append(freshness_error)
    reference = normalized.get("reference")
    if reference is not None:
        if _contains_placeholder_reference(reference):
            errors.append(f"{check_name}.reference contains placeholder text")
        reference_error = _reference_format_error(check_name, reference)
        if reference_error:
            errors.append(reference_error)
    summary = normalized.get("summary")
    if summary is not None:
        errors.extend(
            _summary_contract_errors(
                check_name=check_name,
                summary=summary,
                repo_root=repo_root,
            )
        )

    if errors:
        return "", {}, errors
    return (
        f"{normalized['summary']} "
        f"(reference: {normalized['reference']}; "
        f"verified_by: {normalized['verified_by']}; "
        f"verified_at: {normalized['verified_at']})",
        normalized,
        [],
    )


def _is_blank_evidence_record(raw_value: dict[object, object]) -> bool:
    for field in EVIDENCE_RECORD_FIELDS:
        value = raw_value.get(field)
        if not isinstance(value, str) or value.strip():
            return False
    return True


def _is_timezone_aware_iso_timestamp(value: str) -> bool:
    return _parse_timezone_aware_iso_timestamp(value) is not None


def _parse_timezone_aware_iso_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _verified_at_freshness_error(
    *,
    check_name: str,
    verified_at: datetime,
) -> str:
    now = datetime.now(UTC)
    verified_at_utc = verified_at.astimezone(UTC)
    future_skew = timedelta(minutes=MAX_EXTERNAL_EVIDENCE_FUTURE_SKEW_MINUTES)
    if verified_at_utc > now + future_skew:
        return (
            f"{check_name}.verified_at is more than "
            f"{MAX_EXTERNAL_EVIDENCE_FUTURE_SKEW_MINUTES} minutes in the future"
        )
    max_age = timedelta(days=MAX_EXTERNAL_EVIDENCE_AGE_DAYS)
    if now - verified_at_utc > max_age:
        return f"{check_name}.verified_at is older than {MAX_EXTERNAL_EVIDENCE_AGE_DAYS} days"
    return ""


def _contains_placeholder_reference(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in PLACEHOLDER_REFERENCE_MARKERS)


def _reference_format_error(check_name: str, reference: str) -> str:
    if check_name == "github_release_artifacts" and not _is_github_release_reference(
        reference,
    ):
        return f"{check_name}.reference must be a GitHub release tag URL"
    if check_name == "github_release_install_smoke" and not _is_github_release_reference(
        reference,
    ):
        return f"{check_name}.reference must be a GitHub release tag URL"
    if check_name == "github_artifact_attestations" and not (
        _is_github_release_reference(reference) and bool(urllib.parse.urlparse(reference).fragment)
    ):
        return (
            f"{check_name}.reference must be a GitHub release tag URL with "
            "the attested asset name as a fragment"
        )
    if check_name == "pypi_project" and not _is_github_actions_run_reference(reference):
        return f"{check_name}.reference must be a GitHub Actions run URL"
    if check_name == "pypi_install_smoke" and not _is_pypi_project_version_reference(
        reference,
    ):
        return f"{check_name}.reference must be a PyPI project version URL"
    if check_name == "hosted_mcp_domain" and not _is_hosted_mcp_reference(reference):
        return f"{check_name}.reference must be an HTTPS hosted /healthz or /mcp URL"
    if check_name == "hosted_beta_live_smoke" and not _is_github_actions_run_reference(
        reference,
    ):
        return f"{check_name}.reference must be a GitHub Actions run URL"
    if check_name == "github_labels_applied" and reference != GITHUB_LABEL_LIST_REFERENCE:
        return f"{check_name}.reference must be the gh label list command"
    if check_name == "github_topics_applied" and reference != GITHUB_TOPIC_LIST_REFERENCE:
        return f"{check_name}.reference must be the gh repo view command"
    return ""


def _is_github_release_reference(reference: str) -> bool:
    parsed = urllib.parse.urlparse(reference)
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and len(parts) >= 5
        and parts[2] == "releases"
        and parts[3] == "tag"
        and bool(parts[4])
    )


def _is_github_actions_run_reference(reference: str) -> bool:
    parsed = urllib.parse.urlparse(reference)
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and len(parts) >= 5
        and parts[2] == "actions"
        and parts[3] == "runs"
        and parts[4].isdigit()
    )


def _is_pypi_project_version_reference(reference: str) -> bool:
    parsed = urllib.parse.urlparse(reference)
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme == "https"
        and parsed.netloc == "pypi.org"
        and len(parts) >= 3
        and parts[0] == "project"
        and parts[1] == "policynim"
        and bool(parts[2])
    )


def _is_hosted_mcp_reference(reference: str) -> bool:
    parsed = urllib.parse.urlparse(reference)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.path.rstrip("/") in {"/healthz", "/mcp"}
    )


def _with_external_evidence(
    check: dict[str, str],
    external_evidence: dict[str, str],
) -> dict[str, str]:
    evidence = external_evidence.get(check["name"])
    if evidence is None:
        return check
    return {
        "name": check["name"],
        "status": "passed",
        "scope": "external",
        "evidence": evidence,
        "next_step": "",
    }


def _file_contains_check(
    *,
    name: str,
    repo_root: Path,
    path: Path,
    tokens: list[str],
    evidence: str,
    next_step: str,
) -> dict[str, str]:
    absolute_path = repo_root / path
    try:
        text = absolute_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _failed_check(
            name=name,
            evidence=f"{path} could not be read: {type(exc).__name__}: {exc}",
            next_step=next_step,
        )

    missing = [token for token in tokens if token not in text]
    if missing:
        return _failed_check(
            name=name,
            evidence=f"{path} is missing required text: {', '.join(missing)}.",
            next_step=next_step,
        )
    return {
        "name": name,
        "status": "passed",
        "scope": "local",
        "evidence": evidence,
        "next_step": "",
    }


def _failed_check(*, name: str, evidence: str, next_step: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "failed",
        "scope": "local",
        "evidence": evidence,
        "next_step": next_step,
    }


def _missing_external_check(*, name: str, evidence: str, next_step: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "missing_external",
        "scope": "external",
        "evidence": evidence,
        "next_step": next_step,
    }


def _render_text_summary(result: dict[str, Any]) -> str:
    lines = [
        "PolicyNIM OSS readiness",
        f"Decision: {result['decision']}",
        f"Repository: {result['repo_root']}",
        f"Local required passed: {str(result['local_required_passed']).lower()}",
        f"External required passed: {str(result['external_required_passed']).lower()}",
        "",
        "Checks:",
    ]
    for check in result["checks"]:
        lines.append(f"- {check['status']}: {check['name']} ({check['scope']})")
        lines.append(f"  Evidence: {check['evidence']}")
        if check["next_step"]:
            lines.append(f"  Next: {check['next_step']}")
    return "\n".join(lines)


def _render_markdown_summary(result: dict[str, Any]) -> str:
    checks = result["checks"]
    passed_local = [
        check for check in checks if check["scope"] == "local" and check["status"] == "passed"
    ]
    local_failures = [
        check for check in checks if check["scope"] == "local" and check["status"] != "passed"
    ]
    external_passed = [
        check for check in checks if check["scope"] == "external" and check["status"] == "passed"
    ]
    external_missing = [
        check for check in checks if check["scope"] == "external" and check["status"] != "passed"
    ]
    lines = [
        "# PolicyNIM OSS Readiness",
        "",
        f"- Decision: `{result['decision']}`",
        f"- Repository: `{result['repo_root']}`",
        f"- Local required passed: `{str(result['local_required_passed']).lower()}`",
        f"- External required passed: `{str(result['external_required_passed']).lower()}`",
        "",
        "## Passed Local Checks",
    ]
    lines.extend(_render_markdown_check_items(passed_local))
    lines.extend(["", "## Local Failures"])
    lines.extend(_render_markdown_check_items(local_failures))
    lines.extend(["", "## Verified External Evidence"])
    lines.extend(_render_markdown_check_items(external_passed))
    lines.extend(["", "## Missing External Evidence"])
    lines.extend(_render_markdown_check_items(external_missing))
    return "\n".join(lines)


def _render_markdown_check_items(checks: list[dict[str, str]]) -> list[str]:
    if not checks:
        return ["None."]
    lines: list[str] = []
    for check in checks:
        lines.extend(
            [
                f"### {check['name']}",
                "",
                f"- Status: `{check['status']}`",
                f"- Evidence: {check['evidence']}",
            ]
        )
        if check["next_step"]:
            lines.append(f"- Next step: {check['next_step']}")
        lines.append("")
    return lines[:-1] if lines else lines


def _render_launch_issue(result: dict[str, Any]) -> str:
    repo_root = Path(str(result["repo_root"]))
    checks = result["checks"]
    local_failures = [
        check for check in checks if check["scope"] == "local" and check["status"] != "passed"
    ]
    evidence_file_checks = [
        check
        for check in checks
        if check["scope"] == "external" and check["name"] == "external_evidence_file"
    ]
    external_checks = [
        check
        for check in checks
        if check["scope"] == "external" and check["name"] in EXTERNAL_CHECK_NAMES
    ]
    lines = [
        "# PolicyNIM Public Launch Evidence",
        "",
        f"Decision: `{result['decision']}`",
        f"Local required passed: `{str(result['local_required_passed']).lower()}`",
        f"External required passed: `{str(result['external_required_passed']).lower()}`",
        "",
    ]
    if evidence_file_checks:
        lines.append("## Evidence File")
        for check in evidence_file_checks:
            lines.extend(
                [
                    f"- Status: `{check['status']}`",
                    f"- Evidence: {check['evidence']}",
                ]
            )
            if check["next_step"]:
                lines.append(f"- Next: {check['next_step']}")
        lines.append("")
    lines.append("## External Evidence Checklist")
    for check in external_checks:
        checkbox = "x" if check["status"] == "passed" else " "
        lines.extend(
            [
                f"- [{checkbox}] `{check['name']}`",
                f"  - Status: `{check['status']}`",
                f"  - Evidence: {check['evidence']}",
            ]
        )
        next_step = _launch_issue_next_step(check, repo_root=repo_root)
        if next_step:
            lines.append(f"  - Next: {next_step}")
    collection_commands = _launch_issue_collection_commands(
        external_checks,
        repo_root=repo_root,
    )
    if collection_commands:
        lines.extend(["", "## Missing Evidence Collection Commands"])
        for check_name, commands in collection_commands:
            lines.extend(["", f"### {check_name}", "```bash"])
            lines.extend(commands)
            lines.append("```")
    lines.extend(["", "## Local Gate"])
    if local_failures:
        lines.append("Local readiness is not clean. Fix these before using this launch issue:")
        for check in local_failures:
            lines.extend(
                [
                    f"- `{check['name']}`: {check['evidence']}",
                    f"  - Next: {check['next_step']}",
                ]
            )
    else:
        lines.append("Local readiness is clean. Keep this issue focused on external proof.")
    lines.extend(
        [
            "",
            "## Evidence Commands",
            "```bash",
            "uv run python scripts/oss_readiness_check.py "
            "--write-external-evidence-template docs/launch-evidence.json",
            "uv run python scripts/collect_launch_evidence.py "
            "--write-external-evidence-file docs/launch-evidence.json --merge-existing "
            "--format json",
            "uv run python scripts/oss_readiness_check.py "
            "--external-evidence-file docs/launch-evidence.json --format launch-issue",
            "uv run python scripts/oss_readiness_check.py "
            "--strict-public --external-evidence-file docs/launch-evidence.json "
            "--format markdown",
            "```",
            "",
            "## Redaction",
            "- Do not include API keys, bearer tokens, cookies, or raw MCP transcripts.",
            "- Attach sanitized URLs, workflow run links, release pages, or redacted screenshots.",
        ]
    )
    return "\n".join(lines)


def _launch_issue_collection_commands(
    external_checks: list[dict[str, str]],
    *,
    repo_root: Path,
) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    launch_commands = _launch_evidence_commands(repo_root)
    for check in external_checks:
        if check["status"] == "passed":
            continue
        check_commands = launch_commands.get(check["name"])
        if check_commands:
            commands.append((check["name"], check_commands))
    return commands


def _launch_issue_next_step(check: dict[str, str], *, repo_root: Path) -> str:
    if check["status"] == "passed":
        return ""
    if check["name"] in _launch_evidence_commands(repo_root):
        return f"Use the `{check['name']}` command block in Missing Evidence Collection Commands."
    return check["next_step"]


def _exit_code(result: dict[str, Any]) -> int:
    return 0 if result["decision"] in {"local_ready_external_missing", "public_ready"} else 1


if __name__ == "__main__":
    sys.exit(main())
