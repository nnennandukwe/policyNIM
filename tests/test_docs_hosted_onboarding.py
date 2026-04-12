"""Docs parity checks for hosted onboarding."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
DOCS_INDEX = REPO_ROOT / "docs" / "index.md"
CONTRIBUTOR_GUIDE = REPO_ROOT / "docs" / "contributor-guide.md"
WORKFLOWS_GUIDE = REPO_ROOT / "docs" / "workflows.md"
HOSTED_OPERATIONS = REPO_ROOT / "docs" / "hosted-beta-operations.md"
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


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _assert_contains_command(text: str, command: str) -> None:
    assert _normalize_whitespace(command) in _normalize_whitespace(text)


def test_readme_uses_hosted_first_commands() -> None:
    text = _read_text(README)
    local_setup_heading = "## Local Contributor Setup"

    assert local_setup_heading in text
    hosted_section = text.split(local_setup_heading, maxsplit=1)[0]

    _assert_contains_command(hosted_section, CODEX_HOSTED_COMMAND)
    _assert_contains_command(hosted_section, CLAUDE_HOSTED_COMMAND)


def test_readme_links_to_split_docs_structure() -> None:
    text = _read_text(README)

    for relative_path in (
        "docs/index.md",
        "docs/contributor-guide.md",
        "docs/workflows.md",
        "docs/hosted-beta-operations.md",
    ):
        assert relative_path in text


def test_docs_index_points_to_the_split_guides() -> None:
    text = _read_text(DOCS_INDEX)

    for relative_path in (
        "contributor-guide.md",
        "workflows.md",
        "hosted-beta-operations.md",
    ):
        assert relative_path in text


def test_codex_example_is_hosted_first() -> None:
    text = _read_text(CODEX_README)
    local_fallback_heading = "## Local Fallback"

    assert "## Hosted Railway MCP" in text
    assert local_fallback_heading in text
    assert text.index("## Hosted Railway MCP") < text.index(local_fallback_heading)
    _assert_contains_command(
        text.split(local_fallback_heading, maxsplit=1)[0],
        CODEX_HOSTED_COMMAND,
    )


def test_claude_example_is_hosted_first() -> None:
    text = _read_text(CLAUDE_README)
    local_fallback_heading = "## Local Fallback"

    assert "## Hosted Railway MCP" in text
    assert local_fallback_heading in text
    assert text.index("## Hosted Railway MCP") < text.index(local_fallback_heading)
    _assert_contains_command(
        text.split(local_fallback_heading, maxsplit=1)[0],
        CLAUDE_HOSTED_COMMAND,
    )


def test_hosted_operations_doc_covers_required_recovery_topics() -> None:
    text = _read_text(HOSTED_OPERATIONS).lower()

    for topic in (
        "invalid token",
        "temporary upstream nvidia failure",
        "insufficient context",
        "service unavailable",
    ):
        assert topic in text


def test_readme_links_to_contributor_and_workflow_guides() -> None:
    text = _read_text(README)

    assert CONTRIBUTOR_GUIDE.name in text
    assert WORKFLOWS_GUIDE.name in text


def test_tests_readme_distinguishes_client_and_smoke_env_vars() -> None:
    text = _read_text(TESTS_README)

    assert "`POLICYNIM_TOKEN`" in text
    assert "`POLICYNIM_BETA_MCP_URL`" in text
    assert "`POLICYNIM_BETA_MCP_TOKEN`" in text
