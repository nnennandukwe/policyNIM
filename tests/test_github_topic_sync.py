"""GitHub topic sync script contract tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync_github_topics.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_github_topics", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_topic_taxonomy_preserves_order_and_rejects_invalid_names(
    tmp_path: Path,
) -> None:
    """Load the checked-in topic taxonomy without relying on GitHub state."""
    module = _load_script_module()
    topics_file = tmp_path / "topics.yml"
    topics_file.write_text(
        "\n".join(
            [
                "# Discoverability topics",
                "- ai-agents",
                "- mcp",
                "- nvidia-nim",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert module.load_topic_taxonomy(topics_file) == ["ai-agents", "mcp", "nvidia-nim"]

    topics_file.write_text("- Invalid Topic\n", encoding="utf-8")
    with pytest.raises(module.TopicSyncError, match="lowercase"):
        module.load_topic_taxonomy(topics_file)


def test_plan_topic_sync_adds_removes_and_leaves_matching_topics() -> None:
    """Produce deterministic add/remove/noop actions from current GitHub topics."""
    module = _load_script_module()

    plan = module.plan_topic_sync(
        desired=["ai-agents", "mcp", "python"],
        existing=["ai-agents", "old-topic", "python"],
    )

    assert plan == [
        {"action": "noop", "topic": "ai-agents"},
        {"action": "add", "topic": "mcp"},
        {"action": "noop", "topic": "python"},
        {"action": "remove", "topic": "old-topic"},
    ]


def test_sync_github_topics_dry_run_outputs_json_without_gh_calls() -> None:
    """Keep the default path safe and machine-readable for maintainers."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--topics-file",
            str(REPO_ROOT / ".github" / "topics.yml"),
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["mode"] == "dry-run"
    assert payload["topics_file"] == str(REPO_ROOT / ".github" / "topics.yml")
    assert "--live" in payload["next_step"]
    assert any(entry["topic"] == "mcp" for entry in payload["plan"])
    assert all(entry["action"] == "ensure" for entry in payload["plan"])


def test_live_dry_run_reads_gh_topics_without_applying_changes() -> None:
    """Let maintainers inspect the real topic delta without mutating GitHub."""
    module = _load_script_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "nameWithOwner": "nnennandukwe/policyNIM",
                    "repositoryTopics": [
                        {"name": "ai-agents"},
                        {"name": "old-topic"},
                    ],
                }
            ),
            stderr="",
        )

    result = module.sync_topics(
        desired=["ai-agents", "mcp"],
        apply=False,
        live=True,
        repo="nnennandukwe/policyNIM",
        runner=fake_runner,
    )

    assert result["mode"] == "live-dry-run"
    assert result["plan"] == [
        {"action": "noop", "topic": "ai-agents"},
        {"action": "add", "topic": "mcp"},
        {"action": "remove", "topic": "old-topic"},
    ]
    assert calls == [
        [
            "gh",
            "repo",
            "view",
            "nnennandukwe/policyNIM",
            "--json",
            "repositoryTopics,nameWithOwner",
        ]
    ]
    assert "rerun with --apply" in result["next_step"]


def test_apply_mode_runs_gh_topic_add_and_remove_commands() -> None:
    """Apply mode should call gh only after an explicit --apply flag."""
    module = _load_script_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "nameWithOwner": "nnennandukwe/policyNIM",
                        "repositoryTopics": [
                            {"name": "ai-agents"},
                            {"name": "old-topic"},
                        ],
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = module.sync_topics(
        desired=["ai-agents", "mcp"],
        apply=True,
        repo="nnennandukwe/policyNIM",
        runner=fake_runner,
    )

    assert result["mode"] == "apply"
    assert [entry["action"] for entry in result["plan"]] == ["noop", "add", "remove"]
    assert [
        "gh",
        "repo",
        "edit",
        "nnennandukwe/policyNIM",
        "--add-topic",
        "mcp",
    ] in calls
    assert [
        "gh",
        "repo",
        "edit",
        "nnennandukwe/policyNIM",
        "--remove-topic",
        "old-topic",
    ] in calls


def test_apply_and_live_modes_are_mutually_exclusive() -> None:
    """Prevent an inspect-only command from being combined with mutation mode."""
    module = _load_script_module()

    with pytest.raises(module.TopicSyncError, match="--apply and --live"):
        module.sync_topics(
            desired=["mcp"],
            apply=True,
            live=True,
            repo=None,
            runner=lambda command: subprocess.CompletedProcess(
                command,
                0,
                stdout='{"repositoryTopics": []}',
                stderr="",
            ),
        )


def test_apply_mode_reports_missing_gh_without_traceback() -> None:
    """A maintainer without gh installed should get recovery guidance."""
    module = _load_script_module()

    def missing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    with pytest.raises(module.TopicSyncError) as exc_info:
        module.sync_topics(
            desired=["mcp"],
            apply=True,
            repo=None,
            runner=missing_runner,
        )

    message = str(exc_info.value)
    assert "GitHub CLI" in message
    assert "gh auth status" in message


def test_live_mode_reports_missing_gh_without_apply_only_guidance() -> None:
    """Inspect-only live mode should not tell maintainers gh is only required for --apply."""
    module = _load_script_module()

    def missing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    with pytest.raises(module.TopicSyncError) as exc_info:
        module.sync_topics(
            desired=["mcp"],
            apply=False,
            live=True,
            repo="nnennandukwe/policyNIM",
            runner=missing_runner,
        )

    message = str(exc_info.value)
    assert "GitHub CLI" in message
    assert "gh auth status" in message
    assert "--live" in message
    assert "required for --apply" not in message
