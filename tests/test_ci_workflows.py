"""Workflow contract checks for offline CI and opt-in live smoke gates."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HOSTED_SMOKE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "hosted-smoke.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _read_text(path: Path) -> str:
    """Read a workflow file as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def test_ci_pytest_gate_excludes_all_live_markers() -> None:
    """Ensure normal CI cannot discover live or Docker-only tests by accident."""
    text = _read_text(CI_WORKFLOW)

    assert 'uv run pytest -q -m "not live and not docker_live"' in text
    assert 'uv run pytest -q -m "not live"\n' not in text


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


def test_release_workflow_uploads_expected_install_artifacts() -> None:
    """Lock the public artifact contract used by curl and PowerShell installers."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "policynim-${RELEASE_TAG}-linux-amd64.tar.gz",
        "policynim-${RELEASE_TAG}-darwin-arm64.tar.gz",
        "policynim-${RELEASE_TAG}-windows-amd64.zip",
        "scripts/install.sh",
        "scripts/install.ps1",
        "SHA256SUMS",
        "gh release create",
        "--draft",
    ):
        assert token in text


def test_release_workflow_rejects_mismatched_manual_versions() -> None:
    """Prevent manual release dispatches from publishing inconsistent artifacts."""
    text = _read_text(RELEASE_WORKFLOW)

    assert 'project_version = project["project"]["version"]' in text
    assert 'requested_version = requested.removeprefix("v")' in text
    assert "requested_version != project_version" in text
    assert "Release version mismatch:" in text
