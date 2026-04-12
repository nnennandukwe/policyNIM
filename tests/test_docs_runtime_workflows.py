"""Docs parity checks for runtime workflows and settings."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_GUIDE = REPO_ROOT / "docs" / "workflows.md"
CONTRIBUTOR_GUIDE = REPO_ROOT / "docs" / "contributor-guide.md"
POLICY_TEMPLATE = REPO_ROOT / "policies" / "TEMPLATE.md"
TESTS_README = REPO_ROOT / "tests" / "README.md"
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
