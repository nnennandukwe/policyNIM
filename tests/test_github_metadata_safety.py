"""Explicit repository targeting and bounded GitHub CLI failures for maintainer commands."""

from __future__ import annotations

import importlib.util
import json
import os
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


def _duplicate_taxonomy(path: Path, duplicate_name: str) -> None:
    """Write repeated normalized names with conflicting metadata for the command boundary."""
    path.write_text(
        '- name: "type/bug"\n  color: "d73a4a"\n  description: "First entry."\n'
        f'- name: "{duplicate_name}"\n  color: "000000"\n  description: "Repeated entry."\n'
    )


@pytest.mark.parametrize("duplicate_name", ["type/bug", " type/bug ", "TYPE/BUG"])
def test_label_taxonomy_rejects_repeated_normalized_names(
    tmp_path: Path, duplicate_name: str
) -> None:
    """The loader rejects repeated names before planning multiple writes to one label."""
    module = _script("labels")
    taxonomy = tmp_path / "labels.yml"
    _duplicate_taxonomy(taxonomy, duplicate_name)
    with pytest.raises(module.LabelSyncError, match="Duplicate label name"):
        module.load_label_taxonomy(taxonomy)


@pytest.mark.skipif(
    sys.platform == "win32", reason="Exercises a POSIX executable process boundary."
)
@pytest.mark.parametrize("mode", ["dry-run", "live", "apply"])
@pytest.mark.parametrize("duplicate_name", ["type/bug", " type/bug ", "TYPE/BUG"])
def test_duplicate_taxonomy_stops_public_cli_before_any_github_process(
    tmp_path: Path, mode: str, duplicate_name: str
) -> None:
    """Real command dispatch rejects duplicates before simulated partial external application."""
    taxonomy = tmp_path / "labels.yml"
    _duplicate_taxonomy(taxonomy, duplicate_name)
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    receipt = tmp_path / "github-invocations.json"
    fake_gh = binary_directory / "gh"
    fake_gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "receipt = Path(os.environ['POLICYNIM_TEST_GH_RECEIPT'])\n"
        "calls = json.loads(receipt.read_text()) if receipt.exists() else []\n"
        "calls.append(sys.argv[1:])\n"
        "receipt.write_text(json.dumps(calls))\n"
        "if sys.argv[1:3] == ['label', 'list']:\n"
        "    print('[]')\n"
        "elif sys.argv[1:3] == ['label', 'create']:\n"
        "    previous = [call for call in calls[:-1] if call[:2] == ['label', 'create']]\n"
        "    if any(call[2].casefold() == sys.argv[3].casefold() for call in previous):\n"
        "        sys.stderr.write('label already exists after prior create')\n"
        "        sys.exit(1)\n"
    )
    fake_gh.chmod(0o755)
    arguments = [
        sys.executable,
        str(ROOT / "scripts" / "sync_github_labels.py"),
        "--labels-file",
        str(taxonomy),
        "--format",
        "json",
    ]
    if mode != "dry-run":
        arguments.append("--" + mode)
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": str(binary_directory) + os.pathsep + os.environ.get("PATH", ""),
            "POLICYNIM_TEST_GH_RECEIPT": str(receipt),
        },
    )
    assert not receipt.exists(), "GitHub invocations: " + receipt.read_text()
    assert result.returncode == 1
    assert "Duplicate label name" in result.stderr and not result.stdout


@pytest.mark.parametrize(("color", "action"), [("000000", "noop"), ("ffffff", "update")])
def test_label_plan_matches_existing_case_variants_without_creating_again(
    color: str, action: str
) -> None:
    """Desired casing can't turn an existing GitHub label identity into another create."""
    module = _script("labels")
    desired = [{"name": "TYPE/BUG", "color": color, "description": "Bug reports."}]
    existing = [{"name": "type/bug", "color": "000000", "description": "Bug reports."}]
    plan = module.plan_label_sync(desired, existing)
    assert len(plan) == 1 and plan[0]["action"] == action
    assert plan[0]["name"] == "TYPE/BUG"
