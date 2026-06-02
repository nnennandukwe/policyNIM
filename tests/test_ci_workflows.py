"""Workflow contract checks for offline CI and opt-in live smoke gates."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HOSTED_SMOKE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "hosted-smoke.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
WORKFLOWS = (CI_WORKFLOW, HOSTED_SMOKE_WORKFLOW, RELEASE_WORKFLOW)


def _read_text(path: Path) -> str:
    """Read a workflow file as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def test_ci_pytest_gate_excludes_all_live_markers() -> None:
    """Ensure normal CI cannot discover live or Docker-only tests by accident."""
    text = _read_text(CI_WORKFLOW)

    assert 'uv run pytest -q -m "not live and not docker_live"' in text
    assert 'uv run pytest -q -m "not live"\n' not in text


def test_workflows_opt_into_node24_javascript_actions() -> None:
    """Exercise the Node 24 runner default without allowing the Node 20 fallback."""
    for workflow in WORKFLOWS:
        text = _read_text(workflow)

        assert 'FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"' in text
        assert "ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION" not in text


def test_workflows_use_approved_node24_migration_action_pins() -> None:
    """Keep migrated GitHub action lines on approved major versions and SHA pins."""
    text = "\n".join(_read_text(workflow) for workflow in WORKFLOWS)

    expected_refs = (
        r"actions/checkout@[0-9a-f]{40} # v5",
        r"actions/setup-python@[0-9a-f]{40} # v6",
        r"astral-sh/setup-uv@[0-9a-f]{40} # v6\.8\.0",
        r"actions/upload-artifact@[0-9a-f]{40} # v5",
        r"actions/download-artifact@[0-9a-f]{40} # v5",
    )
    for ref_pattern in expected_refs:
        assert re.search(ref_pattern, text), ref_pattern

    for legacy_ref in (
        "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
    ):
        assert legacy_ref not in text


def test_hosted_smoke_workflow_is_manual_and_secret_gated() -> None:
    """Ensure the hosted smoke workflow is opt-in and requires deployed beta secrets."""
    text = _read_text(HOSTED_SMOKE_WORKFLOW)

    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "POLICYNIM_BETA_MCP_URL: ${{ secrets.POLICYNIM_BETA_MCP_URL }}" in text
    assert "POLICYNIM_BETA_MCP_TOKEN: ${{ secrets.POLICYNIM_BETA_MCP_TOKEN }}" in text
    assert "Validate hosted smoke secrets" in text
    assert "pytest -q -m live tests/test_hosted_mcp_live.py" in text
    assert "permissions:\n  contents: read" in text


def test_hosted_smoke_workflow_uses_pinned_actions() -> None:
    """Ensure the hosted smoke workflow keeps GitHub actions pinned to SHAs."""
    text = _read_text(HOSTED_SMOKE_WORKFLOW)
    action_refs = re.findall(r"uses: [^@\n]+@([0-9a-f]{40})", text)

    assert len(action_refs) == 3


def test_release_workflow_runs_offline_verification_before_release_artifacts() -> None:
    """Ensure release automation builds from the same offline quality gate as CI."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "workflow_dispatch:",
        "tags:",
        '- "v*.*.*"',
        "uv lock --check",
        "uv run ruff check .",
        "uv run pyright",
        'uv run pytest -q -m "not live and not docker_live"',
        "uv build --out-dir dist",
        "wheel-smoke:",
        "standalone-build:",
        "publish-github-release:",
    ):
        assert token in text
    assert "tests/test_hosted_mcp_live.py" not in text
    assert "POLICYNIM_BETA_MCP_TOKEN" not in text


def test_release_workflow_uses_pinned_actions_and_trusted_pypi_publish() -> None:
    """Ensure release actions are pinned and PyPI publish uses OIDC, not secrets."""
    text = _read_text(RELEASE_WORKFLOW)
    uses_lines = re.findall(r"uses: [^\n]+", text)

    assert uses_lines
    for line in uses_lines:
        if line.startswith("uses: pypa/gh-action-pypi-publish@v1.14.0"):
            assert line.endswith("# 6733eb7d741f0b11ec6a39b58540dab7590f9b7d")
            continue
        assert re.search(r"@[0-9a-f]{40}(?:\s+#.*)?$", line), line
    assert "id-token: write" in text
    assert "pypa/gh-action-pypi-publish@" in text
    assert "password:" not in text
    assert "TWINE_PASSWORD" not in text


def test_release_workflow_publishes_pypi_after_release_assets() -> None:
    """Do not let immutable PyPI files publish before release assets pass."""
    text = _read_text(RELEASE_WORKFLOW)

    publish_pypi_job = re.search(
        r"publish-pypi:\n(?P<body>.*?)(?=\n  [a-zA-Z0-9_-]+:|\Z)",
        text,
        re.S,
    )
    assert publish_pypi_job is not None
    body = publish_pypi_job.group("body")

    assert "needs:\n      - publish-github-release" in body
    assert "pypa/gh-action-pypi-publish@" in body
    assert text.index("publish-github-release:") < text.index("publish-pypi:")


def test_release_workflow_uploads_expected_install_artifacts() -> None:
    """Lock the public artifact contract used by curl and PowerShell installers."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "policynim-${RELEASE_TAG}-linux-amd64.tar.gz",
        "policynim-${RELEASE_TAG}-darwin-arm64.tar.gz",
        "policynim-${RELEASE_TAG}-darwin-amd64.tar.gz",
        "policynim-${RELEASE_TAG}-windows-amd64.zip",
        "scripts/install.sh",
        "scripts/install.ps1",
        "SHA256SUMS",
        "gh release create",
        "--draft",
    ):
        assert token in text


def test_release_workflow_builds_macos_intel_standalone_artifact() -> None:
    """Restore macOS Intel as a first-class standalone release target."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "os: macos-15-intel",
        "platform: darwin-amd64",
        'darwin-amd64) ASSET_NAME="policynim-${RELEASE_TAG}-darwin-amd64.tar.gz" ;;',
    ):
        assert token in text


def test_release_workflow_rejects_mismatched_manual_versions() -> None:
    """Prevent manual release dispatches from publishing inconsistent artifacts."""
    text = _read_text(RELEASE_WORKFLOW)

    assert 'project_version = project["project"]["version"]' in text
    assert 'requested_version = requested.removeprefix("v")' in text
    assert "requested_version != project_version" in text
    assert "Release version mismatch:" in text
