"""Exercise signature-gate control flow without inventing signed-build evidence."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_release_attestations.py"
SPEC = importlib.util.spec_from_file_location("release_attestations", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
pytestmark = pytest.mark.skipif(os.name == "nt", reason="Release verifier runs on Ubuntu.")


@pytest.fixture
def subjects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Provide owned downloads and a fake gh process; signatures aren't generated."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "policynim.tar.gz").write_text("synthetic archive")
    (assets / "RELEASE_MANIFEST.json").write_text(
        json.dumps({"source_sha": "a" * 40, "assets": [{"name": "policynim.tar.gz"}]})
    )
    entries = [
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name
        for path in [assets / "policynim.tar.gz", assets / "RELEASE_MANIFEST.json"]
    ]
    (assets / "SHA256SUMS").write_text("\n".join(entries) + "\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "gh-argv.jsonl"
    fake = binaries / "gh"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys,time\n"
        "with open(os.environ['ATTESTATION_TEST_LOG'],'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "time.sleep(float(os.environ.get('ATTESTATION_TEST_DELAY','0')))\n"
        "print('private verifier sentinel',file=sys.stderr)\n"
        "raise SystemExit(int(os.environ.get('ATTESTATION_TEST_EXIT','0')))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("ATTESTATION_TEST_LOG", str(log))
    monkeypatch.setattr(verifier.time, "sleep", lambda seconds: None)
    return assets, log


def test_every_subject_is_pinned_to_repository_workflow_and_source(subjects) -> None:
    """The actual subprocess receives all identity constraints for every subject."""
    assets, log = subjects
    report = verifier.verify_subjects(assets, "owner/project", "a" * 40)
    assert report["status"] == "passed"
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == len(report["subjects"]) == 2
    for call in calls:
        assert call[:2] == ["attestation", "verify"]
        assert call[call.index("--repo") + 1] == "owner/project"
        assert call[call.index("--source-digest") + 1] == "a" * 40
        assert (
            call[call.index("--signer-workflow") + 1]
            == "owner/project/.github/workflows/release.yml"
        )


@pytest.mark.parametrize(
    "failure",
    ["changed", "duplicate", "missing-manifest", "traversal", "omitted-subject", "extra-file"],
)
def test_invalid_subject_set_is_rejected_before_verifier_io(subjects, failure: str) -> None:
    """Unsafe or incomplete checksum lists can't authorize external verification."""
    assets, log = subjects
    checksums = assets / "SHA256SUMS"
    original = checksums.read_text()
    if failure == "changed":
        (assets / "policynim.tar.gz").write_text("substitution")
    elif failure == "duplicate":
        checksums.write_text(original + original)
    elif failure == "extra-file":
        (assets / "unverified.bin").write_bytes(b"extra")
    elif failure == "omitted-subject":
        checksums.write_text(original.splitlines()[1] + "\n")
    elif failure == "missing-manifest":
        checksums.write_text(original.splitlines()[0] + "\n")
    else:
        checksums.write_text("0" * 64 + "  ../unowned\n")
    report = verifier.verify_subjects(assets, "owner/project", "a" * 40)
    assert report["status"] == "failed"
    assert not log.exists()


def test_rejected_signature_stops_publication_without_leaking_diagnostics(
    subjects, monkeypatch
) -> None:
    """A failed gh process leaves explicit partial evidence and never passes."""
    assets, log = subjects
    monkeypatch.setenv("ATTESTATION_TEST_EXIT", "1")
    report = verifier.verify_subjects(assets, "owner/project", "a" * 40)
    assert report["status"] == "failed"
    assert report["failure"] == "attestation_rejected"
    assert report["subjects"][0]["verified"] is False
    assert len(log.read_text().splitlines()) == 3
    assert "private verifier sentinel" not in json.dumps(report)


def test_stalled_verifier_is_killed_and_recorded_incomplete(subjects, monkeypatch) -> None:
    """A stalled signature service can't hang the publication gate indefinitely."""
    assets, _ = subjects
    monkeypatch.setenv("ATTESTATION_TEST_DELAY", "5")
    report = verifier.verify_subjects(assets, "owner/project", "a" * 40, timeout=0.05)
    assert report["status"] == "failed"
    assert report["failure"] == "verification_timeout"


def test_cli_retains_failed_receipt_and_exits_nonzero(subjects, monkeypatch, tmp_path) -> None:
    """Publication receives a nonzero exit even when writing diagnostics succeeds."""
    assets, _ = subjects
    monkeypatch.setenv("ATTESTATION_TEST_EXIT", "1")
    output = tmp_path / "verification.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(assets),
            "--repository",
            "owner/project",
            "--revision",
            "a" * 40,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 1
    assert json.loads(output.read_text())["status"] == "failed"
    assert "private verifier sentinel" not in result.stdout + result.stderr
