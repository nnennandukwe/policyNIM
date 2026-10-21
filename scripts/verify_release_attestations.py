"""Verify every checksummed release subject against the publishing workflow and source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import time
from pathlib import Path

_DIAGNOSTIC_BYTES = 8192


def _diagnostic_hint(stderr: bytes) -> tuple[str, str]:
    """Map a bounded untrusted prefix to static hints without reproducing its contents."""
    text = stderr.decode("utf-8", errors="replace").lower()
    if any(
        marker in text
        for marker in (
            "http 401",
            "http 403",
            "authentication required",
            "authentication failed",
            "gh auth login",
            "not logged into",
        )
    ):
        return "authentication", "Run gh auth status and check repository access before retrying."
    if any(marker in text for marker in ("no attestations", "attestation not found", "http 404")):
        return (
            "attestation_unavailable",
            "Check access and confirm that this signed subject has a published attestation.",
        )
    mismatch = any(
        marker in text for marker in ("does not match", "mismatch", "expected", "not match")
    )
    if "expected san value to match" in text or (
        mismatch and any(marker in text for marker in ("signer workflow", "workflow identity"))
    ):
        return (
            "workflow_identity",
            "Check the expected release workflow and the artifact's signing workflow; "
            "keep the pin.",
        )
    if "expected sourcerepositorydigest to be" in text or (
        mismatch and any(marker in text for marker in ("source digest", "source revision"))
    ):
        return (
            "source_revision",
            "Check the expected source commit and obtain its matching signed artifact; "
            "keep the pin.",
        )
    return (
        "unclassified",
        "Check GitHub CLI availability and service status, then retry with the same verification "
        "constraints. Use a private operator session for further diagnosis.",
    )


def _run_verifier(command: list[str], *, timeout: float) -> tuple[int, bytes]:
    """Drain the POSIX publishing runner's verifier without spooling untrusted output."""
    deadline = time.monotonic() + timeout
    prefix = bytearray()
    with subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        assert process.stderr is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stderr, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise subprocess.TimeoutExpired(command, timeout)
                    block = os.read(process.stderr.fileno(), _DIAGNOSTIC_BYTES)
                    if not block:
                        break
                    available = _DIAGNOSTIC_BYTES + 1 - len(prefix)
                    if available > 0:
                        prefix.extend(block[:available])
            returncode = process.wait(timeout=max(0, deadline - time.monotonic()))
            return returncode, bytes(prefix)
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                finally:
                    process.wait()


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
            result: dict = {
                "name": name,
                "sha256": digest,
                "verified": False,
                "attempt_outcomes": [],
            }
            report["subjects"].append(result)
            for attempt in range(1, 4):
                result["attempts"] = attempt
                try:
                    returncode, diagnostic = _run_verifier(
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
                        timeout=timeout,
                    )
                except subprocess.TimeoutExpired:
                    result.update(
                        returncode=None,
                        diagnostic_hint="verification_timeout",
                        recovery_instruction="Check service responsiveness, then rerun "
                        "verification "
                        "with the same pins. A timeout stops this run without retrying.",
                    )
                    result["attempt_outcomes"].append(
                        {
                            "attempt": attempt,
                            "outcome": "timeout",
                            "returncode": None,
                            "diagnostic_hint": "verification_timeout",
                        }
                    )
                    report["failure"] = "verification_timeout"
                    return report
                except OSError:
                    result.update(
                        returncode=None,
                        diagnostic_hint="verifier_unavailable",
                        recovery_instruction="Check GitHub CLI installation and local temporary "
                        "storage, then rerun verification with the same pins.",
                    )
                    result["attempt_outcomes"].append(
                        {
                            "attempt": attempt,
                            "outcome": "unavailable",
                            "returncode": None,
                            "diagnostic_hint": "verifier_unavailable",
                        }
                    )
                    report["failure"] = "invalid_subjects_or_verifier_unavailable"
                    return report
                hint, recovery = (
                    ("verified", "")
                    if returncode == 0
                    else _diagnostic_hint(diagnostic[:_DIAGNOSTIC_BYTES])
                )
                result.update(
                    returncode=returncode,
                    diagnostic_hint=hint,
                    recovery_instruction=recovery,
                )
                result["attempt_outcomes"].append(
                    {
                        "attempt": attempt,
                        "outcome": "verified" if returncode == 0 else "rejected",
                        "returncode": returncode,
                        "diagnostic_hint": hint,
                        "stderr_prefix_truncated": len(diagnostic) > _DIAGNOSTIC_BYTES,
                    }
                )
                if returncode == 0:
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
