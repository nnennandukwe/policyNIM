"""Repository metadata checks for OSS maintainer trust."""

from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
SECURITY = REPO_ROOT / "SECURITY.md"
SUPPORT = REPO_ROOT / "SUPPORT.md"
CODE_OF_CONDUCT = REPO_ROOT / "CODE_OF_CONDUCT.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
ROADMAP = REPO_ROOT / "docs" / "roadmap.md"
MAINTAINER_TRIAGE = REPO_ROOT / "docs" / "maintainer-triage.md"
LABELS = REPO_ROOT / ".github" / "labels.yml"
TOPICS = REPO_ROOT / ".github" / "topics.yml"
CODEOWNERS = REPO_ROOT / ".github" / "CODEOWNERS"
DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"
LABEL_SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_github_labels.py"
TOPIC_SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_github_topics.py"
OSS_READINESS_SCRIPT = REPO_ROOT / "scripts" / "oss_readiness_check.py"
PR_TEMPLATE = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
BUG_TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
FEATURE_TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml"
LAUNCH_EVIDENCE_TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "public_launch_evidence.yml"
ISSUE_CONFIG = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml"
README = REPO_ROOT / "README.md"
DOCS_INDEX = REPO_ROOT / "docs" / "index.md"
OSS_READINESS_AUDIT = REPO_ROOT / "docs" / "oss-readiness-audit.md"
PUBLIC_LAUNCH_RUNBOOK = REPO_ROOT / "docs" / "public-launch-runbook.md"
GITIGNORE = REPO_ROOT / ".gitignore"
RELEASE_DOC = REPO_ROOT / "docs" / "release.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _read_text(path: Path) -> str:
    """Read a repository metadata file."""
    return path.read_text(encoding="utf-8")


def _read_yaml_mapping(path: Path) -> dict[str, object]:
    """Parse a YAML metadata file without relying on GitHub rendering."""
    yaml = importlib.import_module("yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return cast(dict[str, object], data)


def _project_keywords() -> list[str]:
    """Return package keywords from pyproject.toml."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    return cast(list[str], project["keywords"])


def _issue_template_labels(text: str) -> set[str]:
    match = re.search(r"^labels:\s*\[(?P<labels>[^\]]+)\]", text, flags=re.MULTILINE)
    assert match is not None
    return {label.strip().strip('"') for label in match.group("labels").split(",")}


def test_root_contribution_and_security_docs_exist_with_policyNIM_specific_gates() -> None:
    """Keep contribution and vulnerability reporting discoverable at repo root."""
    contributing = _read_text(CONTRIBUTING)
    security = _read_text(SECURITY)

    for token in (
        "uv run policynim doctor --format json",
        'uv run pytest -q -m "not live and not docker_live"',
        "uv run python scripts/release_check.py",
        "docs/oss-readiness-audit.md#high-value-pr-sequence",
        "one user-facing thesis",
        "one primary evidence surface",
        "bounded rollback story",
        "MCP",
        "No API keys",
    ):
        assert token in contributing

    for token in (
        "private GitHub security advisory",
        "hosted MCP bearer tokens",
        "policynim doctor --format json",
        "without calling NVIDIA-hosted APIs",
    ):
        assert token in security


def test_community_health_docs_route_support_conduct_and_roadmap() -> None:
    """Keep public support and project-scope expectations discoverable."""
    support = _read_text(SUPPORT)
    conduct = _read_text(CODE_OF_CONDUCT)
    roadmap = _read_text(ROADMAP)
    config = _read_text(ISSUE_CONFIG)
    triage = _read_text(MAINTAINER_TRIAGE)

    for token in (
        "policynim support-bundle",
        "Security issues",
        "MCP stdio",
        "Hosted MCP",
        "policynim mcp-config --target hosted-http",
        "hosted_url_placeholder",
        "--include-local-paths",
        "redacts local path prefixes",
        "`first_run` section",
        "`quickstart_command`",
        "Attach `policynim support-bundle` output to public issues",
        "Keep raw `policynim doctor --format json` output local unless a maintainer asks",
        "Issue Response Expectations",
        "docs/maintainer-triage.md",
        "public_launch_evidence.yml",
        "Public launch evidence",
        "strict public readiness",
    ):
        assert token in support

    for token in (
        "PolicyNIM Community Standards",
        "maintainer review",
        "private policy content",
        "enforcement",
    ):
        assert token in conduct

    for token in (
        "Roadmap",
        "Now",
        "Next",
        "Later",
        "Not committed yet",
        "RELEASE_MANIFEST.json",
        "PyPI",
        "Hosted beta",
        "PyPI package installs",
        "trusted-publishing evidence",
        ".github/labels.yml",
        "scripts/sync_github_labels.py",
        "scripts/oss_readiness_check.py",
        "policynim quickstart",
    ):
        assert token in roadmap

    for token in (
        "PolicyNIM Maintainer Triage",
        "../.github/labels.yml",
        "type/*",
        "surface/*",
        "priority/*",
        "status/needs-repro",
        "status/blocked-external",
        "policynim support-bundle --include-mcp-smoke",
        "policynim support-bundle --include-local-paths",
        "`first_run` target summary",
        "policynim mcp-config --client codex",
        "policynim mcp-config --target hosted-http",
        "hosted_url_placeholder",
        "scripts/sync_github_labels.py --format json",
        "scripts/sync_github_labels.py --live --format json",
        "scripts/sync_github_labels.py --apply --format json",
        "gh auth status",
        "fails without a",
        "rerun `--live`",
        "use `--apply` only when ready",
    ):
        assert token in triage

    assert "Support questions" in config
    assert "CODE_OF_CONDUCT.md" in config


def test_support_surfaces_quote_hosted_mcp_placeholder_commands() -> None:
    """Keep hosted MCP support commands pasteable in zsh and bash."""
    for path in (SUPPORT, MAINTAINER_TRIAGE, BUG_TEMPLATE):
        text = _read_text(path)
        assert "--hosted-url 'https://<host>/mcp'" in text, (
            f"{path.relative_to(REPO_ROOT)} must quote placeholder hosted MCP URLs "
            "in copyable support commands"
        )
        assert "--hosted-url https://<host>/mcp" not in text


def test_readme_badges_advertise_only_verified_public_channels() -> None:
    """Keep public badges useful without claiming unverified hosted launch proof."""
    readme = _read_text(README)
    audit = _read_text(OSS_READINESS_AUDIT)
    roadmap = _read_text(ROADMAP)

    expected_badges = (
        "actions/workflows/ci.yml/badge.svg?branch=main",
        "img.shields.io/pypi/v/policynim",
        "img.shields.io/pypi/pyversions/policynim",
        "img.shields.io/github/v/release/nnennandukwe/policyNIM",
        "img.shields.io/github/license/nnennandukwe/policyNIM",
        "Built%20with-NVIDIA%20NIM",
    )
    for badge in expected_badges:
        assert badge in readme

    for premature_badge in (
        "img.shields.io/badge/hosted",
        "img.shields.io/badge/public%20ready",
        "img.shields.io/badge/uptime",
        "uptimerobot",
    ):
        assert premature_badge not in readme.lower()

    assert "bounded public README badges" in audit
    assert "hosted health and public-ready badges" in " ".join(roadmap.split())


def test_label_taxonomy_covers_public_issue_templates_and_triage_docs() -> None:
    """Keep public labels stable enough for maintainers to route issues."""
    labels_text = _read_text(LABELS)
    sync_script = _read_text(LABEL_SYNC_SCRIPT)
    readiness_script = _read_text(OSS_READINESS_SCRIPT)
    bug = _read_text(BUG_TEMPLATE)
    feature = _read_text(FEATURE_TEMPLATE)
    launch = _read_text(LAUNCH_EVIDENCE_TEMPLATE)
    triage = _read_text(MAINTAINER_TRIAGE)
    readme = _read_text(README)
    docs_index = _read_text(DOCS_INDEX)

    label_names = set(re.findall(r"^- name: (.+)$", labels_text, flags=re.MULTILINE))
    required_labels = {
        "type/bug",
        "type/feature",
        "type/docs",
        "type/launch",
        "status/needs-triage",
        "status/needs-repro",
        "status/blocked-external",
        "priority/p0",
        "priority/p1",
        "priority/p2",
        "surface/cli",
        "surface/mcp-stdio",
        "surface/hosted-mcp",
        "surface/install-release",
        "surface/runtime-evidence",
        "surface/ci",
        "surface/docs",
        "needs/codeowner-review",
        "needs/support-bundle",
        "needs/live-check",
        "needs/launch-evidence",
        "good-first-issue",
    }
    assert required_labels <= label_names
    assert _issue_template_labels(bug) <= label_names
    assert _issue_template_labels(feature) <= label_names
    assert _issue_template_labels(launch) <= label_names
    assert 'DEFAULT_LABELS_FILE = REPO_ROOT / ".github" / "labels.yml"' in sync_script
    assert "live/apply label sync" in sync_script
    assert "`--live` to inspect" in sync_script
    assert "`--apply` only when ready" in sync_script
    assert "missing_external" in readiness_script
    assert "hold_external_missing" in readiness_script

    for token in (
        "Label Source Of Truth",
        "Evidence First",
        "Priority Rules",
        "Surface Routing",
        "needs/launch-evidence",
        "strict public launch evidence",
        "public_launch_evidence.yml",
        "Public launch evidence",
        "type/launch",
        "generated launch issue",
        "Response Expectations",
    ):
        assert token in triage

    for text in (readme, docs_index):
        assert "maintainer-triage.md" in text


def test_topic_taxonomy_matches_public_discoverability_positioning() -> None:
    """Keep repository topics aligned with package keywords and public positioning."""
    topics = [
        line.removeprefix("- ").strip()
        for line in _read_text(TOPICS).splitlines()
        if line.startswith("- ")
    ]
    topic_sync_script = _read_text(TOPIC_SYNC_SCRIPT)
    readiness_script = _read_text(OSS_READINESS_SCRIPT)
    triage = _read_text(MAINTAINER_TRIAGE)
    audit = _read_text(OSS_READINESS_AUDIT)
    roadmap = _read_text(ROADMAP)
    readme = _read_text(README)
    docs_index = _read_text(DOCS_INDEX)
    pyproject = _project_keywords()

    required_topics = {
        "ai-agents",
        "cli",
        "code-quality",
        "mcp",
        "model-context-protocol",
        "nvidia-nim",
        "policy",
        "preflight",
        "python",
        "verification",
    }
    assert required_topics <= set(topics)
    assert 'DEFAULT_TOPICS_FILE = REPO_ROOT / ".github" / "topics.yml"' in (topic_sync_script)
    assert "gh repo view --json repositoryTopics" in topic_sync_script
    assert "live/apply topic sync" in topic_sync_script
    assert "`--live` to inspect" in topic_sync_script
    assert "`--apply` only when ready" in topic_sync_script
    assert "github_topic_metadata" in readiness_script
    assert "topic_sync_script" in readiness_script

    for keyword in (
        "ai-agents",
        "cli",
        "code-quality",
        "mcp",
        "nvidia-nim",
        "policy",
        "preflight",
        "verification",
    ):
        assert keyword in pyproject
        assert keyword in topics

    for token in (
        "Topic Source Of Truth",
        "../.github/topics.yml",
        "scripts/sync_github_topics.py --format json",
        "scripts/sync_github_topics.py --live --format json",
        "scripts/sync_github_topics.py --apply --format json",
        "public-ready topics",
    ):
        assert token in triage

    for text in (audit, roadmap):
        assert ".github/topics.yml" in text
        assert "scripts/sync_github_topics.py" in text

    assert "repo topic taxonomy" in " ".join(readme.split())
    assert "repository topics" in docs_index


def test_supply_chain_ownership_metadata_is_reviewable_and_bounded() -> None:
    """Keep dependency updates and risky ownership paths visible before public launch."""
    codeowners = _read_text(CODEOWNERS)
    dependabot = _read_text(DEPENDABOT)
    labels = _read_text(LABELS)
    triage = _read_text(MAINTAINER_TRIAGE)
    contributing = _read_text(CONTRIBUTING)
    security = _read_text(SECURITY)
    roadmap = _read_text(ROADMAP)
    readiness_script = _read_text(OSS_READINESS_SCRIPT)

    for token in (
        "* @nnennandukwe",
        "/.github/workflows/ @nnennandukwe",
        "/.github/dependabot.yml @nnennandukwe",
        "/pyproject.toml @nnennandukwe",
        "/uv.lock @nnennandukwe",
        "/scripts/release_check.py @nnennandukwe",
        "/scripts/install.sh @nnennandukwe",
        "/scripts/install.ps1 @nnennandukwe",
        "/src/policynim/interfaces/cli.py @nnennandukwe",
        "/src/policynim/interfaces/mcp.py @nnennandukwe",
        "/SECURITY.md @nnennandukwe",
    ):
        assert token in codeowners

    for token in (
        "version: 2",
        'package-ecosystem: "uv"',
        'package-ecosystem: "github-actions"',
        'directory: "/"',
        'interval: "weekly"',
        'timezone: "America/New_York"',
        "open-pull-requests-limit: 3",
        "surface/install-release",
        "surface/ci",
        "needs/codeowner-review",
        "status/needs-triage",
    ):
        assert token in dependabot

    assert "needs/codeowner-review" in labels
    for text in (triage, contributing, security, roadmap):
        assert ".github/CODEOWNERS" in text
        assert ".github/dependabot.yml" in text
    assert "pull_request_template_metadata" in readiness_script
    assert "codeowners_metadata" in readiness_script
    assert "dependabot_metadata" in readiness_script


def test_release_docs_explain_artifact_attestation_verification() -> None:
    """Keep release provenance visible to users and maintainers."""
    release_doc = _read_text(RELEASE_DOC)
    audit = _read_text(OSS_READINESS_AUDIT)
    runbook = _read_text(PUBLIC_LAUNCH_RUNBOOK)

    for token in (
        "actions/attest",
        "subject-checksums: release-assets/SHA256SUMS",
        "gh attestation verify",
        "artifact attestations",
        "selected release asset",
        "matching SHA-256 digests",
        "SHA256SUMS",
        "RELEASE_MANIFEST.json",
    ):
        assert token in release_doc

    for text in (audit, runbook):
        assert "artifact attestations" in text
        assert "gh attestation verify" in text
        assert "selected release asset" in text
        assert "RELEASE_MANIFEST.json" in text
        assert "SHA256SUMS" in text


def test_pull_request_template_requires_verification_and_secret_safety() -> None:
    """Make PR evidence expectations explicit for maintainers and contributors."""
    text = _read_text(PR_TEMPLATE)

    for token in (
        "uv run ruff check .",
        "uv run pyright",
        'uv run pytest -q -m "not live and not docker_live"',
        "uv build --out-dir dist",
        "uv run policynim doctor --format json",
        "uv run policynim support-bundle",
        "uv run python scripts/release_check.py",
        "uv run python scripts/oss_readiness_check.py --format launch-issue",
        "uv run python scripts/release_check.py --strict-public "
        "--external-evidence-file docs/launch-evidence.json",
        "docs/oss-readiness-audit.md#high-value-pr-sequence",
        "one user-facing thesis",
        "one primary evidence surface",
        "bounded rollback story",
        "First-run and hosted MCP onboarding",
        "Local CLI and MCP verification loop",
        "Installability and release trust",
        "SQLite migration and storage contract",
        "Maintainer trust and public launch proof",
        "public_ready",
        "hold_external_missing",
        "package-smoke-evidence",
        "policynim-mcp-smoke.json",
        "No API keys",
        "Docs match the current command output",
    ):
        assert token in text


def test_issue_templates_collect_reproduction_doctor_output_and_verification() -> None:
    """Route bug and feature reports toward actionable maintainer evidence."""
    bug = _read_text(BUG_TEMPLATE)
    feature = _read_text(FEATURE_TEMPLATE)
    launch = _read_text(LAUNCH_EVIDENCE_TEMPLATE)
    config = _read_text(ISSUE_CONFIG)

    for token in (
        "Affected surface",
        "policynim support-bundle",
        "policynim doctor",
        "MCP stdio",
        "Hosted MCP streamable-http",
        "MCP config evidence",
        "policynim mcp-config --target hosted-http",
        "hosted_url_placeholder",
        "Did this call live or hosted services?",
    ):
        assert token in bug

    for token in (
        "Primary surface",
        "How should this be verified?",
        "doctor",
        "MCP smoke",
        "CI or release",
    ):
        assert token in feature

    for token in (
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
    ):
        assert token in launch

    assert "blank_issues_enabled: false" in config
    assert "security/advisories/new" in config
    assert 'labels: ["type/bug", "status/needs-triage", "needs/support-bundle"]' in bug
    assert 'labels: ["type/feature", "status/needs-triage"]' in feature
    assert 'labels: ["type/launch", "status/needs-triage", "needs/launch-evidence"]' in launch


def test_github_issue_forms_parse_and_keep_evidence_fields_stable() -> None:
    """Catch broken GitHub issue-form YAML before public reporters hit it."""
    bug = _read_yaml_mapping(BUG_TEMPLATE)
    feature = _read_yaml_mapping(FEATURE_TEMPLATE)
    launch = _read_yaml_mapping(LAUNCH_EVIDENCE_TEMPLATE)
    config = _read_yaml_mapping(ISSUE_CONFIG)

    assert bug["labels"] == ["type/bug", "status/needs-triage", "needs/support-bundle"]
    assert feature["labels"] == ["type/feature", "status/needs-triage"]
    assert launch["labels"] == ["type/launch", "status/needs-triage", "needs/launch-evidence"]
    assert config["blank_issues_enabled"] is False

    bug_fields = _issue_form_fields(bug)
    assert {
        "surface",
        "support_bundle",
        "mcp_config",
        "reproduce",
        "expected",
        "actual",
        "live",
    } <= set(bug_fields)
    for field_id in ("surface", "support_bundle", "reproduce", "expected", "actual", "live"):
        assert _issue_form_required(bug_fields[field_id]) is True
    assert _issue_form_required(bug_fields["mcp_config"]) is False

    feature_fields = _issue_form_fields(feature)
    assert {"surface", "problem", "proposal", "verification"} <= set(feature_fields)
    assert all(_issue_form_required(field) for field in feature_fields.values())

    launch_fields = _issue_form_fields(launch)
    assert {
        "release_tag",
        "strict_public",
        "launch_issue",
        "evidence_records",
        "remaining_proof",
    } <= set(launch_fields)
    assert all(_issue_form_required(field) for field in launch_fields.values())


def _issue_form_fields(template: dict[str, object]) -> dict[str, dict[str, object]]:
    body = template["body"]
    assert isinstance(body, list)
    fields: dict[str, dict[str, object]] = {}
    for item in body:
        assert isinstance(item, dict)
        field_id = item.get("id")
        if isinstance(field_id, str):
            fields[field_id] = cast(dict[str, object], item)
    return fields


def _issue_form_required(field: dict[str, object]) -> bool:
    validations = field.get("validations", {})
    assert isinstance(validations, dict)
    return validations.get("required") is True


def test_readme_and_docs_index_link_trust_metadata() -> None:
    """Expose contribution and security routes from the docs map."""
    readme = _read_text(README)
    docs_index = _read_text(DOCS_INDEX)

    for text in (readme, docs_index):
        assert "CONTRIBUTING.md" in text
        assert "SECURITY.md" in text
        assert "SUPPORT.md" in text
        assert "CODE_OF_CONDUCT.md" in text
        assert "CHANGELOG.md" in text
        assert "roadmap.md" in text
        assert "maintainer-triage.md" in text

    assert "public_launch_evidence.yml" in docs_index
    assert "Public launch evidence" in docs_index


def test_changelog_tracks_public_release_history_and_gate() -> None:
    """Keep release notes visible and tied to the local release gate."""
    changelog = _read_text(CHANGELOG)
    release = _read_text(REPO_ROOT / "docs" / "release.md")
    readiness_script = _read_text(OSS_READINESS_SCRIPT)

    for token in (
        "# Changelog",
        "## [Unreleased]",
        "## [0.1.0]",
        "OSS readiness",
        "MCP",
        "CLI",
        "release gate",
        "GitHub release notes",
    ):
        assert token in changelog

    assert "scripts/check_release_notes.py --format json" in release
    assert "scripts/check_release_notes.py --write-github-release-notes release-notes.md" in release
    assert "release_notes_check" in readiness_script
    assert "--write-github-release-notes" in readiness_script


def test_public_launch_runbook_turns_external_proof_into_an_ordered_workflow() -> None:
    """Keep the public launch path explicit enough for a maintainer to execute."""
    runbook = _read_text(PUBLIC_LAUNCH_RUNBOOK)
    readme = _read_text(README)
    docs_index = _read_text(DOCS_INDEX)
    release = _read_text(REPO_ROOT / "docs" / "release.md")
    roadmap = _read_text(ROADMAP)
    audit = _read_text(OSS_READINESS_AUDIT)

    for text in (readme, docs_index, release, roadmap, audit):
        assert "public-launch-runbook.md" in text

    for token in (
        "Public Launch Runbook",
        "uv run python scripts/release_check.py --dry-run --format json",
        "uv run python scripts/release_check.py",
        "uv run python scripts/release_check.py \\\n  --strict-public",
        "--external-evidence-file docs/launch-evidence.json \\\n  --format json",
        "uv run python scripts/oss_readiness_check.py --format markdown",
        "uv run python scripts/oss_readiness_check.py --format launch-issue",
        "uv run python scripts/oss_readiness_check.py --external-evidence-file "
        "docs/launch-evidence.json --format launch-issue",
        "Missing Evidence Collection Commands",
        "`--format json` so writing `docs/launch-evidence.json` still",
        "Checklist `Next` lines point to those command blocks",
        "uv run python scripts/oss_readiness_check.py "
        "--write-external-evidence-template docs/launch-evidence.json",
        "uv run python scripts/oss_readiness_check.py --strict-public "
        "--external-evidence-file docs/launch-evidence.json --format markdown",
        "uv run python scripts/oss_readiness_check.py --strict-public "
        "--external-evidence-file docs/launch-evidence.json --format json",
        "--pypi-publish-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>'",
        "--github-install-smoke",
        "`github_release_install_smoke`",
        "--pypi-install-smoke",
        "`policynim==<version>` from PyPI",
        "PyPI project version URL",
        "current-version commands directly from `pyproject.toml`",
        "--release-tag v<version>",
        "--release-attestation-asset-name install.sh",
        "--hosted-mcp-url 'https://<railway-domain>/mcp'",
        "--hosted-smoke-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>'",
        "--write-mcp-client-evidence-template launch-notes/codex-mcp-session.json",
        "--mcp-client-template-client codex",
        "--mcp-client-template-transport hosted-http",
        "--mcp-client-setup-command",
        "--mcp-client-hosted-url 'https://<railway-domain>/mcp'",
        "codex mcp add policynim --url 'https://<railway-domain>/mcp'",
        "--mcp-client-evidence-file launch-notes/codex-mcp-session.json",
        "--merge-existing",
        "--merge-existing \\\n  --format json",
        "current live label or topic drift probe clears",
        "GitHub release installer smoke or PyPI install smoke failure clears older",
        "GitHub release artifact probe failures",
        "stale release evidence",
        "PyPI install smoke failure clears older",
        "clears older GitHub metadata proof",
        "--require-requested-probes",
        "Text output mirrors the actionable probe details from JSON",
        "failed attestation or hosted-service `detail` messages",
        "docs/mcp-client-evidence.example.json",
        "checked-in example intentionally",
        "leaves `setup_command` and `reference` blank",
        "client-session references and setup commands are rejected",
        "GitHub release artifacts",
        "mentions every current expected release asset",
        "GitHub release installer smoke",
        "GitHub artifact attestation",
        "github_release_install_smoke",
        "github_artifact_attestations",
        "PyPI",
        "`headSha`",
        "`targetCommitish`",
        "same commit as the GitHub release target",
        "hosted MCP domain",
        "strict_public_hosted_onboarding_docs",
        "README.md",
        "examples/codex/README.md",
        "examples/claude-code/README.md",
        "Hosted Beta Smoke",
        "GitHub labels",
        "gh auth status",
        "scripts/sync_github_labels.py --live --format json",
        "scripts/sync_github_topics.py --live --format json",
        "scripts/sync_github_labels.py --apply --format json",
        "scripts/sync_github_topics.py --apply --format json",
        "real MCP client session",
        "`launch-notes/` directory is ignored by Git",
        "public_launch_evidence.yml",
        "Public Launch Evidence",
        "type/launch",
        "needs/launch-evidence",
        "hosted-smoke-evidence",
        "policynim-hosted-smoke-junit.xml",
        "summary",
        "reference",
        "Placeholder or example references are rejected",
        "Reference shapes are also checked",
        "GitHub release tag URL",
        "GitHub Actions run URL",
        "the exact `gh label list --json name,color,description --limit 1000` command",
        "verified_by",
        "verified_at",
        "last 14 days",
        "more than 10 minutes in the future",
        "stale launch proof",
        "No API keys",
        "do not include tokens",
        "hold_external_missing",
        "public_ready",
    ):
        assert token in runbook

    gitignore = _read_text(GITIGNORE)
    assert "docs/launch-evidence.json" in gitignore
    assert "launch-notes/" in gitignore
    assert "docs/launch-evidence.example.json" not in gitignore
    assert "docs/mcp-client-evidence.example.json" not in gitignore


def test_public_launch_runbook_uses_version_placeholders_for_manual_commands() -> None:
    """Keep reusable launch instructions from going stale after version bumps."""
    runbook = _read_text(PUBLIC_LAUNCH_RUNBOOK)

    assert "v0.1.0" not in runbook
    assert "policynim-0.1.0" not in runbook
    assert "--release-tag v<version>" in runbook
    assert "--release-attestation-asset-name install.sh" in runbook
    assert "policynim-v<version>-linux-amd64.tar.gz" in runbook
    assert "policynim-<version>-py3-none-any.whl" in runbook


def test_oss_readiness_audit_tracks_journey_priorities_and_evidence() -> None:
    """Keep the OSS-readiness plan grounded in the current developer journey."""
    audit = _read_text(OSS_READINESS_AUDIT)
    readme = _read_text(README)
    docs_index = _read_text(DOCS_INDEX)

    for text in (readme, docs_index):
        assert "oss-readiness-audit.md" in text

    for token in (
        "Hosted MCP first run",
        "Installed CLI first run",
        "Source checkout contributor path",
        "MCP local fallback",
        "Release and CI trust path",
        "Maintainer trust path",
        "P0",
        "P1",
        "P2",
        'uv run pytest -q -m "not live and not docker_live"',
        "policynim quickstart",
        "policynim doctor --format json",
        "policynim mcp-smoke",
        "policynim mcp-config",
        "policynim mcp-config --target hosted-http",
        "policynim mcp-config --target local-stdio --client codex",
        "installed no-clone launches",
        "policynim mcp --transport stdio",
        "uv run --directory /ABS/PATH/TO/policyNIM policynim mcp --transport stdio",
        "policynim support-bundle",
        "first-run target summary",
        "quickstart_command",
        "agent_workflows",
        "RELEASE_MANIFEST.json",
        "scripts/release_check.py",
        "--strict-public",
        "scripts/oss_readiness_check.py",
        "scripts/collect_launch_evidence.py",
        "--pypi-publish-run-url",
        "--github-install-smoke",
        "github_release_install_smoke",
        "--pypi-install-smoke",
        "clean virtualenv",
        "install smoke evidence",
        "run `headSha`",
        "GitHub release `targetCommitish`",
        "--hosted-mcp-url",
        "--hosted-smoke-run-url",
        "Hosted-smoke evidence is accepted only when the run `headSha`",
        "--mcp-client-evidence-file",
        "--write-mcp-client-evidence-template",
        "--mcp-client-setup-command",
        "--mcp-client-hosted-url",
        "--require-requested-probes",
        "--merge-existing",
        "current live label or topic drift clears older",
        "requested GitHub release installer smoke or PyPI",
        "GitHub metadata proof and requested GitHub release installer smoke",
        "docs/mcp-client-evidence.example.json",
        "checked-in example leaves `setup_command` and `reference` blank",
        "`launch-notes/` workspace is",
        "client-session references and setup commands are rejected",
        "placeholder references are rejected",
        "scripts/sync_github_labels.py",
        "`--live` for a non-mutating authenticated GitHub diff",
        "GitHub label taxonomy dry-run",
        "GitHub label taxonomy apply evidence",
        "github_labels_applied",
        ".github/labels.yml",
        "needs/launch-evidence",
        ".github/CODEOWNERS",
        ".github/dependabot.yml",
        "docs/maintainer-triage.md",
        "hosted-smoke-evidence",
        "policynim-hosted-smoke-junit.xml",
        "--external-evidence-file",
        "--write-external-evidence-template",
        "--format markdown",
        "--format launch-issue",
        "docs/launch-evidence.example.json",
        "summary",
        "reference",
        "verified_by",
        "verified_at",
        "last 14 days",
        "stale evidence",
        "Reference shapes are validated",
        "GitHub release tag URL",
        "GitHub Actions run URL",
        "exact `gh label list --json name,color,description --limit 1000` command",
        "hosted HTTP MCP config JSON",
        "standalone local stdio MCP config evidence",
        "support-bundle first-run contract",
        "validates that launch-issue external proof",
        "strict public",
        "strict_public_hosted_onboarding_docs",
        "same verified hosted `/mcp` origin",
        "`release_check.py --strict-public --external-evidence-file",
        "before any PR claims `public_ready`",
        "local_ready_external_missing",
        "hold_external_missing",
        "ship or hold",
        "CI package job",
    ):
        assert token in audit
    assert "label taxonomy still needs maintainer-run GitHub apply evidence" not in audit


def test_oss_readiness_audit_does_not_claim_unverified_github_metadata() -> None:
    """Keep GitHub labels/topics framed as external proof until applied live."""
    audit = _read_text(OSS_READINESS_AUDIT)

    assert "the current launch evidence shows labels matching" not in audit
    assert "the current launch evidence shows topics matching" not in audit
    for token in (
        "GitHub label taxonomy apply evidence remains external launch proof",
        "GitHub topic taxonomy apply evidence remains external launch proof",
        "`github_labels_applied` and `github_topics_applied` as `missing_external`",
        "`scripts/sync_github_labels.py --apply --format json`",
        "`scripts/sync_github_topics.py --apply --format json`",
    ):
        assert token in audit


def test_public_contributor_setup_uses_offline_pytest_gate() -> None:
    """Avoid sending new contributors through live or Docker test paths by default."""
    readme = _read_text(README)
    contributor = _read_text(REPO_ROOT / "docs" / "contributor-guide.md")

    for text in (readme, contributor):
        assert 'uv run pytest -q -m "not live and not docker_live"' in text

    assert "uv run pytest -q\n" not in readme
    assert "uv run pytest -q\n" not in contributor
