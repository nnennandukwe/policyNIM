"""Docs parity checks for runtime workflows and settings."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
WORKFLOWS_GUIDE = REPO_ROOT / "docs" / "workflows.md"
AGENT_WORKFLOWS_GUIDE = REPO_ROOT / "docs" / "agent-workflows.md"
CONTRIBUTOR_GUIDE = REPO_ROOT / "docs" / "contributor-guide.md"
HOSTED_BETA_OPERATIONS = REPO_ROOT / "docs" / "hosted-beta-operations.md"
POLICY_TEMPLATE = REPO_ROOT / "policies" / "TEMPLATE.md"
TESTS_README = REPO_ROOT / "tests" / "README.md"
RELEASE_GUIDE = REPO_ROOT / "docs" / "release.md"
STANDALONE_SETUP_DOCS = (README, WORKFLOWS_GUIDE, CONTRIBUTOR_GUIDE)
ENV_EXAMPLES = (
    REPO_ROOT / ".env.example",
    REPO_ROOT / ".env.development.example",
    REPO_ROOT / ".env.production.example",
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workflows_guide_documents_runtime_request_shapes_and_sqlite_usage() -> None:
    text = _read_text(WORKFLOWS_GUIDE)

    for token in (
        "policynim runtime decide --input <path|->",
        "policynim runtime execute --input <path|->",
        "policynim evidence report --session-id <id>",
        "policynim evidence report --session-id <id> --format markdown --output reports/<id>.md",
        '"kind": "shell_command"',
        '"kind": "file_write"',
        '"kind": "http_request"',
        "session_id",
        "sqlite3",
        "allow is still a no-match runtime decision outcome",
    ):
        assert token in text


def test_public_docs_do_not_reference_retired_lancedb_backend() -> None:
    """Keep user-facing storage docs aligned with the SQLite vector backend."""
    public_docs = (
        README,
        REPO_ROOT / "docs" / "architecture.md",
        REPO_ROOT / "docs" / "architecture-diagram.md",
        REPO_ROOT / "docs" / "demo-script.md",
        WORKFLOWS_GUIDE,
    )

    for path in public_docs:
        text = _read_text(path)
        assert "LanceDB" not in text, f"{path.name} still names the retired backend"
        assert "lancedb" not in text, f"{path.name} still names the retired backend"


def test_contributor_guide_and_env_examples_include_runtime_settings() -> None:
    guide_text = _read_text(CONTRIBUTOR_GUIDE)
    for token in (
        "POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH",
        "POLICYNIM_RUNTIME_EVIDENCE_DB_PATH",
        "POLICYNIM_RUNTIME_SHELL_TIMEOUT_SECONDS",
    ):
        assert token in guide_text

    for path in ENV_EXAMPLES:
        text = _read_text(path)
        for token in (
            "POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH",
            "POLICYNIM_RUNTIME_EVIDENCE_DB_PATH",
            "POLICYNIM_RUNTIME_SHELL_TIMEOUT_SECONDS",
        ):
            assert token in text, f"{path.name} is missing {token}"


def test_standalone_setup_docs_use_installed_cli_entrypoint() -> None:
    """Document standalone setup with the installed CLI entrypoint."""
    workflows_text = _read_text(WORKFLOWS_GUIDE)
    assert "policynim quickstart --format json" in workflows_text
    assert "policynim init\npolicynim doctor\npolicynim ingest" in workflows_text
    assert "policynim doctor" in workflows_text
    assert "policynim doctor --format json" in workflows_text
    assert "Local MCP quickstart output can include exact filesystem paths" in workflows_text
    assert "local MCP `local_launch_mode`" in workflows_text
    assert "`installed-cli`" in workflows_text
    assert "`source-checkout`" in workflows_text
    assert "copyable `mcp-smoke` and local `mcp-config` commands" in workflows_text
    assert "`client_commands`" in workflows_text
    assert "copyable `agent_workflows` prompts" in workflows_text
    assert "generated follow-up CLI commands" in workflows_text
    assert "hosted MCP quickstart keeps the no-clone\ndirect" in workflows_text
    assert "policynim mcp-config ..." in workflows_text
    assert "Local CLI and local MCP quickstart output may use" in workflows_text
    assert "uv run policynim quickstart --target local-cli --format json" in workflows_text
    assert "may use\n`uv run policynim ...`" in workflows_text
    assert "installed copies use the direct\n`policynim ...` entrypoint" in workflows_text
    assert "Installed copies should keep using" in workflows_text
    assert "the direct `policynim ...` entrypoint" in workflows_text

    for path in STANDALONE_SETUP_DOCS:
        text = _read_text(path)
        assert "policynim init" in text, f"{path.name} should document standalone init"


def test_public_install_caveats_precede_copyable_install_commands() -> None:
    """Warn about stale public channels before users copy no-clone install commands."""
    for path in (README, CONTRIBUTOR_GUIDE):
        text = _read_text(path)
        pypi_status = text.index("Public PyPI install status:")
        github_status = text.index("GitHub release installer status:")
        pypi_command = text.index("pipx install --python 3.11 policynim")
        github_command = text.index(
            "https://github.com/nnennandukwe/policyNIM/releases/latest/download/install.sh"
        )

        assert pypi_status < pypi_command, f"{path.name} should warn before PyPI install"
        assert github_status < github_command, f"{path.name} should warn before GitHub installer"


def test_workflows_guide_documents_local_mcp_smoke() -> None:
    """Document the deterministic stdio tool-list smoke before manual client setup."""
    text = _read_text(WORKFLOWS_GUIDE)

    for token in (
        "policynim mcp-smoke",
        "policynim mcp-smoke --format json",
        "policynim mcp-smoke --mcp-config-file",
        "uv run policynim quickstart --target local-mcp",
        "policynim mcp-config",
        "policy_preflight",
        "policy_search",
        "does not call either tool",
        "recovery steps",
        "For public issues, prefer",
        "policynim support-bundle --include-mcp-smoke",
        "`mcp-smoke --format json` output can include exact local launch paths",
        "Regenerate client config",
        "Local `mcp-config` output can include exact filesystem paths",
        "Use `policynim support-bundle`",
        "public diagnostics",
        "NVIDIA_API_KEY",
    ):
        assert token in text


def test_workflows_guide_documents_support_bundle() -> None:
    """Document the issue-ready diagnostic bundle and MCP-smoke opt-in."""
    text = _read_text(WORKFLOWS_GUIDE)

    for token in (
        "policynim support-bundle",
        "--include-mcp-smoke",
        "--include-local-paths",
        "issue-ready diagnostics",
        "first-run target summary",
        "`first_run` section",
        "`quickstart_command`",
        "`hosted_url`",
        "`beta_portal_url`",
        "browser token-creation steps",
        "`agent_workflows`",
        "copyable `policy_preflight`, `policy_search`, and MCP tool-list prompts",
        "hosted MCP, local CLI, and local MCP quickstart targets",
        "Source-checkout bundles keep those generated commands",
        "does not print configured secret values",
        "path prefixes are redacted",
        "private maintainer",
    ):
        assert token in text


def test_workflows_guide_documents_agent_integration_patterns() -> None:
    """Keep the workflow guide useful for coding-agent users, not just CLI reference."""
    text = _read_text(WORKFLOWS_GUIDE)

    for token in (
        "## Coding-Agent Workflow Patterns",
        "Run policy preflight before implementation",
        "Search policy evidence during review or debugging",
        "Smoke MCP setup before a long agent session",
        "Attach diagnostics when setup fails",
        "Ask your coding agent to call `policy_preflight`",
        "Ask your coding agent to call `policy_search`",
        "policynim preflight --task",
        "policynim search --query",
        "policynim mcp-smoke --mcp-config-file",
        "policynim support-bundle --include-mcp-smoke",
    ):
        assert token in text


def test_source_checkout_setup_docs_state_init_writes_checkout_dotenv() -> None:
    """Document source-checkout init as writing the checkout env file."""
    for path in STANDALONE_SETUP_DOCS:
        text = _read_text(path)
        assert "uv run policynim init" in text
        assert "checkout `.env`" in text


def test_production_env_example_uses_absolute_runtime_paths() -> None:
    production_text = _read_text(REPO_ROOT / ".env.production.example")

    assert "POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH=/app/data/runtime/runtime_rules.json" in (
        production_text
    )
    assert "POLICYNIM_RUNTIME_EVIDENCE_DB_PATH=/app/state/runtime_evidence.sqlite3" in (
        production_text
    )


def test_policy_template_includes_runtime_rules_authoring_guidance() -> None:
    text = _read_text(POLICY_TEMPLATE)

    for token in (
        "runtime_rules:",
        "effect: confirm",
        "exactly one matcher family",
        "allow is not an authored runtime rule effect",
    ):
        assert token in text


def test_tests_readme_mentions_runtime_and_docs_parity_coverage() -> None:
    text = _read_text(TESTS_README)

    for token in (
        "Real SQLite-backed CLI runtime execution plus `evidence report` coverage",
        "Runtime docs parity",
    ):
        assert token in text


def test_hosted_operations_documents_operator_beta_release_gate() -> None:
    """Document the deterministic and opt-in hosted beta release checks."""
    text = _read_text(REPO_ROOT / "docs" / "hosted-beta-operations.md")

    for token in (
        "90-Day Operator Beta Release Gate",
        "uv run ruff check .",
        "uv run pyright",
        'uv run pytest -q -m "not live and not docker_live"',
        "uv run --group test pytest -q -m live tests/test_hosted_mcp_live.py",
        "POLICYNIM_RUN_DOCKER_TESTS=1 uv run --group test pytest -q -m docker_live",
        "policynim beta-admin audit-log",
    ):
        assert token in text


def test_install_docs_cover_direct_cli_channels() -> None:
    """Document install paths that do not require cloning the repo."""
    readme = _read_text(README)
    contributor = _read_text(CONTRIBUTOR_GUIDE)

    for text in (readme, contributor):
        for token in (
            "Use the PyPI package path",
            "pipx install --python 3.11 policynim",
            "uv tool install --python 3.11 policynim",
            "trusted-publishing evidence",
            "Public PyPI install status",
            "`pypi_install_smoke`",
            "GitHub release installer status",
            "`github_release_install_smoke`",
            "If `policynim quickstart` is unavailable",
            "does not pass the public launch gate",
            "macOS Apple Silicon (`darwin-arm64`)",
            "macOS Intel (`darwin-amd64`)",
            "Linux x86_64 (`linux-amd64`)",
            "Windows x86_64\n(`windows-amd64`)",
            "`install.sh` auto-detects the supported macOS or Linux\ntarget",
            "`install.ps1` installs the Windows\nbundle",
            (
                "curl -fsSL "
                "https://github.com/nnennandukwe/policyNIM/releases/latest/download/install.sh | sh"
            ),
            (
                "irm "
                "https://github.com/nnennandukwe/policyNIM/releases/latest/download/"
                "install.ps1 | iex"
            ),
            "policynim init",
            "policynim quickstart",
            "policynim doctor",
            "policynim ingest",
            "policynim --help",
            "POLICYNIM_VERIFY_ATTESTATION=1",
            "GitHub CLI",
            "gh attestation verify",
        ):
            assert token in text


def test_hosted_getting_started_docs_show_client_specific_quickstart_commands() -> None:
    """Keep hosted first-run docs explicit for both Codex and Claude Code users."""
    for path in (README, WORKFLOWS_GUIDE, AGENT_WORKFLOWS_GUIDE, HOSTED_BETA_OPERATIONS):
        text = _read_text(path)
        assert (
            "policynim quickstart --target hosted-mcp --client codex "
            "--hosted-url 'https://<railway-domain>/mcp' --format json"
        ) in text, f"{path.name} should show the Codex hosted quickstart command"
        assert (
            "policynim quickstart --target hosted-mcp --client claude-code "
            "--hosted-url 'https://<railway-domain>/mcp' --format json"
        ) in text, f"{path.name} should show the Claude Code hosted quickstart command"
        assert "selected client" in text, f"{path.name} should explain client-specific output"


def test_release_guide_documents_publish_checklist() -> None:
    """Keep GitHub and PyPI release steps discoverable."""
    index_text = _read_text(REPO_ROOT / "docs" / "index.md")
    release_text = _read_text(RELEASE_GUIDE)

    assert "release.md" in index_text
    for token in (
        "Ship/Hold Release Gate",
        "SHIP only when",
        "HOLD when",
        "scripts/release_check.py",
        "scripts/release_manifest.py",
        "--format launch-issue",
        "proof collection commands in that issue use `--require-requested-probes`",
        "passes that same file to the readiness JSON and launch-issue renderer",
        "--release-tag v<version>",
        "git tag v<version>",
        "policynim-v<version>-linux-amd64.tar.gz",
        "policynim-<version>-py3-none-any.whl",
        "summary to name every current expected release\nasset",
        "gh auth status",
        "--live --format\njson",
        "--apply --format json",
        "uv run python -m venv /tmp/policynim-wheel-smoke",
        "RELEASE_MANIFEST.json",
        "git tag v",
        "Release",
        "draft GitHub release",
        "SHA256SUMS",
        "quickstart --format json",
        "semantic quickstart contracts",
        "hosted `client_commands`",
        "copyable `agent_workflows`",
        "support-bundle first-run",
        "each target's `quickstart_command`",
        "hosted `client_commands`",
        "`agent_workflows`",
        "checkout-only `uv run` or `--repo-root` commands",
        "quickstart --target local-cli --format json",
        "quickstart --target local-mcp --format json",
        "support-bundle",
        "standalone bundle",
        "standalone MCP stdio smoke",
        "standalone local stdio config for Codex and Claude Code",
        "mcp-config",
        "--target hosted-http",
        "https://example.invalid/mcp",
        "PyPI trusted publishing",
        "public PyPI JSON lists the current wheel and sdist",
        "primary command help",
        "semantic first-run quickstart JSON",
        "install.sh guidance",
        "support-bundle hosted `client_commands`",
        "support-bundle `hosted_url`/`beta_portal_url` token flow",
        "local MCP config JSON",
        "GitHub release artifact\nprobe failures",
        "stale release evidence",
        "publish-pypi",
        "POLICYNIM_VERIFY_ATTESTATION=1",
        "gh attestation verify",
        "Hosted Beta Smoke",
    ):
        assert token in release_text

    assert "v0.1.0" not in release_text
    assert "policynim-0.1.0" not in release_text


def test_oss_readiness_audit_documents_pr_package_smoke_evidence() -> None:
    """Keep PR package evidence visible in the public readiness map."""
    text = _read_text(REPO_ROOT / "docs" / "oss-readiness-audit.md")

    for token in (
        "package-smoke-evidence",
        "PR reviewers",
        "all first-run quickstart targets",
        "agent_workflows",
        "MCP stdio smoke",
        "Codex and Claude Code MCP config",
        "hosted HTTP config JSON",
    ):
        assert token in text


def test_oss_readiness_audit_documents_high_value_pr_sequence() -> None:
    """Keep the OSS readiness work split into reviewable high-value PRs."""
    text = _read_text(REPO_ROOT / "docs" / "oss-readiness-audit.md")

    for token in (
        "## High-Value PR Sequence",
        "First-run and hosted MCP onboarding",
        "Local CLI and MCP verification loop",
        "Installability and release trust",
        "SQLite migration and storage contract",
        "Maintainer trust and public launch proof",
        "one user-facing thesis",
        "one primary evidence\nsurface",
        "bounded rollback story",
        "Do not combine these into one review",
        "public-launch claims blocked on\nexternal evidence",
    ):
        assert token in text
