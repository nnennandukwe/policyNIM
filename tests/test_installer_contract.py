"""Contract tests for standalone packaging and installer scripts."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"
PYINSTALLER_SPEC = REPO_ROOT / "packaging" / "pyinstaller.spec"
PYPROJECT = REPO_ROOT / "pyproject.toml"
VERSION = "0.1.0"
LINUX_ASSET = f"policynim-v{VERSION}-linux-amd64"


def test_pyinstaller_is_release_only_and_pinned() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    release_deps = project["dependency-groups"]["release"]
    assert any(dep.lower().startswith("pyinstaller==") for dep in release_deps)

    runtime_deps = project["project"]["dependencies"]
    dev_deps = project["dependency-groups"]["dev"]
    test_deps = project["dependency-groups"]["test"]
    non_release_deps = [*runtime_deps, *dev_deps, *test_deps]
    assert not any(dep.lower().startswith("pyinstaller") for dep in non_release_deps)


def test_pyinstaller_spec_packages_runtime_resources() -> None:
    spec = PYINSTALLER_SPEC.read_text(encoding="utf-8")

    assert "src/policynim/interfaces/cli.py" in spec
    assert 'copy_metadata("policynim")' in spec
    assert '"policynim/policies"' in spec
    assert '"policynim/evals"' in spec
    assert '"policynim/assets"' in spec
    assert '"policynim/templates"' in spec
    assert "collect_submodules" not in spec


def test_installer_scripts_lock_supported_artifact_contract() -> None:
    install_sh = INSTALL_SH.read_text(encoding="utf-8")
    install_ps1 = INSTALL_PS1.read_text(encoding="utf-8")

    for platform in ("darwin-arm64", "darwin-amd64", "linux-amd64"):
        assert platform in install_sh
    assert "windows-amd64" in install_ps1
    assert "SHA256SUMS" in install_sh
    assert "SHA256SUMS" in install_ps1
    assert "NVIDIA_API_KEY" not in install_sh
    assert "NVIDIA_API_KEY" not in install_ps1


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_rejects_unsupported_platform(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir()

    result, home = run_unix_installer(
        tmp_path,
        release_dir,
        arch="aarch64",
        path="",
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Unsupported platform: linux-arm64" in output
    assert "Supported platforms: darwin-arm64, darwin-amd64, linux-amd64." in output
    assert not (home / ".local" / "bin" / "policynim").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_stops_before_extracting_on_checksum_mismatch(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    create_unix_release_asset(release_dir, checksum="0" * 64)

    result, home = run_unix_installer(tmp_path, release_dir)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Checksum mismatch for policynim-v0.1.0-linux-amd64." in output
    assert not (home / ".local" / "share" / "policynim" / VERSION).exists()
    assert not (home / ".local" / "bin" / "policynim").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_reports_missing_release_asset(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (release_dir / "SHA256SUMS").write_text(f"{'0' * 64}  {LINUX_ASSET}\n", encoding="utf-8")

    result, home = run_unix_installer(tmp_path, release_dir)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Could not download release asset policynim-v0.1.0-linux-amd64" in output
    assert "Check the release page or retry the install." in output
    assert not (home / ".local" / "bin" / "policynim").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_installs_launcher_and_prints_path_guidance(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    create_unix_release_asset(release_dir)

    result, home = run_unix_installer(tmp_path, release_dir)

    installed_binary = home / ".local" / "share" / "policynim" / VERSION / "policynim"
    launcher = home / ".local" / "bin" / "policynim"
    assert result.returncode == 0, result.stdout + result.stderr
    assert installed_binary.is_file()
    assert launcher.is_file()
    assert f".local/share/policynim/{VERSION}/policynim" in launcher.read_text(encoding="utf-8")
    assert 'export PATH="$HOME/.local/bin:$PATH"' in result.stdout
    assert "Run `policynim init` to configure your local NVIDIA API key." in result.stdout


def test_windows_installer_contract_is_actionable() -> None:
    script = INSTALL_PS1.read_text(encoding="utf-8")

    assert "Get-FileHash" in script
    assert "Expand-Archive" in script
    assert "policynim.cmd" in script
    assert "LocalAppData" in script
    assert "SetEnvironmentVariable" in script
    assert "policynim init" in script
    assert "Read-Host" not in script


def create_unix_release_asset(release_dir: Path, *, checksum: str | None = None) -> Path:
    build_root = release_dir / "build"
    bundle_root = build_root / "policynim"
    bundle_root.mkdir(parents=True)
    binary = bundle_root / "policynim"
    binary.write_text("#!/bin/sh\nprintf 'fake policynim\\n'\n", encoding="utf-8")
    binary.chmod(0o755)
    (bundle_root / "_internal").mkdir()

    asset_path = release_dir / LINUX_ASSET
    with tarfile.open(asset_path, "w:gz") as archive:
        archive.add(bundle_root, arcname="policynim")

    digest = checksum or hashlib.sha256(asset_path.read_bytes()).hexdigest()
    (release_dir / "SHA256SUMS").write_text(f"{digest}  {LINUX_ASSET}\n", encoding="utf-8")
    return asset_path


def run_unix_installer(
    tmp_path: Path,
    release_dir: Path,
    *,
    os_name: str = "Linux",
    arch: str = "x86_64",
    path: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "POLICYNIM_VERSION": VERSION,
            "POLICYNIM_RELEASE_BASE_URL": release_dir.as_uri(),
            "POLICYNIM_INSTALLER_TEST_OS": os_name,
            "POLICYNIM_INSTALLER_TEST_ARCH": arch,
        }
    )
    if path is not None:
        env["PATH"] = path

    result = subprocess.run(
        [shutil.which("sh") or "sh", str(INSTALL_SH)],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )
    return result, home
