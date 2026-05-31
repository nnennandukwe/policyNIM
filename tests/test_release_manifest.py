"""Release manifest generator contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "release_manifest.py"


def test_release_manifest_writes_sorted_checksums_and_json(tmp_path: Path) -> None:
    """Generate deterministic release metadata from a directory of assets."""
    asset_dir = tmp_path / "release-assets"
    asset_dir.mkdir()
    (asset_dir / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (asset_dir / "policynim-v0.1.0-linux-amd64.tar.gz").write_bytes(b"linux-bundle")
    (asset_dir / "policynim-0.1.0-py3-none-any.whl").write_bytes(b"wheel")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(asset_dir),
            "--tag",
            "v0.1.0",
            "--source-sha",
            "abc123",
            "--expected",
            "install.sh",
            "--expected",
            "policynim-v0.1.0-linux-amd64.tar.gz",
            "--expected",
            "policynim-0.1.0-py3-none-any.whl",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads((asset_dir / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    checksum_lines = (asset_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines()

    assert manifest["schema_version"] == "1"
    assert manifest["release_tag"] == "v0.1.0"
    assert manifest["source_sha"] == "abc123"
    assert [asset["name"] for asset in manifest["assets"]] == [
        "install.sh",
        "policynim-0.1.0-py3-none-any.whl",
        "policynim-v0.1.0-linux-amd64.tar.gz",
    ]
    assert all("sha256" in asset for asset in manifest["assets"])
    assert all("size_bytes" in asset for asset in manifest["assets"])
    assert [line.split("  ", maxsplit=1)[1] for line in checksum_lines] == [
        "install.sh",
        "policynim-0.1.0-py3-none-any.whl",
        "policynim-v0.1.0-linux-amd64.tar.gz",
    ]
    assert "RELEASE_MANIFEST.json" not in "\n".join(checksum_lines)


def test_release_manifest_fails_when_expected_assets_are_missing(tmp_path: Path) -> None:
    """Hold a release when the public asset contract is incomplete."""
    asset_dir = tmp_path / "release-assets"
    asset_dir.mkdir()
    (asset_dir / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(asset_dir),
            "--expected",
            "install.sh",
            "--expected",
            "policynim-v0.1.0-linux-amd64.tar.gz",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "Missing expected release assets" in result.stderr
    assert "policynim-v0.1.0-linux-amd64.tar.gz" in result.stderr
    assert not (asset_dir / "RELEASE_MANIFEST.json").exists()
