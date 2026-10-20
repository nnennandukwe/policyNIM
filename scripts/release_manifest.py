"""Bind release downloads to source, locked dependencies and observed artifact checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

PLATFORMS = {
    "linux-amd64": ("Linux", {"x86_64", "AMD64"}),
    "darwin-arm64": ("Darwin", {"arm64", "aarch64"}),
    "darwin-amd64": ("Darwin", {"x86_64", "AMD64"}),
    "windows-amd64": ("Windows", {"x86_64", "AMD64"}),
}
GENERATED = {"RELEASE_MANIFEST.json", "SHA256SUMS"}


def require(condition: bool, message: str) -> None:
    """Reject incomplete evidence even when assertions are disabled."""
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    """Hash large release archives without loading them into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_receipt(
    receipt: dict, *, revision: str, lock: str, generation: str, catalog: str
) -> None:
    """Require matching completed source and generation evidence for both protocols."""
    require(isinstance(receipt, dict), "Artifact receipt must be a JSON object.")
    require(receipt.get("status") == "passed", "Artifact verification didn't pass.")
    require(receipt.get("source_revision") == revision, "Artifact source revision differs.")
    recorded_lock = receipt.get("dependency_lock")
    if isinstance(recorded_lock, dict):
        recorded_lock = recorded_lock.get("sha256")
    require(recorded_lock == lock, "Artifact dependency lock differs.")
    require(receipt.get("generation_id") == generation, "Artifact generation differs.")
    require(receipt.get("catalog_digest") == catalog, "Artifact catalog differs.")
    for transport in ("stdio", "http"):
        results = receipt.get(transport)
        require(isinstance(results, list) and len(results) == 2, "Protocol evidence is incomplete.")
        require(all(isinstance(result, dict) for result in results), "Invalid protocol evidence.")
        require(
            {result.get("protocol") for result in results} == {"2026-07-28", "2025-11-25"},
            "Required protocol versions differ.",
        )
        require(
            all(result.get("invalid_argument_rejected") is True for result in results),
            "Argument validation evidence is incomplete.",
        )


def generate_manifest(*, asset_dir: Path, repo_root: Path, tag: str, revision: str) -> dict:
    """Validate every required artifact before writing the public manifest and checksums."""
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    require(tag == "v" + version, "Release tag differs from the project version.")
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "Invalid source revision.")
    lock = "sha256:" + sha256(repo_root / "uv.lock")
    assets: dict[str, Path] = {}
    for path in asset_dir.iterdir():
        require(not path.is_symlink() and path.is_file(), "Release assets must be regular files.")
        require(re.fullmatch(r"[A-Za-z0-9_.+-]+", path.name) is not None, "Unsafe asset filename.")
        if path.name not in GENERATED:
            assets[path.name] = path
    wheel = f"policynim-{version}-py3-none-any.whl"
    required = {
        wheel,
        f"policynim-{version}.tar.gz",
        "install.sh",
        "install.ps1",
        "policynim-public-generation.zip",
        "policynim-public-generation.sha256",
        "wheel-evidence.json",
    }
    for target in PLATFORMS:
        suffix = "zip" if target == "windows-amd64" else "tar.gz"
        required.update(
            {f"policynim-{tag}-{target}.{suffix}", f"standalone-evidence-{target}.json"}
        )
    require(
        required <= assets.keys(),
        "Missing release assets: " + ", ".join(sorted(required - assets.keys())),
    )
    require(assets.keys() <= required, "Unexpected release assets aren't accepted.")
    bundle = assets["policynim-public-generation.zip"]
    bundle_digest = assets["policynim-public-generation.sha256"].read_text(encoding="utf-8").strip()
    require(bundle_digest == "sha256:" + sha256(bundle), "Public bundle checksum differs.")
    with zipfile.ZipFile(bundle) as archive:
        require(archive.namelist().count("manifest.json") == 1, "Ambiguous generation manifest.")
        with archive.open("manifest.json") as stream:
            manifest_bytes = stream.read(1024 * 1024 + 1)
    require(len(manifest_bytes) <= 1024 * 1024, "Generation manifest exceeds its size limit.")
    generation = hashlib.sha256(manifest_bytes).hexdigest()
    catalog = json.loads(manifest_bytes)["catalog_digest"]
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", catalog) is not None, "Invalid catalog digest.")
    for target, (system, machines) in PLATFORMS.items():
        receipt = json.loads(
            assets[f"standalone-evidence-{target}.json"].read_text(encoding="utf-8")
        )
        check_receipt(receipt, revision=revision, lock=lock, generation=generation, catalog=catalog)
        require(receipt.get("platform") == system, "Standalone operating system differs.")
        require(
            receipt.get("runner_machine") in machines, "Standalone runner architecture differs."
        )
        require(receipt.get("version_output") == version, "Standalone package version differs.")
        suffix = "zip" if target == "windows-amd64" else "tar.gz"
        name = f"policynim-{tag}-{target}.{suffix}"
        require(
            receipt.get("archive") == {"name": name, "sha256": sha256(assets[name])},
            "Standalone archive differs from its receipt.",
        )
    receipt = json.loads(assets["wheel-evidence.json"].read_text(encoding="utf-8"))
    check_receipt(receipt, revision=revision, lock=lock, generation=generation, catalog=catalog)
    require(isinstance(receipt.get("wheel"), dict), "Wheel receipt metadata is missing.")
    require(receipt["wheel"].get("package_version") == version, "Wheel package version differs.")
    require(
        receipt["wheel"].get("sha256") == sha256(assets[wheel]),
        "Wheel differs from its receipt.",
    )
    entries = [
        {"name": name, "size_bytes": path.stat().st_size, "sha256": sha256(path)}
        for name, path in sorted(assets.items())
    ]
    result = {
        "schema_version": "1",
        "release_tag": tag,
        "source_sha": revision,
        "dependency_lock": lock,
        "generation_id": generation,
        "catalog_digest": catalog,
        "public_bundle_digest": bundle_digest,
        "scope": ("artifact verification; production and coding-client acceptance stay separate"),
        "assets": entries,
    }
    output = asset_dir / "RELEASE_MANIFEST.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    sums = "".join(f"{entry['sha256']}  {entry['name']}\n" for entry in entries)
    sums += f"{sha256(output)}  RELEASE_MANIFEST.json\n"
    (asset_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return result


def main() -> int:
    """Require the checked-out source revision and return nonzero for incomplete evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset_dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        observed = subprocess.check_output(
            ["git", "-C", str(args.repo_root), "rev-parse", "HEAD"], text=True
        ).strip()
        require(
            observed == args.source_sha, "Checked-out source differs from the requested revision."
        )
        result = generate_manifest(
            asset_dir=args.asset_dir,
            repo_root=args.repo_root,
            tag=args.tag,
            revision=args.source_sha,
        )
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        zipfile.BadZipFile,
        subprocess.CalledProcessError,
    ) as error:
        print(f"Release evidence rejected: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "passed",
                "source_sha": result["source_sha"],
                "asset_count": len(result["assets"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
