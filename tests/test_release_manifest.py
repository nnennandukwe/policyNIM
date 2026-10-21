"""Reject stale or incomplete artifact evidence before release publication."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
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
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'policynim'\nversion = '0.2.0'\n"
        "requires-python = '>=3.11,<3.13'\n"
        "dependencies = ['requests>=2; python_version < \"3.13\"']\n"
        "[project.optional-dependencies]\n"
        "feature = ['extra-lib>=1; sys_platform == \"win32\"']\n"
    )
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
    package_metadata = (
        b"Metadata-Version: 2.4\nName: policynim\nVersion: 0.2.0\n"
        b"Requires-Python: <3.13,>=3.11\nProvides-Extra: feature\n"
        b'Requires-Dist: requests>=2; python_version < "3.13"\n'
        b'Requires-Dist: extra-lib>=1; sys_platform == "win32" and extra == "feature"\n'
    )
    with zipfile.ZipFile(assets / "policynim-0.2.0-py3-none-any.whl", "w") as archive:
        archive.writestr("policynim-0.2.0.dist-info/METADATA", package_metadata)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "pyproject.toml", "uv.lock"], check=True)
    with tarfile.open(assets / "policynim-0.2.0.tar.gz", "w:gz") as archive:
        for name in ["pyproject.toml", "uv.lock"]:
            archive.add(repo / name, arcname="policynim-0.2.0/" + name)
        info = tarfile.TarInfo("policynim-0.2.0/PKG-INFO")
        payload = package_metadata
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    catalog = "sha256:" + "1" * 64
    source_manifest = json.dumps({"catalog_digest": catalog}).encode()
    bundle = assets / "policynim-public-generation.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", source_manifest)
    (assets / "policynim-public-generation.sha256").write_text("sha256:" + manifest.sha256(bundle))
    common = {
        "status": "passed",
        "synthetic_offline_cases": {"case_count": 6, "overall_pass_rate": 1.0},
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
            "archive_verified": True,
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


@pytest.mark.parametrize(
    "fault", ["arbitrary-bytes", "missing-source", "changed-source", "private-state"]
)
def test_source_archive_must_match_release_checkout(release_assets, fault: str) -> None:
    """An unverified source download can't inherit passing binary evidence."""
    repo, assets = release_assets
    path = assets / "policynim-0.2.0.tar.gz"
    if fault == "arbitrary-bytes":
        path.write_bytes(b"unverified source")
    else:
        with tarfile.open(path, "r:gz") as original:
            content = original.extractfile("policynim-0.2.0/PKG-INFO")
            assert content is not None
            package_metadata = content.read()
        with tarfile.open(path, "w:gz") as archive:
            files = {
                "pyproject.toml": (repo / "pyproject.toml").read_bytes(),
                "uv.lock": (repo / "uv.lock").read_bytes(),
                "PKG-INFO": package_metadata,
            }
            if fault == "missing-source":
                del files["uv.lock"]
            elif fault == "changed-source":
                files["uv.lock"] = b"different lock"
            else:
                files[".git"] = b"private checkout pointer"
            for name, payload in files.items():
                info = tarfile.TarInfo("policynim-0.2.0/" + name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    expected_message = {
        "missing-source": "Source archive omits package inputs",
        "changed-source": "Source archive bytes differ",
        "private-state": "Unexpected source archive file",
    }.get(fault)
    with pytest.raises((ValueError, tarfile.TarError), match=expected_message):
        manifest.generate_manifest(
            asset_dir=assets, repo_root=repo, tag="v0.2.0", revision="a" * 40
        )
    assert not (assets / "RELEASE_MANIFEST.json").exists()


@pytest.mark.parametrize("distribution", ["wheel", "sdist"])
@pytest.mark.parametrize("fault", ["dependency", "python", "extra", "marker"])
def test_manifest_cli_rejects_changed_dependency_metadata(release_assets, distribution, fault):
    """Actual publication rejects altered package contracts and permits a corrected retry."""
    repo, assets = release_assets
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=release-test",
            "-c",
            "user.email=release-test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    for receipt_path in assets.glob("*-evidence-*.json"):
        receipt = json.loads(receipt_path.read_text())
        receipt["source_revision"] = revision
        receipt_path.write_text(json.dumps(receipt))
    wheel = assets / "policynim-0.2.0-py3-none-any.whl"
    target = wheel if distribution == "wheel" else assets / "policynim-0.2.0.tar.gz"
    original = target.read_bytes()
    replacements = {
        "dependency": (b"requests>=2", b"requests>=999"),
        "python": (b"Requires-Python: <3.13,>=3.11", b"Requires-Python: >=3.10"),
        "extra": (b'extra == "feature"', b'extra == "other"'),
        "marker": (b'python_version < "3.13"', b'sys_platform == "linux"'),
    }
    before, after = replacements[fault]
    if distribution == "wheel":
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
        with zipfile.ZipFile(target, "w") as archive:
            for entry, content in entries:
                archive.writestr(entry, content.replace(before, after))
    else:
        with tarfile.open(fileobj=io.BytesIO(original), mode="r:gz") as archive:
            entries = []
            for entry in archive:
                content = archive.extractfile(entry)
                assert content is not None
                entries.append((entry, content.read()))
        with tarfile.open(target, "w:gz") as archive:
            for entry, content in entries:
                if entry.name.endswith("/PKG-INFO"):
                    content = content.replace(before, after)
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))

    def invoke():
        """Join real on-disk receipts through the public manifest command."""
        for receipt_path in assets.glob("wheel-evidence-*.json"):
            receipt = json.loads(receipt_path.read_text())
            receipt["wheel"]["sha256"] = manifest.sha256(wheel)
            receipt_path.write_text(json.dumps(receipt))
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(assets),
                "--repo-root",
                str(repo),
                "--tag",
                "v0.2.0",
                "--source-sha",
                revision,
            ],
            text=True,
            capture_output=True,
        )

    rejected = invoke()
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    assert "metadata" in rejected.stderr.lower()
    assert not (assets / "RELEASE_MANIFEST.json").exists()
    target.write_bytes(original)
    recovered = invoke()
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert (assets / "RELEASE_MANIFEST.json").is_file()


def test_manifest_rejects_archive_hash_without_payload_execution(release_assets) -> None:
    """Hashing an archive after compression can't impersonate running its payload."""
    repo, assets = release_assets
    path = assets / "standalone-evidence-linux-amd64.json"
    receipt = json.loads(path.read_text())
    receipt.pop("archive_verified", None)
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="archive.*verif"):
        manifest.generate_manifest(
            asset_dir=assets, repo_root=repo, tag="v0.2.0", revision="a" * 40
        )
    assert not (assets / "RELEASE_MANIFEST.json").exists()


def test_package_metadata_compares_canonical_names_and_full_markers() -> None:
    """Equivalent spelling and header order preserve cross-platform dependency contracts."""
    project = {
        "name": "policyNIM",
        "requires-python": ">=3.11,<3.13",
        "dependencies": ['Demo-Package[Fast_Path]>=1; python_version < "3.13"'],
    }
    payload = (
        b"Name: policynim\nVersion: 0.2.0\nRequires-Python: <3.13,>=3.11\n"
        b'Requires-Dist: demo_package[fast-path]>=1; python_version < "3.13"\n'
    )
    manifest.check_package_metadata(payload, project, "0.2.0")


@pytest.mark.parametrize(
    "metrics",
    [
        None,
        {"case_count": 5, "overall_pass_rate": 1.0},
        {"case_count": 6, "overall_pass_rate": 0.5},
    ],
)
def test_manifest_cli_rejects_incomplete_wheel_evaluation(release_assets, metrics):
    """A real publication command can't accept missing, reduced or failing wheel evidence."""
    repo, assets = release_assets
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=release-test",
            "-c",
            "user.email=release-test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    for path in assets.glob("*-evidence-*.json"):
        receipt = json.loads(path.read_text())
        receipt["source_revision"] = revision
        receipt["synthetic_offline_cases"] = {"case_count": 6, "overall_pass_rate": 1.0}
        if path.name == "wheel-evidence-3.11.json":
            receipt["synthetic_offline_cases"] = metrics
        path.write_text(json.dumps(receipt))
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(assets),
            "--repo-root",
            str(repo),
            "--tag",
            "v0.2.0",
            "--source-sha",
            revision,
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "offline fixture" in result.stderr.lower()
    assert not (assets / "RELEASE_MANIFEST.json").exists()
