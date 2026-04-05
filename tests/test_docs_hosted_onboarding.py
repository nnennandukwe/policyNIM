"""Docs parity checks for hosted Day 4 onboarding."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
CODEX_README = REPO_ROOT / "examples" / "codex" / "README.md"
CLAUDE_README = REPO_ROOT / "examples" / "claude-code" / "README.md"
TESTS_README = REPO_ROOT / "tests" / "README.md"

CODEX_HOSTED_COMMAND = (
    "codex mcp add policynim --url https://<railway-domain>/mcp "
    "--bearer-token-env-var POLICYNIM_TOKEN"
)
CLAUDE_HOSTED_COMMAND = (
    "claude mcp add --transport http policynim https://<railway-domain>/mcp "
    '--header "Authorization: Bearer $POLICYNIM_TOKEN"'
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_uses_hosted_first_commands() -> None:
    text = _read_text(README)

    assert CODEX_HOSTED_COMMAND in text
    assert CLAUDE_HOSTED_COMMAND in text
    assert text.index(CODEX_HOSTED_COMMAND) < text.index("uv sync")


def test_codex_example_is_hosted_first() -> None:
    text = _read_text(CODEX_README)

    assert CODEX_HOSTED_COMMAND in text
    assert text.index("## Hosted Railway MCP") < text.index("## Local Fallback")


def test_claude_example_is_hosted_first() -> None:
    text = _read_text(CLAUDE_README)

    assert CLAUDE_HOSTED_COMMAND in text
    assert text.index("## Hosted Railway MCP") < text.index("## Local Fallback")


def test_readme_covers_required_recovery_topics() -> None:
    text = _read_text(README).lower()

    for topic in (
        "invalid token",
        "temporary upstream nvidia failure",
        "insufficient context",
        "service unavailable",
    ):
        assert topic in text


def test_tests_readme_distinguishes_client_and_smoke_env_vars() -> None:
    text = _read_text(TESTS_README)

    assert "`POLICYNIM_TOKEN`" in text
    assert "`POLICYNIM_BETA_MCP_URL`" in text
    assert "`POLICYNIM_BETA_MCP_TOKEN`" in text
