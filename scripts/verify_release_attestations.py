"""Verify every checksummed release subject against the publishing workflow and source."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path


def verify_subjects(
    asset_dir: Path, repository: str, revision: str, *, timeout: float = 45
) -> dict:
    """Retain partial evidence and stop at the first unverified subject."""
    report: dict = {
        "status": "failed",
        "repository": repository,
        "source_revision": revision,
        "signer_workflow": repository + "/.github/workflows/release.yml",
        "subjects": [],
    }
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        report["failure"] = "invalid_repository"
        return report
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        report["failure"] = "invalid_source_revision"
        return report
    try:
        checksum_file = asset_dir / "SHA256SUMS"
        if checksum_file.is_symlink() or checksum_file.stat().st_size > 1024 * 1024:
            raise ValueError("Invalid checksum file.")
        lines = checksum_file.read_text(encoding="ascii").splitlines()
        if not lines or len(lines) > 1024:
            raise ValueError("Invalid subject count.")
        subjects: list[tuple[str, str, Path]] = []
        names: set[str] = set()
        for line in lines:
            match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.+-]+)", line)
            if match is None:
                raise ValueError("Invalid subject entry.")
            digest, name = match.groups()
            if name in {".", "..", "SHA256SUMS"} or name in names:
                raise ValueError("Duplicate or invalid subject.")
            names.add(name)
            path = asset_dir / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Subject isn't a regular file.")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != digest:
                raise ValueError("Subject checksum differs.")
            subjects.append((name, digest, path))
        if "RELEASE_MANIFEST.json" not in names:
            raise ValueError("Release manifest isn't a subject.")
        manifest_path = asset_dir / "RELEASE_MANIFEST.json"
        if manifest_path.stat().st_size > 1024 * 1024:
            raise ValueError("Release manifest is too large.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {entry["name"] for entry in manifest["assets"]}
        if manifest["source_sha"] != revision or names != expected | {"RELEASE_MANIFEST.json"}:
            raise ValueError("Release manifest subjects differ.")
        if {path.name for path in asset_dir.iterdir()} != names | {"SHA256SUMS"}:
            raise ValueError("Unverified release asset is present.")
        for name, digest, path in subjects:
            result: dict = {"name": name, "sha256": digest, "verified": False}
            report["subjects"].append(result)
            for attempt in range(1, 4):
                result["attempts"] = attempt
                try:
                    process = subprocess.run(
                        [
                            "gh",
                            "attestation",
                            "verify",
                            str(path),
                            "--repo",
                            repository,
                            "--signer-workflow",
                            report["signer_workflow"],
                            "--source-digest",
                            revision,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=timeout,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    report["failure"] = "verification_timeout"
                    return report
                if process.returncode == 0:
                    break
                if attempt == 3:
                    report["failure"] = "attestation_rejected"
                    return report
                time.sleep(2)
            result["verified"] = True
    except (ValueError, OSError, UnicodeError, TypeError, KeyError):
        report["failure"] = "invalid_subjects_or_verifier_unavailable"
        return report
    report["status"] = "passed"
    return report


def main() -> int:
    """Write an explicit failed receipt when verification can't complete."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset_dir", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify_subjects(args.asset_dir.resolve(), args.repository, args.revision)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
