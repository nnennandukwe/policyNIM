"""Reject stale or incomplete artifact evidence before release publication."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release_manifest.py"
spec = importlib.util.spec_from_file_location("release_manifest", SCRIPT)
assert spec is not None and spec.loader is not None
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


@pytest.fixture
def release_assets(tmp_path: Path) -> tuple[Path, Path]:
    """Build synthetic metadata joins; these fixtures aren't actual artifact acceptance."""
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (repo / "uv.lock").write_text("version = 1\n")
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in [
        "policynim-0.2.0-py3-none-any.whl",
        "policynim-0.2.0.tar.gz",
        "install.sh",
        "install.ps1",
    ]:
        (assets / name).write_bytes(name.encode())
    catalog = "sha256:" + "1" * 64
    source_manifest = json.dumps({"catalog_digest": catalog}).encode()
    bundle = assets / "policynim-public-generation.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", source_manifest)
    (assets / "policynim-public-generation.sha256").write_text("sha256:" + manifest.sha256(bundle))
    common = {
        "status": "passed",
        "source_revision": "a" * 40,
        "dependency_lock": "sha256:" + manifest.sha256(repo / "uv.lock"),
        "generation_id": hashlib.sha256(source_manifest).hexdigest(),
        "catalog_digest": catalog,
        "public_bundle_digest": "sha256:" + manifest.sha256(bundle),
        "stdio": [
            {"protocol": protocol, "invalid_argument_rejected": True}
            for protocol in ["2026-07-28", "2025-11-25"]
        ],
        "http": [
            {"protocol": protocol, "invalid_argument_rejected": True}
            for protocol in ["2026-07-28", "2025-11-25"]
        ],
    }
    for target, (system, machines) in manifest.PLATFORMS.items():
        suffix = "zip" if target == "windows-amd64" else "tar.gz"
        name = f"policynim-v0.2.0-{target}.{suffix}"
        (assets / name).write_bytes(target.encode())
        receipt = {
            **common,
            "platform": system,
            "runner_machine": sorted(machines)[0],
            "version_output": "0.2.0",
            "artifact": {
                "digest_format": "json-records-v1",
                "tree_sha256": "2" * 64,
                "file_count": 1,
            },
            "archive": {"name": name, "sha256": manifest.sha256(assets / name)},
        }
        (assets / f"standalone-evidence-{target}.json").write_text(json.dumps(receipt))
    receipt = {
        **common,
        "dependency_lock": {"sha256": common["dependency_lock"]},
        "wheel": {
            "sha256": manifest.sha256(assets / "policynim-0.2.0-py3-none-any.whl"),
            "package_version": "0.2.0",
        },
    }
    for runtime in ["3.11", "3.12"]:
        (assets / f"wheel-evidence-{runtime}.json").write_text(
            json.dumps({**receipt, "python": runtime + ".0"})
        )
    return repo, assets


def test_manifest_binds_download_bytes_and_its_own_checksum(release_assets) -> None:
    """Checksums identify every download plus the generated source-bound manifest."""
    repo, assets = release_assets
    result = manifest.generate_manifest(
        asset_dir=assets, repo_root=repo, tag="v0.2.0", revision="a" * 40
    )
    assert result["source_sha"] == "a" * 40
    sums = (assets / "SHA256SUMS").read_text().splitlines()
    assert len(sums) == len(result["assets"]) + 1
    for line in sums:
        digest, name = line.split("  ", 1)
        assert digest == manifest.sha256(assets / name)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "failed"),
        ("source_revision", "b" * 40),
        ("dependency_lock", "sha256:" + "0" * 64),
        ("generation_id", "0" * 64),
        ("catalog_digest", "sha256:" + "0" * 64),
        ("public_bundle_digest", "sha256:" + "0" * 64),
        ("version_output", "0.1.0"),
        ("runner_machine", "wrong-architecture"),
        ("http", [{"protocol": "2026-07-28", "invalid_argument_rejected": True}]),
        ("stdio", [None, None]),
    ],
)
def test_mismatched_receipts_cant_authorize_publication(release_assets, field, value) -> None:
    """One stale platform or incomplete protocol result must block the entire manifest."""
    repo, assets = release_assets
    path = assets / "standalone-evidence-linux-amd64.json"
    receipt = json.loads(path.read_text())
    receipt[field] = value
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        manifest.generate_manifest(
            asset_dir=assets, repo_root=repo, tag="v0.2.0", revision="a" * 40
        )
    assert not (assets / "RELEASE_MANIFEST.json").exists()


@pytest.mark.parametrize(
    "change",
    [
        "archive",
        "wheel",
        "missing-platform",
        "unexpected-file",
        "wrong-tag",
        "missing-runtime",
        "wrong-runtime",
    ],
)
def test_replaced_or_missing_downloads_cant_reuse_passing_evidence(release_assets, change) -> None:
    """Passing receipt metadata can't authorize substituted bytes or omitted platforms."""
    repo, assets = release_assets
    if change == "archive":
        (assets / "policynim-v0.2.0-linux-amd64.tar.gz").write_bytes(b"substitution")
    elif change == "wheel":
        (assets / "policynim-0.2.0-py3-none-any.whl").write_bytes(b"substitution")
    elif change == "missing-platform":
        (assets / "standalone-evidence-windows-amd64.json").unlink()
    elif change == "missing-runtime":
        (assets / "wheel-evidence-3.12.json").unlink()
    elif change == "wrong-runtime":
        path = assets / "wheel-evidence-3.12.json"
        receipt = json.loads(path.read_text())
        receipt["python"] = "3.11.99"
        path.write_text(json.dumps(receipt))
    elif change == "unexpected-file":
        (assets / "unreviewed.log").write_text("unreviewed")
    with pytest.raises(ValueError):
        manifest.generate_manifest(
            asset_dir=assets,
            repo_root=repo,
            tag="v0.1.0" if change == "wrong-tag" else "v0.2.0",
            revision="a" * 40,
        )
    assert not (assets / "RELEASE_MANIFEST.json").exists()


def test_cli_rejects_a_revision_that_isnt_checked_out(release_assets) -> None:
    """A supplied version label can't replace the actual source checkout identity."""
    import subprocess
    import sys

    _, assets = release_assets
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(assets), "--tag", "v0.2.0", "--source-sha", "0" * 40],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Checked-out source differs" in result.stderr
    assert not (assets / "RELEASE_MANIFEST.json").exists()
