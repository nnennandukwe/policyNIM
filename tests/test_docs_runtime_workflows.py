"""Docs parity checks for runtime workflows and settings."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
WORKFLOWS_GUIDE = REPO_ROOT / "docs" / "workflows.md"
CONTRIBUTOR_GUIDE = REPO_ROOT / "docs" / "contributor-guide.md"
POLICY_TEMPLATE = REPO_ROOT / "policies" / "TEMPLATE.md"
TESTS_README = REPO_ROOT / "tests" / "README.md"
RELEASE_GUIDE = REPO_ROOT / "docs" / "release.md"
ARCHITECTURE_GUIDE = REPO_ROOT / "docs" / "architecture.md"
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
    assert "policynim init\npolicynim ingest" in workflows_text
    assert "Installed copies should keep using" in workflows_text
    assert "the direct `policynim ...` entrypoint" in workflows_text

    for path in STANDALONE_SETUP_DOCS:
        text = _read_text(path)
        assert "policynim init" in text, f"{path.name} should document standalone init"


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
        "uv run --group test --group dev pyright",
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
            "pipx install policynim",
            "uv tool install policynim",
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
            "policynim ingest",
            "policynim --help",
        ):
            assert token in text


def test_release_guide_documents_publish_checklist() -> None:
    """Keep GitHub and PyPI release steps discoverable."""
    index_text = _read_text(REPO_ROOT / "docs" / "index.md")
    release_text = _read_text(RELEASE_GUIDE)

    assert "release.md" in index_text
    for token in (
        "git tag v",
        "Release",
        "draft GitHub release",
        "SHA256SUMS",
        "PyPI trusted publishing",
        "Hosted Beta Smoke",
    ):
        assert token in release_text


def test_architecture_guide_documents_config_discovery_boundary() -> None:
    """Explain which modules own environment-backed config discovery."""
    text = _read_text(ARCHITECTURE_GUIDE)

    for token in (
        "src/policynim/config_discovery.py",
        "Discovers checkout, cwd, and standalone env-file precedence.",
        "Reads process environment for config-file overrides and hosted detection.",
    ):
        assert token in text
