"""Docs parity checks for hosted onboarding."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
DOCS_INDEX = REPO_ROOT / "docs" / "index.md"
CONTRIBUTOR_GUIDE = REPO_ROOT / "docs" / "contributor-guide.md"
WORKFLOWS_GUIDE = REPO_ROOT / "docs" / "workflows.md"
HOSTED_OPERATIONS = REPO_ROOT / "docs" / "hosted-beta-operations.md"
AGENT_WORKFLOWS = REPO_ROOT / "docs" / "agent-workflows.md"
CODEX_README = REPO_ROOT / "examples" / "codex" / "README.md"
CLAUDE_README = REPO_ROOT / "examples" / "claude-code" / "README.md"
TESTS_README = REPO_ROOT / "tests" / "README.md"

CODEX_HOSTED_COMMAND = (
    "codex mcp add policynim --url 'https://<railway-domain>/mcp' "
    "--bearer-token-env-var POLICYNIM_TOKEN"
)
CLAUDE_HOSTED_COMMAND = (
    "claude mcp add --transport http policynim 'https://<railway-domain>/mcp' "
    '--header "Authorization: Bearer $POLICYNIM_TOKEN"'
)
CODEX_HOSTED_CONFIG_COMMAND = (
    "policynim mcp-config --target hosted-http --client codex "
    "--hosted-url 'https://<railway-domain>/mcp' "
    "--bearer-token-env-var POLICYNIM_TOKEN"
)
CLAUDE_HOSTED_CONFIG_COMMAND = (
    "policynim mcp-config --target hosted-http --client claude-code "
    "--hosted-url 'https://<railway-domain>/mcp' "
    "--bearer-token-env-var POLICYNIM_TOKEN"
)
CODEX_HOSTED_QUICKSTART_COMMAND = (
    "policynim quickstart --target hosted-mcp --client codex "
    "--hosted-url 'https://<railway-domain>/mcp' --format json"
)
CLAUDE_HOSTED_QUICKSTART_COMMAND = (
    "policynim quickstart --target hosted-mcp --client claude-code "
    "--hosted-url 'https://<railway-domain>/mcp' --format json"
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _assert_contains_command(text: str, command: str) -> None:
    assert _normalize_whitespace(command) in _normalize_whitespace(text)


def _bash_fences(text: str) -> list[str]:
    fences: list[str] = []
    chunks = text.split("```bash")
    for chunk in chunks[1:]:
        fence, _, _rest = chunk.partition("```")
        fences.append(fence)
    return fences


def test_hosted_shell_snippets_quote_angle_bracket_placeholders() -> None:
    """Keep copy-pasteable shell snippets from treating placeholders as redirection."""
    docs = (
        README,
        REPO_ROOT / "SUPPORT.md",
        WORKFLOWS_GUIDE,
        HOSTED_OPERATIONS,
        REPO_ROOT / "docs" / "maintainer-triage.md",
        CODEX_README,
        CLAUDE_README,
        REPO_ROOT / "docs" / "demo-script.md",
        REPO_ROOT / "docs" / "public-launch-runbook.md",
        REPO_ROOT / "docs" / "release.md",
    )
    placeholders = (
        "https://<host>/mcp",
        "https://<railway-domain>/mcp",
        "https://<railway-domain>/healthz",
        "https://<generated-domain>/mcp",
        "https://github.com/<owner>/<repo>/actions/runs/<run-id>",
    )

    for path in docs:
        for fence in _bash_fences(_read_text(path)):
            for placeholder in placeholders:
                if placeholder in fence:
                    assert f"'{placeholder}'" in fence or f'"{placeholder}"' in fence, (
                        f"{path.relative_to(REPO_ROOT)} has an unquoted hosted placeholder "
                        f"in a bash snippet: {placeholder}"
                    )


def test_hosted_docs_do_not_leave_known_placeholder_commands_unquoted() -> None:
    """Catch inline hosted placeholders that users may copy from support docs."""
    docs = (
        README,
        REPO_ROOT / "SUPPORT.md",
        WORKFLOWS_GUIDE,
        HOSTED_OPERATIONS,
        REPO_ROOT / "docs" / "maintainer-triage.md",
        CODEX_README,
        CLAUDE_README,
        REPO_ROOT / "docs" / "demo-script.md",
        REPO_ROOT / "docs" / "public-launch-runbook.md",
        REPO_ROOT / "docs" / "release.md",
    )
    unsafe_patterns = (
        "--hosted-url https://<",
        "POLICYNIM_BETA_MCP_URL=https://<",
        "codex mcp add policynim --url https://<",
        "claude mcp add --transport http policynim https://<",
    )

    for path in docs:
        text = _read_text(path)
        for pattern in unsafe_patterns:
            assert pattern not in text, (
                f"{path.relative_to(REPO_ROOT)} has an unquoted copyable "
                f"hosted placeholder: {pattern}"
            )


def test_hosted_shell_snippets_quote_token_placeholders() -> None:
    """Keep placeholder token exports pasteable in zsh and bash."""
    docs = (
        README,
        HOSTED_OPERATIONS,
        CODEX_README,
        CLAUDE_README,
        REPO_ROOT / "docs" / "demo-script.md",
    )
    assignments = {
        "NVIDIA_API_KEY": ("<your-nvidia-api-key>",),
        "POLICYNIM_TOKEN": ("<generated-beta-token>", "<issued-beta-token>"),
        "POLICYNIM_BETA_MCP_TOKEN": ("<beta-token>",),
    }

    for path in docs:
        for fence in _bash_fences(_read_text(path)):
            for line in fence.splitlines():
                for variable, placeholders in assignments.items():
                    for placeholder in placeholders:
                        if variable in line and placeholder in line:
                            assert (
                                f"{variable}='{placeholder}'" in line
                                or f'{variable}="{placeholder}"' in line
                            ), (
                                f"{path.relative_to(REPO_ROOT)} has an unquoted token "
                                f"placeholder in a bash snippet: {line}"
                            )


def test_readme_uses_hosted_first_commands() -> None:
    text = _read_text(README)
    local_setup_heading = "## Local Contributor Setup"

    assert local_setup_heading in text
    hosted_section = text.split(local_setup_heading, maxsplit=1)[0]

    _assert_contains_command(hosted_section, CODEX_HOSTED_COMMAND)
    _assert_contains_command(hosted_section, CLAUDE_HOSTED_COMMAND)
    assert "client_commands" in hosted_section


def test_hosted_docs_explain_quickstart_client_commands() -> None:
    """Keep quickstart JSON tied to a pasteable MCP client setup command."""
    for path in (README, WORKFLOWS_GUIDE, HOSTED_OPERATIONS):
        text = _read_text(path)
        normalized = _normalize_whitespace(text)

        assert "client_commands" in text
        assert "POLICYNIM_TOKEN" in text
        assert "MCP client command" in normalized


def test_readme_puts_copy_paste_setup_before_capability_inventory() -> None:
    """Keep the repo landing page optimized for first-run setup."""
    text = _read_text(README)

    assert "## Start Here: Pick A Path" in text
    assert "### Hosted MCP In A Few Clicks" in text
    assert "### Local CLI In A Few Commands" in text
    assert "### Source Checkout For Contributors" in text
    assert text.index("## Start Here: Pick A Path") < text.index("## What Works Today")
    assert text.index("### Hosted MCP In A Few Clicks") < text.index(
        "### Local CLI In A Few Commands"
    )
    assert text.index("### Local CLI In A Few Commands") < text.index(
        "### Source Checkout For Contributors"
    )


def test_readme_documents_high_value_agent_workflows() -> None:
    """Show developers why to add PolicyNIM to a coding-agent workflow."""
    text = _read_text(README)

    for token in (
        "## High-Value Agent Workflows",
        "Preflight before implementation",
        "Retrieve policy evidence while debugging",
        "Verify MCP wiring before a real session",
        "Attach issue-ready diagnostics",
        "Ask your coding agent to call `policy_preflight`",
        "Ask your coding agent to call `policy_search`",
        "policynim preflight --task",
        "policynim search --query",
        "policynim mcp-smoke --mcp-config-file",
        "policynim support-bundle --include-mcp-smoke",
        "docs/agent-workflows.md",
    ):
        assert token in text


def test_readme_documents_mcp_url_token_success_loop() -> None:
    """Let a developer start from only an MCP URL and reach a useful agent call."""
    text = _read_text(README)
    hosted_section = text.split("### Local CLI In A Few Commands", maxsplit=1)[0]
    normalized = _normalize_whitespace(hosted_section)

    for token in (
        "If you were given only the hosted MCP URL",
        "open it in a browser",
        "routes you to `/beta`",
        "create or rotate a token",
        "export POLICYNIM_TOKEN='<generated-beta-token>'",
        "List the PolicyNIM MCP tools",
        "Before editing, call policy_preflight for: Implement a refresh-token cleanup",
        "cited constraints",
        "insufficient_context",
    ):
        assert token in normalized


def test_agent_workflows_guide_documents_integration_recipes() -> None:
    """Give coding-agent users a dedicated playbook after setup succeeds."""
    text = _read_text(AGENT_WORKFLOWS)
    normalized = _normalize_whitespace(text)

    for token in (
        "# PolicyNIM Agent Workflows",
        "Start From Hosted MCP",
        "export POLICYNIM_TOKEN='<generated-beta-token>'",
        "codex mcp add policynim --url 'https://<railway-domain>/mcp'",
        "claude mcp add --transport http policynim 'https://<railway-domain>/mcp'",
        "When To Call Each Tool",
        "Before implementation",
        "Review or CI failure",
        "Release or automation change",
        "Ask your agent to call `policy_preflight` before it edits code",
        "If PolicyNIM returns `insufficient_context`, stop and call `policy_search`",
        "Use `policynim support-bundle --include-mcp-smoke`",
        "Do not paste bearer tokens into prompts, issues, logs, or MCP config JSON",
    ):
        assert token in normalized


def test_readme_links_to_split_docs_structure() -> None:
    text = _read_text(README)

    for relative_path in (
        "docs/index.md",
        "docs/contributor-guide.md",
        "docs/workflows.md",
        "docs/hosted-beta-operations.md",
        "docs/agent-workflows.md",
    ):
        assert relative_path in text


def test_install_docs_keep_hosted_path_no_setup() -> None:
    """Do not make local init/ingest look required for hosted MCP users."""
    for path in (README, CONTRIBUTOR_GUIDE):
        text = _read_text(path)
        normalized = _normalize_whitespace(text)

        assert "The hosted MCP path does not require local setup." in normalized
        assert "only when you choose a local CLI or local MCP workflow" in normalized


def test_docs_index_points_to_the_split_guides() -> None:
    text = _read_text(DOCS_INDEX)

    for relative_path in (
        "contributor-guide.md",
        "workflows.md",
        "hosted-beta-operations.md",
        "agent-workflows.md",
    ):
        assert relative_path in text


def test_docs_index_routes_first_run_by_developer_intent() -> None:
    text = _read_text(DOCS_INDEX)

    for token in (
        "## First-Run Paths",
        "Hosted MCP",
        "Local CLI",
        "Source checkout",
        "High-value agent workflows",
        "../README.md#start-here-pick-a-path",
        "../README.md#high-value-agent-workflows",
        "workflows.md#coding-agent-workflow-patterns",
    ):
        assert token in text


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


def test_hosted_examples_include_cli_generated_config_path() -> None:
    """Keep hosted MCP setup generated by the same CLI contract users can run."""
    for path in (README, WORKFLOWS_GUIDE, CODEX_README):
        _assert_contains_command(_read_text(path), CODEX_HOSTED_CONFIG_COMMAND)

    for path in (README, WORKFLOWS_GUIDE, CLAUDE_README):
        _assert_contains_command(_read_text(path), CLAUDE_HOSTED_CONFIG_COMMAND)

    for path in (README, WORKFLOWS_GUIDE, HOSTED_OPERATIONS, CODEX_README):
        _assert_contains_command(_read_text(path), CODEX_HOSTED_QUICKSTART_COMMAND)

    for path in (README, WORKFLOWS_GUIDE, HOSTED_OPERATIONS, CLAUDE_README):
        _assert_contains_command(_read_text(path), CLAUDE_HOSTED_QUICKSTART_COMMAND)


def test_hosted_docs_explain_placeholder_url_metadata() -> None:
    """Tell users when hosted MCP config JSON is only a placeholder smoke."""
    for path in (README, WORKFLOWS_GUIDE, HOSTED_OPERATIONS, CODEX_README, CLAUDE_README):
        text = _read_text(path)
        assert "`hosted_url`" in text
        assert "hosted_url_placeholder" in text
        assert "beta_portal_url" in text


def test_hosted_docs_route_browser_mcp_visits_to_token_creation() -> None:
    """Make the MCP URL itself a discoverable path to beta token creation."""
    for path in (README, WORKFLOWS_GUIDE, HOSTED_OPERATIONS):
        text = _read_text(path)
        normalized = _normalize_whitespace(text)
        assert "open the hosted `/mcp` URL in a browser" in normalized
        assert "routes you to `/beta`" in normalized
        assert "create or rotate a token" in normalized
        assert "Replace the hosted URL placeholder" in _normalize_whitespace(text)


def test_local_mcp_examples_include_doctor_recovery_hint() -> None:
    """Keep local fallback examples diagnosable without calling live providers."""
    for path in (CODEX_README, CLAUDE_README):
        text = _read_text(path)
        normalized = _normalize_whitespace(text)

        assert "uv run policynim doctor" in text
        assert "uv run policynim mcp-smoke" in text
        assert "uv run policynim mcp-config" in text
        assert "without calling NVIDIA-hosted APIs" in normalized


def test_local_mcp_examples_cover_installed_and_checkout_stdio_paths() -> None:
    """Keep local MCP examples aligned with no-clone and source-checkout configs."""
    codex_text = _read_text(CODEX_README)
    claude_text = _read_text(CLAUDE_README)

    for text in (codex_text, claude_text):
        assert "only if you need to run PolicyNIM from a clone" not in text
        normalized = _normalize_whitespace(text)
        assert "installed PolicyNIM CLI" in normalized
        assert "source checkout" in normalized
        assert "--repo-root /ABS/PATH/TO/policyNIM" in normalized

    _assert_contains_command(
        codex_text,
        "policynim mcp-config --target local-stdio --client codex",
    )
    _assert_contains_command(
        claude_text,
        "policynim mcp-config --target local-stdio --client claude-code",
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


def test_hosted_operations_doc_covers_portal_workflow_prompts() -> None:
    text = _read_text(HOSTED_OPERATIONS)
    normalized = _normalize_whitespace(text)

    for expected in (
        "portal dashboard shows",
        "agent workflow prompts",
        "Before editing, call policy_preflight for: Implement a refresh-token cleanup",
        "cited constraints",
        "insufficient_context",
        "Use policy_search for: release installer checksum verification.",
        "cited policy lines",
        (
            "List the PolicyNIM MCP tools and confirm policy_preflight and "
            "policy_search are available before starting implementation."
        ),
    ):
        assert expected in normalized


def test_hosted_operations_doc_covers_60_day_readiness_smoke() -> None:
    """Ensure hosted docs describe the live readiness smoke and recovery evidence."""
    text = _read_text(HOSTED_OPERATIONS)

    for expected in (
        "## 60-Day Readiness Verification",
        "curl -fsS 'https://<railway-domain>/healthz'",
        "POLICYNIM_BETA_MCP_URL='https://<railway-domain>/mcp'",
        "uv run --group test pytest -q -m live tests/test_hosted_mcp_live.py",
        "hosted-smoke-evidence",
        "policynim-hosted-smoke-junit.xml",
        "Hosted Beta Smoke",
        '`-m "not live and not docker_live"`',
        "Local index readiness could not be inspected",
        "upstream_failure_class",
    ):
        assert expected in text


def test_readme_links_to_contributor_and_workflow_guides() -> None:
    text = _read_text(README)

    assert CONTRIBUTOR_GUIDE.name in text
    assert WORKFLOWS_GUIDE.name in text


def test_tests_readme_distinguishes_client_and_smoke_env_vars() -> None:
    text = _read_text(TESTS_README)

    assert "`POLICYNIM_TOKEN`" in text
    assert "`POLICYNIM_BETA_MCP_URL`" in text
    assert "`POLICYNIM_BETA_MCP_TOKEN`" in text
