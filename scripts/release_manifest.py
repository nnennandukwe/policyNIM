"""Generate release checksums and a machine-readable PolicyNIM asset manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

GENERATED_FILENAMES = {"SHA256SUMS", "RELEASE_MANIFEST.json"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset_dir", type=Path, help="Directory containing release assets.")
    parser.add_argument("--tag", default="", help="Release tag, for example v0.1.0.")
    parser.add_argument("--source-sha", default="", help="Source commit SHA for the release.")
    parser.add_argument(
        "--expected",
        action="append",
        default=[],
        help="Expected asset filename. Repeat for every required public asset.",
    )
    args = parser.parse_args()

    try:
        generate_release_manifest(
            asset_dir=args.asset_dir,
            release_tag=args.tag,
            source_sha=args.source_sha,
            expected_assets=args.expected,
        )
    except ReleaseManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


class ReleaseManifestError(Exception):
    """Release manifest generation failed."""


def generate_release_manifest(
    *,
    asset_dir: Path,
    release_tag: str,
    source_sha: str,
    expected_assets: list[str],
) -> None:
    if not asset_dir.is_dir():
        raise ReleaseManifestError(f"Release asset directory does not exist: {asset_dir}")

    assets = [
        path
        for path in sorted(asset_dir.iterdir(), key=lambda candidate: candidate.name)
        if path.is_file() and path.name not in GENERATED_FILENAMES
    ]
    asset_names = {path.name for path in assets}
    missing = sorted(set(expected_assets) - asset_names)
    if missing:
        joined = ", ".join(missing)
        raise ReleaseManifestError(f"Missing expected release assets: {joined}")

    entries = [_asset_entry(path) for path in assets]
    checksums = "".join(f"{entry['sha256']}  {entry['name']}\n" for entry in entries)
    manifest = {
        "schema_version": "1",
        "release_tag": release_tag or None,
        "source_sha": source_sha or None,
        "assets": entries,
    }

    (asset_dir / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    (asset_dir / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote release manifest for {len(entries)} assets in {asset_dir}.")


def _asset_entry(path: Path) -> dict[str, object]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": digest,
    }


if __name__ == "__main__":
    raise SystemExit(main())
