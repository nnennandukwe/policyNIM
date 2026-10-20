"""Workflow contract checks for offline CI and opt-in live smoke gates."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HOSTED_SMOKE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "hosted-smoke.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
WORKFLOW_FILES = (CI_WORKFLOW, HOSTED_SMOKE_WORKFLOW, RELEASE_WORKFLOW)
USES_LINE = re.compile(
    r"uses: (?P<action>[^@\s]+)@(?P<ref>[0-9a-f]{40}|v[0-9][^\s#]*)"
    r"(?:\s+#\s*(?P<version>v[0-9][^\s#]*))?"
)
NODE24_ACTION_MAJOR_COMMENTS = {
    "actions/checkout": {"v5", "v6", "v7"},
    "actions/setup-python": {"v6"},
    "actions/upload-artifact": {"v6", "v7"},
    "actions/download-artifact": {"v7", "v8"},
    "astral-sh/setup-uv": {"v8"},
}


def _read_text(path: Path) -> str:
    """Read a workflow file as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def _workflow_uses(path: Path) -> list[tuple[str, str, str | None]]:
    """Return action, ref, and version comment from workflow uses lines."""
    return [
        (
            match.group("action"),
            match.group("ref"),
            match.group("version"),
        )
        for match in USES_LINE.finditer(_read_text(path))
    ]


def test_workflows_pin_node24_compatible_actions() -> None:
    """Keep JavaScript actions on Node 24-capable pinned generations."""
    for path in WORKFLOW_FILES:
        for action, ref, version in _workflow_uses(path):
            if action == "pypa/gh-action-pypi-publish":
                continue

            assert re.fullmatch(r"[0-9a-f]{40}", ref), (path, action, ref)
            assert version is not None, (path, action)
            assert version.split(".")[0] in NODE24_ACTION_MAJOR_COMMENTS[action], (
                path,
                action,
                version,
            )
        assert "ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION" not in _read_text(path)


def test_ci_pytest_gate_excludes_all_live_markers() -> None:
    """Ensure normal CI cannot discover live or Docker-only tests by accident."""
    text = _read_text(CI_WORKFLOW)

    pytest_commands = re.findall(r"run: (.*pytest.*)", text)
    assert pytest_commands
    for command in pytest_commands:
        assert 'pytest -q -m "not live and not docker_live"' in command
        assert "--no-sync" in command


def test_ci_checks_supported_python_versions_from_locked_dependencies() -> None:
    """Verify the SDK migration on both supported interpreters without resolving new versions."""
    text = _read_text(CI_WORKFLOW)
    assert 'python-version: ["3.11", "3.12"]' in text
    assert "--python ${{ matrix.python-version }}" in text
    sync_commands = re.findall(r"run: (.*uv sync.*)", text)
    assert sync_commands
    assert all("--locked" in command for command in sync_commands)


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
        "uv run --no-sync ruff check .",
        "uv run --no-sync pyright",
        'uv run --no-sync pytest -q -m "not live and not docker_live"',
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


def test_release_uses_locked_dependencies_without_implicit_resync() -> None:
    """Release verification and standalone builds must use the reviewed lockfile."""
    workflow = yaml.safe_load(_read_text(RELEASE_WORKFLOW))
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            for line in step.get("run", "").splitlines():
                if "uv sync" in line:
                    assert "--locked" in line
                if "uv run" in line:
                    assert "--no-sync" in line
