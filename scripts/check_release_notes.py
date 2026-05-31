"""Verify that the current package version has public release notes."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal

Decision = Literal["passed", "failed"]

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format for the release notes check.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to inspect. Defaults to this script's repository.",
    )
    parser.add_argument(
        "--write-github-release-notes",
        type=Path,
        default=None,
        help="Write GitHub release notes for the current version from CHANGELOG.md.",
    )
    args = parser.parse_args()

    result = run_release_notes_check(repo_root=args.repo_root.resolve())
    if args.write_github_release_notes is not None and result["required_passed"]:
        release_notes = github_release_notes(repo_root=args.repo_root.resolve())
        args.write_github_release_notes.write_text(release_notes, encoding="utf-8")
        result["release_notes_file"] = str(args.write_github_release_notes)
        result["message"] = f"Wrote GitHub release notes: {args.write_github_release_notes}"
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(_render_text_summary(result))
    return 0 if result["required_passed"] else 1


def run_release_notes_check(*, repo_root: Path) -> dict[str, Any]:
    """Return a machine-readable release-notes gate for the current project version."""
    version = _read_project_version(repo_root)
    changelog_path = repo_root / "CHANGELOG.md"
    checks: list[dict[str, str]] = []

    try:
        changelog = changelog_path.read_text(encoding="utf-8")
    except OSError as exc:
        checks.append(
            _failed_check(
                name="changelog_exists",
                evidence=f"CHANGELOG.md could not be read: {type(exc).__name__}: {exc}",
                next_step="Create CHANGELOG.md before publishing a release.",
            )
        )
        return _summary(repo_root=repo_root, version=version, checks=checks)

    checks.append(
        _passed_check(
            name="changelog_exists",
            evidence="CHANGELOG.md is present at the repository root.",
        )
    )
    checks.append(
        _heading_check(
            name="unreleased_section",
            changelog=changelog,
            heading="Unreleased",
            evidence="CHANGELOG.md has an Unreleased section for pending changes.",
            next_step="Add a `## [Unreleased]` section before versioned release notes.",
        )
    )
    checks.append(
        _heading_check(
            name="current_version_section",
            changelog=changelog,
            heading=version,
            evidence=f"CHANGELOG.md has release notes for version {version}.",
            next_step=f"Add a `## [{version}]` section before publishing version {version}.",
        )
    )
    return _summary(repo_root=repo_root, version=version, checks=checks)


def github_release_notes(*, repo_root: Path) -> str:
    """Render GitHub release notes from the current CHANGELOG.md version section."""
    version = _read_project_version(repo_root)
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    version_notes = _extract_version_notes(changelog=changelog, version=version)
    return "\n".join(
        [
            f"# PolicyNIM {version}",
            "",
            version_notes,
            "",
            "## Release Artifacts",
            "",
            "- Python wheel and source distribution for package installation.",
            "- Standalone CLI bundles for Linux, Apple Silicon macOS, and Windows.",
            "- Unix and PowerShell installer scripts.",
            "- SHA256SUMS for release asset verification.",
            "- RELEASE_MANIFEST.json with asset sizes and checksums.",
            (
                "- GitHub artifact attestations generated from SHA256SUMS; "
                "verify downloads with "
                "`gh attestation verify <asset> -R <owner>/<repo>`."
            ),
            "",
        ]
    )


def _read_project_version(repo_root: Path) -> str:
    pyproject_path = repo_root / "pyproject.toml"
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"Could not read project version from {pyproject_path}: {exc}") from exc
    version = payload.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise SystemExit(f"{pyproject_path} must define [project].version.")
    return version.strip()


def _extract_version_notes(*, changelog: str, version: str) -> str:
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\](?:\s|$)(?P<body>.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md is missing notes for version {version}.")
    body = match.group("body").strip()
    if not body:
        raise ValueError(f"CHANGELOG.md version {version} section is empty.")
    return body


def _heading_check(
    *,
    name: str,
    changelog: str,
    heading: str,
    evidence: str,
    next_step: str,
) -> dict[str, str]:
    if _has_markdown_heading(changelog, heading):
        return _passed_check(name=name, evidence=evidence)
    return _failed_check(
        name=name,
        evidence=f"CHANGELOG.md is missing a `## [{heading}]` heading.",
        next_step=next_step,
    )


def _has_markdown_heading(markdown: str, heading: str) -> bool:
    escaped_heading = re.escape(heading)
    return re.search(rf"^## \[{escaped_heading}\](?:\s|$)", markdown, re.MULTILINE) is not None


def _passed_check(*, name: str, evidence: str) -> dict[str, str]:
    return {"name": name, "status": "passed", "evidence": evidence, "next_step": ""}


def _failed_check(*, name: str, evidence: str, next_step: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "failed",
        "evidence": evidence,
        "next_step": next_step,
    }


def _summary(*, repo_root: Path, version: str, checks: list[dict[str, str]]) -> dict[str, Any]:
    required_passed = all(check["status"] == "passed" for check in checks)
    decision: Decision = "passed" if required_passed else "failed"
    return {
        "schema_version": "1",
        "decision": decision,
        "required_passed": required_passed,
        "repo_root": str(repo_root),
        "version": version,
        "checks": checks,
    }


def _render_text_summary(result: dict[str, Any]) -> str:
    lines = [
        "PolicyNIM release notes check",
        f"Decision: {result['decision']}",
        f"Version: {result['version']}",
        f"Repository: {result['repo_root']}",
        "",
        "Checks:",
    ]
    for check in result["checks"]:
        lines.append(f"- {check['status']}: {check['name']}")
        lines.append(f"  Evidence: {check['evidence']}")
        if check["next_step"]:
            lines.append(f"  Next: {check['next_step']}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
