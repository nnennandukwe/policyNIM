"""Explicit repository targeting and bounded GitHub CLI failures for maintainer commands."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _script(kind: str) -> ModuleType:
    """Load each standalone command without invoking its entry point."""
    spec = importlib.util.spec_from_file_location(
        "metadata_" + kind, ROOT / "scripts" / ("sync_github_" + kind + ".py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind", ["labels", "topics"])
def test_default_preview_names_the_repository_even_from_another_directory(
    tmp_path: Path, kind: str
) -> None:
    """Offline plans can't silently inherit another checkout's repository target."""
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / ("sync_github_" + kind + ".py")),
            "--format",
            "json",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["repo"] == "nnennandukwe/policyNIM"


@pytest.mark.parametrize("kind", ["labels", "topics"])
def test_live_read_has_an_explicit_repository_target(kind: str) -> None:
    """GitHub reads are bound to the reviewed target instead of ambient gh state."""
    module = _script(kind)
    commands: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        """Return empty existing metadata while recording the actual target arguments."""
        commands.append(command)
        return subprocess.CompletedProcess(
            command, 0, "[]" if kind == "labels" else '{"repositoryTopics": []}', ""
        )

    sync = module.sync_labels if kind == "labels" else module.sync_topics
    desired = (
        [{"name": "type/bug", "color": "d73a4a", "description": "Bug reports."}]
        if kind == "labels"
        else ["mcp"]
    )
    result = sync(
        desired=desired, apply=False, live=True, repo="nnennandukwe/policyNIM", runner=runner
    )
    assert result["repo"] == "nnennandukwe/policyNIM"
    assert len(commands) == 1 and "nnennandukwe/policyNIM" in commands[0]


@pytest.mark.parametrize("kind", ["labels", "topics"])
def test_git_hub_timeouts_have_bounded_safe_recovery(kind: str) -> None:
    """Timeout errors don't print captured subprocess output or an unhandled traceback."""
    module = _script(kind)

    def timeout(command: list[str]) -> subprocess.CompletedProcess[str]:
        """Inject a timeout with private captured output at the process boundary."""
        raise subprocess.TimeoutExpired(command, 30, output="private-captured-output")

    sync = module.sync_labels if kind == "labels" else module.sync_topics
    error = module.LabelSyncError if kind == "labels" else module.TopicSyncError
    with pytest.raises(error, match="timed out") as caught:
        sync(desired=[], apply=False, live=True, runner=timeout)
    assert "private-captured-output" not in str(caught.value)


@pytest.mark.parametrize("kind", ["labels", "topics"])
def test_malformed_remote_names_prevent_all_writes(kind: str) -> None:
    """Malformed API responses can't become a partially applied metadata plan."""
    module = _script(kind)
    commands: list[list[str]] = []

    def malformed(command: list[str]) -> subprocess.CompletedProcess[str]:
        """Return an invalid API name that string coercion would otherwise accept."""
        commands.append(command)
        payload = (
            [{"name": 123, "color": "000000", "description": "Bad API data."}]
            if kind == "labels"
            else {"repositoryTopics": [{"name": 123}]}
        )
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    sync = module.sync_labels if kind == "labels" else module.sync_topics
    error = module.LabelSyncError if kind == "labels" else module.TopicSyncError
    with pytest.raises(error, match="string"):
        sync(desired=[], apply=True, runner=malformed)
    assert len(commands) == 1


@pytest.mark.parametrize("field", ["name", "description"])
def test_whitespace_label_metadata_fails_before_any_github_call(tmp_path: Path, field: str) -> None:
    """A malformed desired taxonomy can't produce a misleading or partially applied plan."""
    values = {"name": "type/bug", "color": "d73a4a", "description": "Bug reports."}
    values[field] = "   "
    taxonomy = tmp_path / "labels.yml"
    taxonomy.write_text(
        f'- name: "{values["name"]}"\n  color: "{values["color"]}"\n'
        f'  description: "{values["description"]}"\n'
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "sync_github_labels.py"),
            "--labels-file",
            str(taxonomy),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "missing" in result.stderr and not result.stdout


def test_capped_remote_label_list_cant_produce_a_partial_write_plan() -> None:
    """A full-size capped response is unknown completeness and stops before mutation."""
    module = _script("labels")
    commands: list[list[str]] = []

    def capped(command: list[str]) -> subprocess.CompletedProcess[str]:
        """Return exactly the configured cap, including no proof of the next remote page."""
        commands.append(command)
        rows = [
            {"name": f"existing-{index}", "color": "000000", "description": "Existing."}
            for index in range(1000)
        ]
        return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")

    with pytest.raises(module.LabelSyncError, match="incomplete"):
        module.sync_labels(
            desired=[{"name": "desired", "color": "000000", "description": "Desired."}],
            apply=True,
            runner=capped,
        )
    assert len(commands) == 1
