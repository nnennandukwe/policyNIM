"""GitHub label sync script contract tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync_github_labels.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_github_labels", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_label_taxonomy_preserves_names_colors_and_descriptions(tmp_path: Path) -> None:
    """Load the checked-in label taxonomy without relying on GitHub state."""
    module = _load_script_module()
    label_file = tmp_path / "labels.yml"
    label_file.write_text(
        "\n".join(
            [
                "- name: type/bug",
                '  color: "d73a4a"',
                "  description: Reproducible broken behavior.",
                "",
                "- name: surface/mcp-stdio",
                '  color: "5319e7"',
                "  description: Local stdio MCP server.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    labels = module.load_label_taxonomy(label_file)

    assert [label["name"] for label in labels] == ["type/bug", "surface/mcp-stdio"]
    assert labels[0] == {
        "name": "type/bug",
        "color": "d73a4a",
        "description": "Reproducible broken behavior.",
    }


def test_plan_label_sync_creates_updates_and_leaves_matching_labels() -> None:
    """Produce deterministic create/update/noop actions from current GitHub labels."""
    module = _load_script_module()
    desired = [
        {"name": "type/bug", "color": "d73a4a", "description": "Bug reports."},
        {"name": "surface/cli", "color": "1d76db", "description": "CLI issues."},
        {"name": "priority/p1", "color": "d93f0b", "description": "Blocks first run."},
    ]
    existing = [
        {"name": "type/bug", "color": "d73a4a", "description": "Bug reports."},
        {"name": "surface/cli", "color": "000000", "description": "Old CLI text."},
    ]

    plan = module.plan_label_sync(desired, existing)

    assert [entry["action"] for entry in plan] == ["noop", "update", "create"]
    assert plan[0]["name"] == "type/bug"
    assert plan[1]["updates"] == {
        "color": {"from": "000000", "to": "1d76db"},
        "description": {"from": "Old CLI text.", "to": "CLI issues."},
    }
    assert plan[2]["name"] == "priority/p1"


def test_sync_github_labels_dry_run_outputs_json_without_gh_calls() -> None:
    """Keep the default path safe and machine-readable for maintainers."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--labels-file",
            str(REPO_ROOT / ".github" / "labels.yml"),
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
    assert payload["labels_file"] == str(REPO_ROOT / ".github" / "labels.yml")
    assert "--live" in payload["next_step"]
    assert any(entry["name"] == "type/bug" for entry in payload["plan"])
    assert all(entry["action"] == "create" for entry in payload["plan"])


def test_live_dry_run_reads_gh_state_without_applying_label_changes() -> None:
    """Let maintainers inspect the real GitHub delta without mutating labels."""
    module = _load_script_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                [
                    {
                        "name": "type/bug",
                        "color": "d73a4a",
                        "description": "Bug reports.",
                    },
                    {
                        "name": "surface/cli",
                        "color": "000000",
                        "description": "Old text.",
                    },
                ]
            ),
            stderr="",
        )

    result = module.sync_labels(
        desired=[
            {"name": "type/bug", "color": "d73a4a", "description": "Bug reports."},
            {"name": "surface/cli", "color": "1d76db", "description": "CLI issues."},
            {"name": "priority/p1", "color": "d93f0b", "description": "Blocks first run."},
        ],
        apply=False,
        live=True,
        runner=fake_runner,
    )

    assert result["mode"] == "live-dry-run"
    assert [entry["action"] for entry in result["plan"]] == ["noop", "update", "create"]
    assert calls == [["gh", "label", "list", "--json", "name,color,description", "--limit", "1000"]]
    assert "rerun with --apply" in result["next_step"]


def test_apply_mode_runs_gh_create_and_update_commands(tmp_path: Path) -> None:
    """Apply mode should call gh only after an explicit --apply flag."""
    module = _load_script_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["gh", "label", "list"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "name": "surface/cli",
                            "color": "000000",
                            "description": "Old text.",
                        }
                    ]
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = module.sync_labels(
        desired=[
            {"name": "type/bug", "color": "d73a4a", "description": "Bug reports."},
            {"name": "surface/cli", "color": "1d76db", "description": "CLI issues."},
        ],
        apply=True,
        runner=fake_runner,
    )

    assert result["mode"] == "apply"
    assert [entry["action"] for entry in result["plan"]] == ["create", "update"]
    assert [
        "gh",
        "label",
        "create",
        "type/bug",
        "--color",
        "d73a4a",
        "--description",
        "Bug reports.",
    ] in calls
    assert [
        "gh",
        "label",
        "edit",
        "surface/cli",
        "--color",
        "1d76db",
        "--description",
        "CLI issues.",
    ] in calls


def test_apply_and_live_modes_are_mutually_exclusive() -> None:
    """Prevent an inspect-only command from being combined with mutation mode."""
    module = _load_script_module()

    with pytest.raises(module.LabelSyncError, match="--apply and --live"):
        module.sync_labels(
            desired=[{"name": "type/bug", "color": "d73a4a", "description": "Bug reports."}],
            apply=True,
            live=True,
            runner=lambda command: subprocess.CompletedProcess(command, 0, stdout="[]", stderr=""),
        )


def test_apply_mode_reports_missing_gh_without_traceback() -> None:
    """A maintainer without gh installed should get recovery guidance."""
    module = _load_script_module()

    def missing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    with pytest.raises(module.LabelSyncError) as exc_info:
        module.sync_labels(
            desired=[{"name": "type/bug", "color": "d73a4a", "description": "Bug reports."}],
            apply=True,
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

    with pytest.raises(module.LabelSyncError) as exc_info:
        module.sync_labels(
            desired=[{"name": "type/bug", "color": "d73a4a", "description": "Bug reports."}],
            apply=False,
            live=True,
            runner=missing_runner,
        )

    message = str(exc_info.value)
    assert "GitHub CLI" in message
    assert "gh auth status" in message
    assert "--live" in message
    assert "required for --apply" not in message
