"""Refuse publication when an existing release tag differs from the artifact source."""

from __future__ import annotations

import argparse
import json
import re
import subprocess


def verify_tag(tag: str, revision: str, *, remote: str = "origin") -> bool:
    """Resolve lightweight and annotated remote tags without creating or moving either."""
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.+-]*)", tag):
        return False
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return False
    reference = "refs/tags/" + tag
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--", remote, reference, reference + "^{}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 2 and not result.stdout:
            return True
        if result.returncode != 0:
            return False
        resolved: dict[str, str] = {}
        for line in result.stdout.splitlines():
            digest, name = line.split("\t")
            if name not in {reference, reference + "^{}"} or name in resolved:
                return False
            if not re.fullmatch(r"[0-9a-f]{40}", digest):
                return False
            resolved[name] = digest
        return (
            reference in resolved
            and resolved.get(reference + "^{}", resolved[reference]) == revision
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def main() -> int:
    """Report a bounded publication gate without exposing remote command diagnostics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()
    passed = verify_tag(args.tag, args.revision, remote=args.remote)
    print(json.dumps({"status": "passed" if passed else "failed", "gate": "release_tag_source"}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
