"""Execute workflow selection and collector gates without publishing or signing."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def release_steps() -> list[dict[str, Any]]:
    """Read the selected publishing job from the real workflow."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    return workflow["jobs"]["publish-github-release"]["steps"]


def run_selection(root: Path, *, revision: str = "a" * 40) -> subprocess.CompletedProcess[str]:
    """Execute the workflow's actual selection producer with trusted fixture CI values."""
    step = next(
        step for step in release_steps() if step["name"] == "Select artifact promotion evidence"
    )
    script = step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    env = {
        **os.environ,
        "GITHUB_REPOSITORY": "nnennandukwe/policyNIM",
        "GITHUB_SHA": revision,
        "RELEASE_TAG": "v0.2.0",
        "PYTHONPATH": str(ROOT / "scripts"),
    }
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.fixture
def gate_fixture(tmp_path: Path) -> Path:
    """Keep checkout identities independent from the mutable downloaded manifest."""
    source = tmp_path / "artifacts/public-generation"
    source.mkdir(parents=True)
    manifest = json.dumps({"catalog_digest": "sha256:" + "d" * 64}).encode()
    bundle = source / "generation.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", manifest)
    bundle_digest = "sha256:" + hashlib.sha256(bundle.read_bytes()).hexdigest()
    (source / "generation.sha256").write_text(bundle_digest)
    lock = b"fixture checked-out lock"
    (tmp_path / "uv.lock").write_bytes(lock)
    assets = tmp_path / "release-assets"
    assets.mkdir()
    payload = b"fixture wheel"
    (assets / "fixture.whl").write_bytes(payload)
    asset_manifest = {
        "schema_version": "1",
        "release_tag": "v0.2.0",
        "source_sha": "a" * 40,
        "dependency_lock": "sha256:" + hashlib.sha256(lock).hexdigest(),
        "generation_id": hashlib.sha256(manifest).hexdigest(),
        "catalog_digest": "sha256:" + "d" * 64,
        "public_bundle_digest": bundle_digest,
        "scope": "synthetic workflow gate fixture",
        "assets": [
            {
                "name": "fixture.whl",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    manifest_bytes = json.dumps(asset_manifest).encode()
    (assets / "RELEASE_MANIFEST.json").write_bytes(manifest_bytes)
    (assets / "SHA256SUMS").write_text(
        hashlib.sha256(payload).hexdigest()
        + "  fixture.whl\n"
        + hashlib.sha256(manifest_bytes).hexdigest()
        + "  RELEASE_MANIFEST.json\n"
    )
    return tmp_path


def test_selection_uses_checkout_and_ci_values(gate_fixture: Path) -> None:
    """A downloaded report can't choose the expected revision, lock or catalog."""
    manifest_path = gate_fixture / "release-assets/RELEASE_MANIFEST.json"
    downloaded = json.loads(manifest_path.read_text())
    downloaded.update(source_sha="b" * 40, catalog_digest="sha256:" + "e" * 64)
    manifest_path.write_text(json.dumps(downloaded))
    process = run_selection(gate_fixture)
    assert process.returncode == 0, process.stderr
    selection = json.loads((gate_fixture / "release-evidence/selection.json").read_text())
    assert selection["expected"]["source_sha"] == "a" * 40
    assert selection["expected"]["catalog_digest"] == "sha256:" + "d" * 64
    assert selection["expected"]["dependency_lock"] == (
        "sha256:" + hashlib.sha256((gate_fixture / "uv.lock").read_bytes()).hexdigest()
    )
    assert selection["required_checks"] == [
        {"id": "artifacts", "kind": "artifacts", "context": {}, "cases": []},
        {"id": "provenance", "kind": "provenance", "context": {}, "cases": []},
    ]


def test_missing_provenance_holds_the_actual_collector(gate_fixture: Path) -> None:
    """Passing artifact bytes alone can't authorize draft creation."""
    process = run_selection(gate_fixture)
    assert process.returncode == 0, process.stderr
    report = gate_fixture / "release-evidence/report.json"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/collect_release_evidence.py"),
            "--selection",
            str(gate_fixture / "release-evidence/selection.json"),
            "--artifact-dir",
            str(gate_fixture / "release-assets"),
            "--provenance-receipt",
            str(gate_fixture / "missing-provenance.json"),
            "--output",
            str(report),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 1
    result = json.loads(report.read_text())
    assert result["decision"] == "hold"
    assert any(
        check["kind"] == "artifacts" and check["status"] == "passed" for check in result["checks"]
    )
    assert any(
        check["kind"] == "provenance" and check["status"] != "passed" for check in result["checks"]
    )


def test_invalid_checkout_or_ci_selection_preserves_prior_selection(gate_fixture: Path) -> None:
    """A bad expected identity can't overwrite a previously retained selection."""
    assert run_selection(gate_fixture).returncode == 0
    selection = gate_fixture / "release-evidence/selection.json"
    prior = selection.read_bytes()
    assert run_selection(gate_fixture, revision="invalid-revision").returncode != 0
    assert selection.read_bytes() == prior
    (gate_fixture / "artifacts/public-generation/generation.sha256").write_text(
        "sha256:" + "0" * 64
    )
    assert run_selection(gate_fixture).returncode != 0
    assert selection.read_bytes() == prior


def test_release_gate_order_and_locked_environment() -> None:
    """Notes, signed provenance and scoped collection must succeed before creating a draft."""
    steps = release_steps()
    names = [step["name"] for step in steps]
    assert names.index("Sync locked evidence dependencies") < names.index(
        "Select artifact promotion evidence"
    )
    assert names.index("Rebuild wheel from reviewed source") < names.index("Prepare release assets")
    assert names.index("Prepare release assets") < names.index(
        "Generate release asset attestations"
    )
    assert names.index("Verify release asset attestations") < names.index(
        "Gate artifact and provenance consistency"
    )
    assert names.index("Gate artifact and provenance consistency") < names.index(
        "Create draft GitHub release"
    )
    by_name = {step["name"]: step for step in steps}
    assert (
        by_name["Sync locked evidence dependencies"]["run"]
        == "uv sync --locked --no-dev --no-default-groups --group verification "
        "--python ${{ env.PYTHON_VERSION }}"
    )
    assert (
        "uv run --no-sync python scripts/check_release_notes.py "
        "--repo-root . --output release-notes.md" in by_name["Prepare release assets"]["run"]
    )
    assert by_name["Retain artifact promotion evidence"]["if"] == "always()"
    assert by_name["Retain artifact promotion evidence"]["with"]["path"] == "release-evidence/"
    for name in ["Gate artifact and provenance consistency", "Select artifact promotion evidence"]:
        assert not by_name[name].get("continue-on-error", False)


def test_publication_rejects_changed_wheel_with_matching_receipts(gate_fixture: Path) -> None:
    """Co-produced receipts can't authorize bytes that differ from the publisher's rebuild."""
    steps = {step["name"]: step for step in release_steps()}
    assert steps["Rebuild wheel from reviewed source"]["run"] == (
        "uv build --wheel --out-dir release-source-wheel"
    )
    command = steps["Prepare release assets"]["run"]
    assert command.index("python - <<'PY'") < command.index("scripts/release_manifest.py")
    script = command.split("python - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    reference = gate_fixture / "release-source-wheel"
    reference.mkdir()
    candidate = gate_fixture / "release-assets/fixture.whl"
    original = candidate.read_bytes()
    (reference / candidate.name).write_bytes(original)

    def invoke() -> subprocess.CompletedProcess[str]:
        """Execute the actual publication comparison independently of supplied receipts."""
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=gate_fixture,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    assert invoke().returncode == 0
    candidate.write_bytes(b"altered executable wheel")
    for runtime in ("3.11", "3.12"):
        (candidate.parent / f"wheel-evidence-{runtime}.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "wheel": {"sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()},
                }
            )
        )
    rejected = invoke()
    assert rejected.returncode != 0
    assert "differs from the reviewed-source rebuild" in rejected.stderr
    candidate.write_bytes(original)
    assert invoke().returncode == 0
