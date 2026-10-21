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
        "print(os.environ.get('ATTESTATION_TEST_STDERR','private verifier sentinel'),"
        "file=sys.stderr)\n"
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
    subject = report["subjects"][0]
    assert subject["attempts"] == 1
    assert subject["returncode"] is None
    assert subject["attempt_outcomes"] == [
        {
            "attempt": 1,
            "outcome": "timeout",
            "returncode": None,
            "diagnostic_hint": "verification_timeout",
        }
    ]
    assert "without retrying" in subject["recovery_instruction"]


def test_cli_retains_failed_receipt_and_exits_nonzero(subjects, monkeypatch, tmp_path) -> None:
    """Publication receives a nonzero exit even when writing diagnostics succeeds."""
    assets, _ = subjects
    monkeypatch.setenv("ATTESTATION_TEST_EXIT", "1")
    monkeypatch.setenv(
        "ATTESTATION_TEST_STDERR", "HTTP 401: private verifier sentinel token=secret"
    )
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
    report = json.loads(output.read_text())
    assert report["status"] == "failed"
    assert report["subjects"][0]["diagnostic_hint"] == "authentication"
    assert "gh auth status" in result.stdout
    assert "private verifier sentinel" not in result.stdout + result.stderr + output.read_text()
    assert "token=secret" not in result.stdout + result.stderr + output.read_text()


@pytest.mark.parametrize(
    ("diagnostic", "hint", "instruction"),
    [
        ("HTTP 401: authentication required", "authentication", "gh auth status"),
        ("no attestations found for artifact", "attestation_unavailable", "signed subject"),
        (
            "signer workflow does not match expected identity",
            "workflow_identity",
            "release workflow",
        ),
        ("source digest does not match expected revision", "source_revision", "source commit"),
        (
            "failed to verify certificate identity: expected SAN value to match regex private",
            "workflow_identity",
            "release workflow",
        ),
        (
            "expected SourceRepositoryDigest to be private-revision, got private-other-revision",
            "source_revision",
            "source commit",
        ),
        ("an unrecognized rejection", "unclassified", "verification constraints"),
    ],
)
def test_actual_gh_rejections_retain_only_allowlisted_hints(
    subjects, monkeypatch, diagnostic: str, hint: str, instruction: str
) -> None:
    """Actual child stderr yields static recovery hints and per-attempt exit evidence."""
    assets, log = subjects
    monkeypatch.setenv("ATTESTATION_TEST_EXIT", "1")
    monkeypatch.setenv(
        "ATTESTATION_TEST_STDERR", diagnostic + " private verifier sentinel token=secret"
    )
    report = verifier.verify_subjects(assets, "owner/project", "a" * 40)
    subject = report["subjects"][0]
    assert report["failure"] == "attestation_rejected"
    assert subject["diagnostic_hint"] == hint
    assert instruction in subject["recovery_instruction"]
    assert subject["returncode"] == 1
    assert [entry["attempt"] for entry in subject["attempt_outcomes"]] == [1, 2, 3]
    assert all(entry["returncode"] == 1 for entry in subject["attempt_outcomes"])
    assert all(entry["outcome"] == "rejected" for entry in subject["attempt_outcomes"])
    assert len(log.read_text().splitlines()) == 3
    assert "private verifier sentinel" not in json.dumps(report)
    assert "token=secret" not in json.dumps(report)


def test_stderr_classification_reads_only_bounded_prefix(subjects, monkeypatch) -> None:
    """A long response can't expand the receipt or classify a hidden trailing hint."""
    assets, _ = subjects
    monkeypatch.setenv("ATTESTATION_TEST_EXIT", "1")
    monkeypatch.setenv(
        "ATTESTATION_TEST_STDERR", "x" * 9000 + " HTTP 401 private verifier sentinel"
    )
    report = verifier.verify_subjects(assets, "owner/project", "a" * 40)
    subject = report["subjects"][0]
    assert subject["diagnostic_hint"] == "unclassified"
    assert all(entry["stderr_prefix_truncated"] for entry in subject["attempt_outcomes"])
    assert "private verifier sentinel" not in json.dumps(report)
    assert len(json.dumps(report)) < 3000


def test_missing_verifier_has_safe_attempt_evidence(subjects, monkeypatch, tmp_path) -> None:
    """An actual missing executable fails closed without disclosing search paths."""
    assets, _ = subjects
    missing = tmp_path / "private-search-path-sentinel"
    missing.mkdir()
    monkeypatch.setenv("PATH", str(missing))
    report = verifier.verify_subjects(assets, "owner/project", "a" * 40)
    subject = report["subjects"][0]
    assert report["failure"] == "invalid_subjects_or_verifier_unavailable"
    assert subject["diagnostic_hint"] == "verifier_unavailable"
    assert subject["returncode"] is None
    assert subject["attempt_outcomes"] == [
        {
            "attempt": 1,
            "outcome": "unavailable",
            "returncode": None,
            "diagnostic_hint": "verifier_unavailable",
        }
    ]
    assert "private-search-path-sentinel" not in json.dumps(report)
