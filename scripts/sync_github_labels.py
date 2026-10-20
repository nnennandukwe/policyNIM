"""Dry-run or apply the PolicyNIM GitHub label taxonomy with gh."""

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
DEFAULT_LABELS_FILE = REPO_ROOT / ".github" / "labels.yml"

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
Action = Literal["create", "update", "noop"]


def main() -> int:
    """Parse flags and report a local preview, live preview or explicit apply result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels-file",
        type=Path,
        default=DEFAULT_LABELS_FILE,
        help="Path to the repository label taxonomy.",
    )
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY, help="GitHub OWNER/REPO target.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Apply create/update operations with gh. Defaults to dry-run.",
    )
    mode_group.add_argument(
        "--live",
        action="store_true",
        help=(
            "Compare against current GitHub labels without applying changes. "
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
        desired = load_label_taxonomy(args.labels_file)
        result = sync_labels(
            desired=desired,
            repo=args.repo,
            apply=args.apply,
            live=args.live,
            labels_file=args.labels_file.resolve(strict=False),
        )
    except LabelSyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(_render_text(result))
    return 0


class LabelSyncError(Exception):
    """GitHub label sync failed."""


def load_label_taxonomy(path: Path) -> list[dict[str, str]]:
    """Load the limited YAML label taxonomy used by this repository."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LabelSyncError(f"Couldn't read label taxonomy {path}: {exc}") from exc

    labels: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- name:"):
            if current is not None:
                labels.append(_validate_label(current))
            current = {"name": _unquote(stripped.removeprefix("- name:").strip())}
            continue
        if current is None:
            raise LabelSyncError(f"Unexpected label taxonomy line before a name: {raw_line}")
        key, separator, raw_value = stripped.partition(":")
        if not separator:
            raise LabelSyncError(f"Invalid label taxonomy line: {raw_line}")
        if key not in {"color", "description"}:
            raise LabelSyncError(f"Unsupported label field {key!r} in {raw_line!r}.")
        current[key] = _unquote(raw_value.strip())
    if current is not None:
        labels.append(_validate_label(current))
    if not labels:
        raise LabelSyncError(f"No labels found in {path}.")
    return labels


def plan_label_sync(
    desired: list[dict[str, str]],
    existing: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Return deterministic create/update/noop actions for desired labels."""
    existing_by_name = {label["name"]: label for label in existing}
    plan: list[dict[str, Any]] = []
    for label in desired:
        current = existing_by_name.get(label["name"])
        if current is None:
            plan.append({"action": "create", **label})
            continue
        updates: dict[str, dict[str, str]] = {}
        for key in ("color", "description"):
            if current.get(key, "") != label[key]:
                updates[key] = {"from": current.get(key, ""), "to": label[key]}
        if updates:
            plan.append({"action": "update", **label, "updates": updates})
        else:
            plan.append({"action": "noop", **label})
    return plan


def sync_labels(
    *,
    desired: list[dict[str, str]],
    apply: bool,
    live: bool = False,
    repo: str | None = None,
    labels_file: Path | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Build or apply a GitHub label sync plan."""
    if apply and live:
        raise LabelSyncError("--apply and --live cannot be used together.")
    repo = _validate_repository(repo or DEFAULT_REPOSITORY)
    command_runner = runner or _run_command
    existing = _load_existing_labels(command_runner, repo=repo) if apply or live else []
    plan = plan_label_sync(desired, existing)
    if apply:
        for entry in plan:
            _apply_plan_entry(entry, command_runner, repo=repo)
    mode = "apply" if apply else "live-dry-run" if live else "dry-run"
    return {
        "schema_version": "1",
        "mode": mode,
        "repo": repo,
        "labels_file": str(labels_file) if labels_file is not None else None,
        "plan": plan,
        "next_step": _next_step_for_mode(apply=apply, live=live, plan=plan),
    }


def _load_existing_labels(runner: Runner, *, repo: str) -> list[dict[str, str]]:
    """Validate the GitHub label response before planning any writes."""
    command = [
        "gh",
        "label",
        "list",
        "--repo",
        repo,
        "--json",
        "name,color,description",
        "--limit",
        "1000",
    ]
    completed = _run_gh(command, runner, failure="list existing GitHub labels")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LabelSyncError(f"Couldn't parse gh label list JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise LabelSyncError("gh label list returned JSON that wasn't a list.")
    labels: list[dict[str, str]] = []
    for raw_label in payload:
        if not isinstance(raw_label, dict):
            raise LabelSyncError("gh label list returned a non-object label entry.")
        name, color, description = (
            raw_label.get("name"),
            raw_label.get("color"),
            raw_label.get("description"),
        )
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(color, str)
            or not re.fullmatch(r"[0-9a-fA-F]{6}", color)
            or (description is not None and not isinstance(description, str))
        ):
            raise LabelSyncError("gh label list returned invalid string fields.")
        labels.append({"name": name, "color": color.lower(), "description": description or ""})
    return labels


def _apply_plan_entry(entry: dict[str, Any], runner: Runner, *, repo: str) -> None:
    """Apply one reviewed non-noop entry to the explicit repository target."""
    action = entry["action"]
    if action == "noop":
        return
    if action == "create":
        command = [
            "gh",
            "label",
            "create",
            str(entry["name"]),
            "--color",
            str(entry["color"]),
            "--description",
            str(entry["description"]),
        ]
    elif action == "update":
        command = [
            "gh",
            "label",
            "edit",
            str(entry["name"]),
            "--color",
            str(entry["color"]),
            "--description",
            str(entry["description"]),
        ]
    else:
        raise LabelSyncError(f"Unsupported label sync action: {action}")

    command.extend(["--repo", repo])
    _run_gh(command, runner, failure=f"{action} label {entry['name']}")


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
        raise LabelSyncError(_gh_recovery_message("GitHub CLI timed out")) from exc
    except OSError as exc:
        raise LabelSyncError(_gh_recovery_message("GitHub CLI is required")) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if not detail:
            detail = f"gh exited with status {completed.returncode}"
        raise LabelSyncError(f"Couldn't {failure}: {detail}. {_gh_recovery_message('Recovery')}")
    return completed


def _gh_recovery_message(prefix: str) -> str:
    """Describe safe authentication and preview steps before a retry."""
    return (
        f"{prefix} for live/apply label sync. Install `gh`, run `gh auth status`, "
        "rerun with `--live` to inspect the GitHub delta, then rerun with "
        "`--apply` only when ready to mutate labels."
    )


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Bound the GitHub CLI process without invoking a shell."""
    return subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)


def _next_step_for_mode(
    *,
    apply: bool,
    live: bool,
    plan: list[dict[str, Any]],
) -> str:
    """Report the next operator action for the chosen execution mode."""
    if apply:
        return "Applied non-noop label changes with gh."
    if live:
        if any(entry["action"] != "noop" for entry in plan):
            return "Review the live label plan, then rerun with --apply when ready."
        return "GitHub labels already match .github/labels.yml."
    return (
        "Run with --live to compare against the named repository, "
        "or rerun with --apply after reviewing the target and changes."
    )


def _validate_label(label: dict[str, str]) -> dict[str, str]:
    """Require complete desired label metadata and a valid color."""
    required = ("name", "color", "description")
    missing = [key for key in required if not label.get(key, "").strip()]
    if missing:
        raise LabelSyncError(
            f"Label entry {label.get('name') or '<unknown>'} is missing: {', '.join(missing)}"
        )
    color = label["color"].strip().removeprefix("#")
    if len(color) != 6 or any(character not in "0123456789abcdefABCDEF" for character in color):
        raise LabelSyncError(f"Label {label['name']} has invalid hex color: {label['color']}")
    return {
        "name": label["name"].strip(),
        "color": color.lower(),
        "description": label["description"].strip(),
    }


def _unquote(value: str) -> str:
    """Remove paired quotes from the supported flat taxonomy format."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _validate_repository(value: str) -> str:
    """Reject ambiguous targets before any GitHub subprocess starts."""
    if value.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise LabelSyncError("Repository target must use OWNER/REPO without a URL or flags.")
    return value


def _render_text(result: dict[str, Any]) -> str:
    """Render the target, execution mode and planned changes for inspection."""
    lines = [
        "PolicyNIM GitHub label sync",
        f"Mode: {result['mode']}",
        f"Repository: {result['repo']}",
        f"Labels file: {result['labels_file']}",
        "",
        "Plan:",
    ]
    for entry in result["plan"]:
        lines.append(f"- {entry['action']}: {entry['name']}")
    lines.extend(["", f"Next step: {result['next_step']}"])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
