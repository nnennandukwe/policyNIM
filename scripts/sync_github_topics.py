"""Dry-run or apply the PolicyNIM GitHub repository topic taxonomy with gh."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

DEFAULT_REPOSITORY = "nnennandukwe/policyNIM"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOPICS_FILE = REPO_ROOT / ".github" / "topics.yml"
TOPIC_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
Action = Literal["add", "remove", "noop", "ensure"]


def main() -> int:
    """Parse flags and report a local preview, live preview or explicit apply result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--topics-file",
        type=Path,
        default=DEFAULT_TOPICS_FILE,
        help="Path to the repository topic taxonomy.",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPOSITORY,
        help="GitHub repository in OWNER/REPO form.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Apply add/remove topic operations with gh. Defaults to dry-run.",
    )
    mode_group.add_argument(
        "--live",
        action="store_true",
        help=(
            "Compare against current GitHub topics without applying changes. "
            "Defaults to offline dry-run."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format for the sync plan.",
    )
    args = parser.parse_args()

    try:
        desired = load_topic_taxonomy(args.topics_file)
        result = sync_topics(
            desired=desired,
            apply=args.apply,
            live=args.live,
            repo=args.repo,
            topics_file=args.topics_file.resolve(strict=False),
        )
    except TopicSyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(_render_text(result))
    return 0


class TopicSyncError(Exception):
    """GitHub topic sync failed."""


def load_topic_taxonomy(path: Path) -> list[str]:
    """Load the limited YAML topic taxonomy used by this repository."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TopicSyncError(f"Couldn't read topic taxonomy {path}: {exc}") from exc

    topics: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("- "):
            raise TopicSyncError(f"Invalid topic taxonomy line: {raw_line}")
        topic = stripped.removeprefix("- ").strip()
        topics.append(_validate_topic(topic))
    if not topics:
        raise TopicSyncError(f"No topics found in {path}.")
    duplicates = sorted({topic for topic in topics if topics.count(topic) > 1})
    if duplicates:
        raise TopicSyncError(f"Duplicate topics found: {', '.join(duplicates)}")
    return topics


def plan_topic_sync(desired: list[str], existing: list[str]) -> list[dict[str, str]]:
    """Return deterministic add/remove/noop actions for desired topics."""
    existing_topics = set(existing)
    desired_topics = set(desired)
    plan: list[dict[str, str]] = []
    for topic in desired:
        action: Action = "noop" if topic in existing_topics else "add"
        plan.append({"action": action, "topic": topic})
    for topic in sorted(existing_topics - desired_topics):
        plan.append({"action": "remove", "topic": topic})
    return plan


def sync_topics(
    *,
    desired: list[str],
    apply: bool,
    live: bool = False,
    repo: str | None = None,
    topics_file: Path | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Build or apply a GitHub topic sync plan."""
    if apply and live:
        raise TopicSyncError("--apply and --live cannot be used together.")
    repo = _validate_repository(repo or DEFAULT_REPOSITORY)
    command_runner = runner or _run_command
    if apply or live:
        existing = _load_existing_topics(command_runner, repo=repo)
        plan = plan_topic_sync(desired, existing)
    else:
        plan = [{"action": "ensure", "topic": topic} for topic in desired]
    if apply:
        for entry in plan:
            _apply_plan_entry(entry, command_runner, repo=repo)
    mode = "apply" if apply else "live-dry-run" if live else "dry-run"
    return {
        "schema_version": "1",
        "mode": mode,
        "repo": repo,
        "topics_file": str(topics_file) if topics_file is not None else None,
        "plan": plan,
        "next_step": _next_step_for_mode(apply=apply, live=live, plan=plan),
    }


def _load_existing_topics(runner: Runner, *, repo: str | None) -> list[str]:
    """Validate the GitHub topic response before planning any writes."""
    command = ["gh", "repo", "view"]
    if repo:
        command.append(repo)
    command.extend(["--json", "repositoryTopics,nameWithOwner"])
    completed = _run_gh(command, runner, failure="list existing GitHub topics")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TopicSyncError(f"Couldn't parse gh repo view JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TopicSyncError("gh repo view returned JSON that wasn't an object.")
    raw_topics = payload.get("repositoryTopics")
    if not isinstance(raw_topics, list):
        raise TopicSyncError("gh repo view didn't return a repositoryTopics list.")
    topics: list[str] = []
    for raw_topic in raw_topics:
        if not isinstance(raw_topic, dict):
            raise TopicSyncError("gh repo view returned a non-object topic entry.")
        name = raw_topic.get("name")
        if not isinstance(name, str):
            raise TopicSyncError("gh repo view returned a topic name that wasn't a string.")
        topics.append(_validate_topic(name))
    return topics


def _apply_plan_entry(entry: dict[str, str], runner: Runner, *, repo: str | None) -> None:
    """Apply one reviewed non-noop entry to the explicit repository target."""
    action = entry["action"]
    if action == "noop":
        return
    if action not in {"add", "remove"}:
        raise TopicSyncError(f"Unsupported topic sync action: {action}")
    flag = "--add-topic" if action == "add" else "--remove-topic"
    command = ["gh", "repo", "edit"]
    if repo:
        command.append(repo)
    command.extend([flag, entry["topic"]])
    _run_gh(command, runner, failure=f"{action} topic {entry['topic']}")


def _run_gh(
    command: list[str],
    runner: Runner,
    *,
    failure: str,
) -> subprocess.CompletedProcess[str]:
    """Translate subprocess failure into an actionable command error."""
    try:
        completed = runner(command)
    except subprocess.TimeoutExpired as exc:
        raise TopicSyncError(_gh_recovery_message("GitHub CLI timed out")) from exc
    except OSError as exc:
        raise TopicSyncError(_gh_recovery_message("GitHub CLI is required")) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if not detail:
            detail = f"gh exited with status {completed.returncode}"
        raise TopicSyncError(f"Couldn't {failure}: {detail}. {_gh_recovery_message('Recovery')}")
    return completed


def _gh_recovery_message(prefix: str) -> str:
    """Describe safe authentication and preview steps before a retry."""
    return (
        f"{prefix} for live/apply topic sync. Install `gh`, run `gh auth status`, "
        "rerun with `--live` to inspect the GitHub delta, then rerun with "
        "`--apply` only when ready to mutate topics."
    )


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Bound the GitHub CLI process without invoking a shell."""
    return subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)


def _next_step_for_mode(
    *,
    apply: bool,
    live: bool,
    plan: list[dict[str, str]],
) -> str:
    """Report the next operator action for the chosen execution mode."""
    if apply:
        return "Applied non-noop topic changes with gh."
    if live:
        if any(entry["action"] != "noop" for entry in plan):
            return "Review the live topic plan, then rerun with --apply when ready."
        return "GitHub repository topics already match .github/topics.yml."
    return (
        "Run with --live to compare against the named repository, "
        "or rerun with --apply after reviewing the target and changes."
    )


def _validate_topic(value: str) -> str:
    """Validate GitHub topic syntax before constructing subprocess arguments."""
    topic = value.strip()
    if not TOPIC_PATTERN.fullmatch(topic):
        raise TopicSyncError(
            f"Topic {value!r} must be lowercase, start with a letter or digit, "
            "and contain only letters, digits, or hyphens."
        )
    return topic


def _validate_repository(value: str) -> str:
    """Reject ambiguous targets before any GitHub subprocess starts."""
    if value.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise TopicSyncError("Repository target must use OWNER/REPO without a URL or flags.")
    return value


def _render_text(result: dict[str, Any]) -> str:
    """Render the target, execution mode and planned changes for inspection."""
    lines = [
        "PolicyNIM GitHub topic sync",
        f"Mode: {result['mode']}",
        f"Repository: {result['repo']}",
        f"Topics file: {result['topics_file']}",
        "",
        "Plan:",
    ]
    for entry in result["plan"]:
        lines.append(f"- {entry['action']}: {entry['topic']}")
    lines.extend(["", f"Next step: {result['next_step']}"])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
