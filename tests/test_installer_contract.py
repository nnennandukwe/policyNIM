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
LINUX_ASSET = f"policynim-v{VERSION}-linux-amd64.tar.gz"


def test_pyinstaller_is_release_only_and_pinned() -> None:
    """Keep PyInstaller scoped to release builds instead of runtime installs."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    release_deps = project["dependency-groups"]["release"]
    assert any(dep.lower().startswith("pyinstaller==") for dep in release_deps)

    runtime_deps = project["project"]["dependencies"]
    dev_deps = project["dependency-groups"]["dev"]
    test_deps = project["dependency-groups"]["test"]
    non_release_deps = [*runtime_deps, *dev_deps, *test_deps]
    assert not any(dep.lower().startswith("pyinstaller") for dep in non_release_deps)


def test_pyinstaller_spec_packages_runtime_resources() -> None:
    """Ensure standalone bundles include the package resources used at runtime."""
    spec = PYINSTALLER_SPEC.read_text(encoding="utf-8")

    assert "src/policynim/interfaces/cli.py" in spec
    assert 'copy_metadata("policynim")' in spec
    assert '"policynim/policies"' in spec
    assert '"policynim/evals"' in spec
    assert '"policynim/assets"' in spec
    assert '"policynim/templates"' in spec
    assert "collect_submodules" not in spec


def test_installer_scripts_lock_supported_artifact_contract() -> None:
    """Keep installer asset names and security assumptions aligned with releases."""
    install_sh = INSTALL_SH.read_text(encoding="utf-8")
    install_ps1 = INSTALL_PS1.read_text(encoding="utf-8")

    for asset in (
        "policynim-$TAG-darwin-amd64.tar.gz",
        "policynim-$TAG-darwin-arm64.tar.gz",
        "policynim-$TAG-linux-amd64.tar.gz",
    ):
        assert asset in install_sh
    assert "policynim-$tag-windows-amd64.zip" in install_ps1
    assert "SHA256SUMS" in install_sh
    assert "SHA256SUMS" in install_ps1
    assert "POLICYNIM_VERIFY_ATTESTATION" in install_sh
    assert "POLICYNIM_VERIFY_ATTESTATION" in install_ps1
    assert "gh attestation verify" in install_sh
    assert "gh attestation verify" in install_ps1
    for command in (
        "awk",
        "cat",
        "chmod",
        "cp",
        "curl",
        "dirname",
        "find",
        "head",
        "mkdir",
        "mktemp",
        "mv",
        "rm",
        "tar",
        "uname",
    ):
        assert f"need_command {command}" in install_sh
    assert "NVIDIA_API_KEY" not in install_sh
    assert "NVIDIA_API_KEY" not in install_ps1


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_can_require_github_attestation_verification(tmp_path: Path) -> None:
    """Let security-conscious users require GitHub artifact attestations during install."""
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    create_unix_release_asset(release_dir)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh.log"
    gh_stub = bin_dir / "gh"
    gh_stub.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" > "$GH_LOG"\n',
        encoding="utf-8",
    )
    gh_stub.chmod(0o755)

    result, home = run_unix_installer(
        tmp_path,
        release_dir,
        path=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        env_extra={
            "GH_LOG": str(gh_log),
            "POLICYNIM_VERIFY_ATTESTATION": "1",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".local" / "bin" / "policynim").exists()
    assert "attestation verify" in gh_log.read_text(encoding="utf-8")
    assert "-R nnennandukwe/policyNIM" in gh_log.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_rejects_unsupported_platform(tmp_path: Path) -> None:
    """Reject unsupported Unix platform tuples before downloading anything."""
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
    assert "Supported platforms: darwin-amd64, darwin-arm64, linux-amd64." in output
    assert not (home / ".local" / "bin" / "policynim").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_installs_macos_intel_bundle(tmp_path: Path) -> None:
    """Allow Intel macOS users to install the restored standalone bundle."""
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    asset = "policynim-v0.1.0-darwin-amd64.tar.gz"
    create_unix_release_asset(release_dir, asset_name=asset)

    result, home = run_unix_installer(
        tmp_path,
        release_dir,
        os_name="Darwin",
        arch="x86_64",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".local" / "share" / "policynim" / VERSION / "policynim").is_file()


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_stops_before_extracting_on_checksum_mismatch(tmp_path: Path) -> None:
    """Abort before extraction when the downloaded archive checksum is wrong."""
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    create_unix_release_asset(release_dir, checksum="0" * 64)

    result, home = run_unix_installer(tmp_path, release_dir)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Checksum mismatch for policynim-v0.1.0-linux-amd64.tar.gz." in output
    assert not (home / ".local" / "share" / "policynim" / VERSION).exists()
    assert not (home / ".local" / "bin" / "policynim").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_reports_missing_release_asset(tmp_path: Path) -> None:
    """Surface actionable guidance when a release asset is unavailable."""
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (release_dir / "SHA256SUMS").write_text(f"{'0' * 64}  {LINUX_ASSET}\n", encoding="utf-8")

    result, home = run_unix_installer(tmp_path, release_dir)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Could not download release asset policynim-v0.1.0-linux-amd64.tar.gz" in output
    assert "Check the release page or retry the install." in output
    assert not (home / ".local" / "bin" / "policynim").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_reports_missing_required_command(tmp_path: Path) -> None:
    """Fail with installer guidance when a required Unix tool is unavailable."""
    release_dir = tmp_path / "release"
    release_dir.mkdir()

    result, home = run_unix_installer(tmp_path, release_dir, path="")

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Missing required command: curl. Install it and retry." in output
    assert not (home / ".local" / "bin" / "policynim").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_unix_installer_installs_launcher_and_prints_path_guidance(tmp_path: Path) -> None:
    """Install the bundle, create a launcher, and print follow-up setup guidance."""
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
    assert "Run `policynim quickstart` to choose a first-run path." in result.stdout
    assert "Hosted MCP does not require `policynim init` or `policynim ingest`." in (result.stdout)
    assert "For local CLI or local MCP, run `policynim init` then `policynim ingest`." in (
        result.stdout
    )
    assert "Run `policynim doctor` to inspect first-run setup." in result.stdout
    assert "Run `policynim support-bundle` before opening an issue." in result.stdout


def test_windows_installer_contract_is_actionable() -> None:
    """Check the PowerShell installer keeps checksum and PATH behavior discoverable."""
    script = INSTALL_PS1.read_text(encoding="utf-8")

    assert "Get-FileHash" in script
    assert "Expand-Archive" in script
    assert "policynim.cmd" in script
    assert "LocalAppData" in script
    assert "SetEnvironmentVariable" in script
    assert "policynim quickstart" in script
    assert "Hosted MCP does not require ``policynim init`` or ``policynim ingest``." in script
    assert "For local CLI or local MCP, run ``policynim init`` then ``policynim ingest``." in (
        script
    )
    assert "policynim doctor" in script
    assert "policynim support-bundle" in script
    assert "Read-Host" not in script


def create_unix_release_asset(
    release_dir: Path,
    *,
    asset_name: str = LINUX_ASSET,
    checksum: str | None = None,
) -> Path:
    """Create a fake Unix release archive and matching checksum file."""
    build_root = release_dir / "build"
    bundle_root = build_root / "policynim"
    bundle_root.mkdir(parents=True)
    binary = bundle_root / "policynim"
    binary.write_text("#!/bin/sh\nprintf 'fake policynim\\n'\n", encoding="utf-8")
    binary.chmod(0o755)
    (bundle_root / "_internal").mkdir()

    asset_path = release_dir / asset_name
    with tarfile.open(asset_path, "w:gz") as archive:
        archive.add(bundle_root, arcname="policynim")

    digest = checksum or hashlib.sha256(asset_path.read_bytes()).hexdigest()
    (release_dir / "SHA256SUMS").write_text(f"{digest}  {asset_name}\n", encoding="utf-8")
    return asset_path


def run_unix_installer(
    tmp_path: Path,
    release_dir: Path,
    *,
    os_name: str = "Linux",
    arch: str = "x86_64",
    path: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the Unix installer against a local fake release directory."""
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
    if env_extra:
        env.update(env_extra)
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
