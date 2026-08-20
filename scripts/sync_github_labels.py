"""Dry-run or apply the PolicyNIM GitHub label taxonomy with gh."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LABELS_FILE = REPO_ROOT / ".github" / "labels.yml"
GITHUB_LABEL_LIST_COMMAND = "gh label list --json name,color,description --limit 1000"

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
Action = Literal["create", "update", "noop"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels-file",
        type=Path,
        default=DEFAULT_LABELS_FILE,
        help="Path to the repository label taxonomy.",
    )
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
        raise LabelSyncError(f"Could not read label taxonomy {path}: {exc}") from exc

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
    labels_file: Path | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Build or apply a GitHub label sync plan."""
    if apply and live:
        raise LabelSyncError("--apply and --live cannot be used together.")
    command_runner = runner or _run_command
    existing = _load_existing_labels(command_runner) if apply or live else []
    plan = plan_label_sync(desired, existing)
    if apply:
        for entry in plan:
            _apply_plan_entry(entry, command_runner)
    mode = "apply" if apply else "live-dry-run" if live else "dry-run"
    return {
        "schema_version": "1",
        "mode": mode,
        "labels_file": str(labels_file) if labels_file is not None else None,
        "plan": plan,
        "next_step": _next_step_for_mode(apply=apply, live=live, plan=plan),
    }


def _load_existing_labels(runner: Runner) -> list[dict[str, str]]:
    command = ["gh", "label", "list", "--json", "name,color,description", "--limit", "1000"]
    completed = _run_gh(command, runner, failure="list existing GitHub labels")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LabelSyncError(f"Could not parse gh label list JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise LabelSyncError("gh label list returned JSON that was not a list.")
    labels: list[dict[str, str]] = []
    for raw_label in payload:
        if not isinstance(raw_label, dict):
            raise LabelSyncError("gh label list returned a non-object label entry.")
        labels.append(
            {
                "name": str(raw_label.get("name", "")),
                "color": str(raw_label.get("color", "")),
                "description": str(raw_label.get("description", "")),
            }
        )
    return labels


def _apply_plan_entry(entry: dict[str, Any], runner: Runner) -> None:
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

    _run_gh(command, runner, failure=f"{action} label {entry['name']}")


def _run_gh(
    command: list[str],
    runner: Runner,
    *,
    failure: str,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(command)
    except OSError as exc:
        raise LabelSyncError(_gh_recovery_message("GitHub CLI is required")) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if not detail:
            detail = f"gh exited with status {completed.returncode}"
        raise LabelSyncError(f"Could not {failure}: {detail}. {_gh_recovery_message('Recovery')}")
    return completed


def _gh_recovery_message(prefix: str) -> str:
    return (
        f"{prefix} for live/apply label sync. Install `gh`, run `gh auth status`, "
        "rerun with `--live` to inspect the GitHub delta, then rerun with "
        "`--apply` only when ready to mutate labels."
    )


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _next_step_for_mode(
    *,
    apply: bool,
    live: bool,
    plan: list[dict[str, Any]],
) -> str:
    if apply:
        return "Applied non-noop label changes with gh."
    if live:
        if any(entry["action"] != "noop" for entry in plan):
            return "Review the live label plan, then rerun with --apply when ready."
        return "GitHub labels already match .github/labels.yml."
    return (
        "Run with --live to compare against current GitHub state "
        f"(`{GITHUB_LABEL_LIST_COMMAND}`), or rerun with --apply when ready."
    )


def _validate_label(label: dict[str, str]) -> dict[str, str]:
    required = ("name", "color", "description")
    missing = [key for key in required if not label.get(key)]
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
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _render_text(result: dict[str, Any]) -> str:
    lines = [
        "PolicyNIM GitHub label sync",
        f"Mode: {result['mode']}",
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
