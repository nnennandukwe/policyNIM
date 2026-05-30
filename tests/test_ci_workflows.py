"""Workflow contract checks for offline CI and opt-in live smoke gates."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HOSTED_SMOKE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "hosted-smoke.yml"


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
