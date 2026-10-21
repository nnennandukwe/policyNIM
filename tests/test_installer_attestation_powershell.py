"""Run the PowerShell installer against controlled download and signature boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="Requires PowerShell.")


@pytest.mark.parametrize("rejected", [False, True])
def test_powershell_signature_gate_preserves_install_on_rejection(
    tmp_path: Path, rejected: bool
) -> None:
    """Exercise the actual installer; fake signatures aren't cryptographic evidence."""
    release = tmp_path / "release"
    release.mkdir()
    archive = release / "policynim-v0.1.0-windows-amd64.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("policynim/policynim.exe", b"synthetic launcher; never executed")
    (release / "SHA256SUMS").write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n"
    )
    local = tmp_path / "local"
    install = local / "PolicyNIM/0.1.0"
    install.mkdir(parents=True)
    marker = install / "preserved.txt"
    marker.write_text("existing install")
    log = tmp_path / "gh.jsonl"
    wrapper = tmp_path / "installer-test.ps1"
    wrapper.write_text(
        """
function global:Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing)
    $source = Join-Path $env:ATTESTATION_TEST_RELEASE ([IO.Path]::GetFileName($Uri))
    Copy-Item -LiteralPath $source -Destination $OutFile
}
function global:gh {
    ConvertTo-Json -Compress -InputObject @($args) | Add-Content $env:ATTESTATION_TEST_LOG
    if ($args[0] -eq 'api') { $global:LASTEXITCODE = 0; return ('a' * 40) }
    $global:LASTEXITCODE = [int]$env:ATTESTATION_TEST_EXIT
}
& $env:ATTESTATION_TEST_INSTALLER -Version '0.1.0'
""",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if key.upper() != "LOCALAPPDATA"}
    env.update(
        LocalAppData=str(local),
        POLICYNIM_REPOSITORY_URL="https://github.com/nnennandukwe/policyNIM",
        POLICYNIM_VERIFY_ATTESTATION="required",
        POLICYNIM_INSTALLER_TEST_OS="windows",
        POLICYNIM_INSTALLER_TEST_ARCH="AMD64",
        ATTESTATION_TEST_RELEASE=str(release),
        ATTESTATION_TEST_LOG=str(log),
        ATTESTATION_TEST_EXIT="1" if rejected else "0",
        ATTESTATION_TEST_INSTALLER=str(ROOT / "scripts/install.ps1"),
    )
    result = subprocess.run(
        [PWSH or "pwsh", "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert log.exists(), result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8-sig").splitlines()]
    assert len(calls) == 2
    call = calls[1]
    assert call[:2] == ["attestation", "verify"]
    assert call[call.index("--source-digest") + 1] == "a" * 40
    assert call[call.index("--signer-workflow") + 1] == (
        "nnennandukwe/policyNIM/.github/workflows/release.yml"
    )
    if rejected:
        assert result.returncode != 0
        assert marker.read_text() == "existing install"
        assert "attestation verification failed" in result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (install / "policynim.exe").is_file()
