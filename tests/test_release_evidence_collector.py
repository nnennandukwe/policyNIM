"""Offline release evidence through the actual collector command."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts/collect_release_evidence.py"


def digest(content: bytes) -> str:
    """Return a bare checksum for artifact contracts."""
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def candidate(tmp_path: Path) -> tuple[Path, dict, Path]:
    """Create source-bound artifact bytes and an explicit trusted selection."""
    assets = tmp_path / "assets"
    assets.mkdir()
    payload = b"fixture artifact"
    (assets / "fixture.whl").write_bytes(payload)
    manifest = {
        "schema_version": "1",
        "release_tag": "v0.2.0",
        "source_sha": "a" * 40,
        "dependency_lock": "sha256:" + "b" * 64,
        "generation_id": "c" * 64,
        "catalog_digest": "sha256:" + "d" * 64,
        "public_bundle_digest": "sha256:" + "e" * 64,
        "scope": "fixture artifact consistency",
        "assets": [{"name": "fixture.whl", "size_bytes": len(payload), "sha256": digest(payload)}],
    }
    manifest_bytes = (json.dumps(manifest) + "\n").encode()
    (assets / "RELEASE_MANIFEST.json").write_bytes(manifest_bytes)
    (assets / "SHA256SUMS").write_text(
        f"{digest(payload)}  fixture.whl\n{digest(manifest_bytes)}  RELEASE_MANIFEST.json\n"
    )
    expected = {
        key: value
        for key, value in manifest.items()
        if key not in {"schema_version", "scope", "assets"}
    }
    expected.update(repository="nnennandukwe/policyNIM", manifest_sha256=digest(manifest_bytes))
    selection = {
        "schema_version": "1",
        "expected": expected,
        "required_checks": [{"id": "artifacts", "kind": "artifacts"}],
    }
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selection))
    return path, selection, assets


def run_collector(
    selection: Path, assets: Path, *arguments: str
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Execute the real offline entry point and parse its single report."""
    process = subprocess.run(
        [
            sys.executable,
            str(COLLECTOR),
            "--selection",
            str(selection),
            "--artifact-dir",
            str(assets),
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return process, json.loads(process.stdout)


def test_artifact_facts_are_recomputed_and_report_is_reusable(candidate, tmp_path: Path) -> None:
    """The public CLI creates a bounded report and rerenders unchanged input deterministically."""
    selection, _, assets = candidate
    output = tmp_path / "report.json"
    process, report = run_collector(selection, assets, "--output", str(output))
    assert process.returncode == 0
    assert report["decision"] == "evidence_consistent"
    assert report["checks"][0]["basis"] == "recomputed_artifact_bytes"
    assert report["checks"][0]["observed"]["repository"] is None
    first = output.read_bytes()
    repeated, _ = run_collector(selection, assets, "--output", str(output))
    assert repeated.returncode == 0 and output.read_bytes() == first


@pytest.mark.parametrize("repository", ["nnennandukwe/policyNIM", "another/repository"])
def test_explicit_artifact_repository_must_match_selection(candidate, repository: str) -> None:
    """Matching hashes can't authorize an explicitly contradictory repository identity."""
    path, selection, assets = candidate
    manifest_path = assets / "RELEASE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["repository"] = repository
    manifest_bytes = (json.dumps(manifest) + "\n").encode()
    manifest_path.write_bytes(manifest_bytes)
    selection["expected"]["manifest_sha256"] = digest(manifest_bytes)
    path.write_text(json.dumps(selection))
    (assets / "SHA256SUMS").write_text(
        f"{digest((assets / 'fixture.whl').read_bytes())}  fixture.whl\n"
        f"{digest(manifest_bytes)}  RELEASE_MANIFEST.json\n"
    )
    process, report = run_collector(path, assets)
    expected_match = repository == selection["expected"]["repository"]
    assert process.returncode == (0 if expected_match else 1)
    assert report["decision"] == ("evidence_consistent" if expected_match else "hold")
    assert report["checks"][0]["observed"]["repository"] == repository
    if not expected_match:
        assert report["checks"][0]["reason"] == "artifact_mismatch"


def test_changed_artifact_holds_and_records_failure(candidate) -> None:
    """A source-bound manifest can't authorize changed local bytes."""
    selection, _, assets = candidate
    (assets / "fixture.whl").write_bytes(b"changed artifact")
    process, report = run_collector(selection, assets)
    assert process.returncode == 1 and report["decision"] == "hold"
    assert report["checks"][0]["reason"] == "artifact_mismatch"


def client_requirement() -> dict:
    """Select an explicit client/version/transport/auth/protocol and named acceptance cases."""
    return {
        "id": "codex-http",
        "kind": "client",
        "context": {
            "client": "codex",
            "client_version": "0.155.1",
            "server_sdk": "mcp==2.2.0",
            "protocol": "2026-07-28",
            "transport": "http",
            "auth_mode": "beta_key",
        },
        "cases": ["tools", "resources", "errors", "cancellation", "jobs"],
    }


def execution_receipt(selection: dict) -> dict:
    """Build supplied recorded evidence, without representing this fixture as a client run."""
    requirement = client_requirement()
    return {
        "schema_version": "1",
        "evidence_type": "execution_receipt",
        "check_id": requirement["id"],
        "producer": "fixture-runner",
        "producer_version": "1",
        "status": "passed",
        "exit_code": 0,
        "observed": selection["expected"],
        "context": requirement["context"],
        "cases": [{"id": name, "status": "passed"} for name in requirement["cases"]],
        "reference": "https://github.com/nnennandukwe/policyNIM/actions/runs/1",
    }


def test_declared_only_client_data_never_satisfies_execution(candidate, tmp_path: Path) -> None:
    """Valid operator declarations keep required execution pending."""
    path, selection, assets = candidate
    selection["required_checks"].append(client_requirement())
    path.write_text(json.dumps(selection))
    declaration = tmp_path / "declaration.json"
    declaration.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "evidence_type": "operator_declaration",
                "context": client_requirement()["context"],
                "sanitized": True,
            }
        )
    )
    process, report = run_collector(path, assets, "--client-declaration", str(declaration))
    assert process.returncode == 1
    assert report["declarations"][0]["status"] == "declared_only"
    assert report["checks"][1]["reason"] == "missing_execution_receipt"


@pytest.mark.parametrize(
    "fault", ["source", "missing_case", "skipped", "timeout", "nonzero", "context"]
)
def test_required_recorded_results_fail_closed(candidate, tmp_path: Path, fault: str) -> None:
    """A receipt's label can't bypass identity, cases, timing or subprocess failure checks."""
    path, selection, assets = candidate
    selection["required_checks"].append(client_requirement())
    path.write_text(json.dumps(selection))
    receipt = execution_receipt(selection)
    if fault == "source":
        receipt["observed"] = {**receipt["observed"], "source_sha": "f" * 40}
    elif fault == "missing_case":
        receipt["cases"].pop()
    elif fault == "skipped":
        receipt["cases"][0]["status"] = "skipped"
    elif fault == "timeout":
        receipt["status"] = "timed_out"
    elif fault == "nonzero":
        receipt["exit_code"] = 2
    else:
        receipt["context"]["client_version"] = "0.0.0"
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    process, report = run_collector(path, assets, "--execution-receipt", str(receipt_path))
    assert process.returncode == 1 and report["checks"][1]["status"] != "passed"


def test_recorded_execution_keeps_its_trust_label(candidate, tmp_path: Path) -> None:
    """Matching receipt contents keep their supplied-evidence label."""
    path, selection, assets = candidate
    selection["required_checks"].append(client_requirement())
    path.write_text(json.dumps(selection))
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(execution_receipt(selection)))
    process, report = run_collector(path, assets, "--execution-receipt", str(receipt_path))
    assert process.returncode == 0
    assert report["checks"][1]["basis"] == "supplied_execution_receipt"
    assert report["checks"][1]["producer_authenticated"] is False


@pytest.mark.parametrize("fault", ["lock", "generation", "catalog", "bundle", "manifest"])
def test_selected_artifact_identity_cant_drift(candidate, fault: str) -> None:
    """A matching artifact version can't substitute for the selected release identity."""
    path, selection, assets = candidate
    key = {
        "lock": "dependency_lock",
        "generation": "generation_id",
        "catalog": "catalog_digest",
        "bundle": "public_bundle_digest",
        "manifest": "manifest_sha256",
    }[fault]
    selection["expected"][key] = (
        "sha256:" if key in {"dependency_lock", "catalog_digest", "public_bundle_digest"} else ""
    ) + "f" * 64
    path.write_text(json.dumps(selection))
    process, report = run_collector(path, assets)
    assert process.returncode == 1 and report["checks"][0]["reason"] == "artifact_mismatch"
    assert report["checks"][0]["observed"][key] != selection["expected"][key]


def test_failed_partial_receipt_retains_actual_observations(candidate, tmp_path: Path) -> None:
    """Absent observed values remain unavailable and a failed exit isn't rewritten as success."""
    path, selection, assets = candidate
    selection["required_checks"].append(client_requirement())
    path.write_text(json.dumps(selection))
    receipt = execution_receipt(selection)
    receipt.update(
        status="failed", exit_code=3, observed={"source_sha": "a" * 40}, failure_class="connection"
    )
    receipt["cases"][0]["status"] = "failed"
    receipt_path = tmp_path / "partial.json"
    receipt_path.write_text(json.dumps(receipt))
    process, report = run_collector(path, assets, "--execution-receipt", str(receipt_path))
    result = report["checks"][1]
    assert process.returncode == 1
    assert result["observed"]["source_sha"] == "a" * 40
    assert result["observed"]["dependency_lock"] is None
    assert result["recorded_status"] == "failed" and result["exit_code"] == 3
    assert result["failure_class"] == "connection" and result["receipt_sha256"] == digest(
        receipt_path.read_bytes()
    )


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate_receipt",
        "duplicate_case",
        "declared_as_execution",
        "secret_reference",
        "invalid_observed",
        "unselected",
    ],
)
def test_invalid_supplied_evidence_is_sanitized_and_holds(
    candidate, tmp_path: Path, fault: str
) -> None:
    """Malformed or conflicting evidence can't be ignored or leak rejected input values."""
    path, selection, assets = candidate
    selection["required_checks"].append(client_requirement())
    path.write_text(json.dumps(selection))
    receipt = execution_receipt(selection)
    marker = "private-sentinel-value"
    if fault == "duplicate_case":
        receipt["cases"].append(receipt["cases"][0])
    elif fault == "declared_as_execution":
        receipt["evidence_type"] = "operator_declaration"
    elif fault == "secret_reference":
        receipt["reference"] += "?token=" + marker
    elif fault == "invalid_observed":
        receipt["observed"] = {"source_sha": marker}
    elif fault == "unselected":
        receipt["check_id"] = "unselected"
    receipt_path = tmp_path / "invalid.json"
    receipt_path.write_text(json.dumps(receipt))
    arguments = ["--execution-receipt", str(receipt_path)]
    if fault == "duplicate_receipt":
        arguments *= 2
    process, report = run_collector(path, assets, *arguments)
    assert process.returncode == 1 and report["input_errors"]
    assert marker not in process.stdout and marker not in process.stderr


@pytest.mark.parametrize(
    ("fault", "error_class"),
    [
        ("missing", "FileNotFoundError"),
        ("symlink", "ValueError"),
        pytest.param(
            "unreadable",
            "PermissionError",
            marks=pytest.mark.skipif(
                os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
                reason="POSIX read-mode denial needs an unprivileged Unix process.",
            ),
        ),
    ],
)
def test_unavailable_provenance_input_isnt_a_receipt_mismatch(
    candidate, tmp_path: Path, fault: str, error_class: str
) -> None:
    """Keep the real CLI on hold with a safe input diagnosis before receipt validation."""
    path, selection, assets = candidate
    selection["required_checks"].append({"id": "provenance", "kind": "provenance"})
    path.write_text(json.dumps(selection))
    receipt_path = tmp_path / "private-provenance.json"
    if fault == "symlink":
        receipt_path.symlink_to(path)
    elif fault == "unreadable":
        receipt_path.write_text("private-verifier-detail")
        receipt_path.chmod(0)
    try:
        process, report = run_collector(path, assets, "--provenance-receipt", str(receipt_path))
    finally:
        if fault == "unreadable":
            receipt_path.chmod(0o600)
    assert process.returncode == 1 and report["decision"] == "hold"
    check = report["checks"][1]
    assert check["status"] == "failed" and check["observed"] == {}
    assert check["reason"] == "provenance_input_unavailable"
    assert check["error_class"] == error_class
    assert "receipt_sha256" not in check
    assert "private-verifier-detail" not in process.stdout
    assert str(receipt_path) not in process.stdout


def provenance_receipt(selection: dict, assets: Path) -> dict:
    """Match the existing #145 receipt contract using fixture-only verification labels."""
    manifest = json.loads((assets / "RELEASE_MANIFEST.json").read_text())
    subjects = [
        {"name": item["name"], "sha256": item["sha256"], "verified": True, "attempts": 1}
        for item in manifest["assets"]
    ]
    subjects.append(
        {
            "name": "RELEASE_MANIFEST.json",
            "sha256": selection["expected"]["manifest_sha256"],
            "verified": True,
            "attempts": 1,
        }
    )
    return {
        "status": "passed",
        "repository": selection["expected"]["repository"],
        "source_revision": selection["expected"]["source_sha"],
        "signer_workflow": selection["expected"]["repository"] + "/.github/workflows/release.yml",
        "subjects": subjects,
    }


@pytest.mark.parametrize(
    "fault", [None, "source", "workflow", "subject", "unverified", "failed", "contradictory"]
)
@pytest.mark.parametrize("include_artifact_check", [True, False])
def test_provenance_receipt_is_bound_to_every_recomputed_subject(
    candidate, tmp_path: Path, fault, include_artifact_check: bool
) -> None:
    """A supplied provenance label can't bypass source/workflow or complete subject matching."""
    path, selection, assets = candidate
    if not include_artifact_check:
        selection["required_checks"] = []
    selection["required_checks"].append({"id": "provenance", "kind": "provenance"})
    path.write_text(json.dumps(selection))
    receipt = provenance_receipt(selection, assets)
    if fault == "source":
        receipt["source_revision"] = "f" * 40
    elif fault == "workflow":
        receipt["signer_workflow"] = "other/repo/.github/workflows/release.yml"
    elif fault == "subject":
        receipt["subjects"].pop()
    elif fault == "unverified":
        receipt["subjects"][0]["verified"] = False
    elif fault == "failed":
        receipt.update(status="failed", failure="attestation_rejected")
    elif fault == "contradictory":
        receipt["failure"] = "attestation_rejected"
    receipt_path = tmp_path / "provenance.json"
    receipt_path.write_text(json.dumps(receipt))
    process, report = run_collector(path, assets, "--provenance-receipt", str(receipt_path))
    assert process.returncode == (0 if fault is None else 1)
    result = report["checks"][-1]
    assert result["producer_authenticated"] is False
    if fault == "failed":
        assert (
            result["recorded_status"] == "failed"
            and result["recorded_failure"] == "attestation_rejected"
        )


@pytest.mark.parametrize(
    "raw", ['{"schema_version":"1","schema_version":"1"}', '{"invalid": NaN}', "[]", "{}"]
)
def test_invalid_selection_preserves_an_existing_report(
    candidate, tmp_path: Path, raw: str
) -> None:
    """Selection parse/shape failures never replace a prior reviewed report."""
    path, _, assets = candidate
    path.write_text(raw)
    output = tmp_path / "report.json"
    output.write_text("prior reviewed report")
    process, report = run_collector(path, assets, "--output", str(output))
    assert process.returncode == 2 and report["reason"] == "invalid_selection"
    assert output.read_text() == "prior reviewed report"


def test_examples_match_the_committed_versioned_schemas() -> None:
    """Examples validate through actual models and schemas can't silently drift."""
    script = """
import json
import sys
from pathlib import Path
sys.path.insert(0, 'scripts')
from collect_release_evidence import Selection, ExecutionReceipt, ClientDeclaration
for model, name in [
    (Selection, 'release-evidence-selection'),
    (ExecutionReceipt, 'release-execution-receipt'),
    (ClientDeclaration, 'client-declaration'),
]:
    model.model_validate(json.loads(
        Path('docs/examples/' + name + '.example.json').read_text()
    ))
    assert model.model_json_schema() == json.loads(
        Path('docs/' + name + '.schema.json').read_text()
    )
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 0, process.stderr


def test_failed_gate_replaces_report_with_complete_current_hold(candidate, tmp_path: Path) -> None:
    """Current failed evidence is retained instead of silently reusing an older passing report."""
    path, selection, assets = candidate
    output = tmp_path / "report.json"
    process, _ = run_collector(path, assets, "--output", str(output))
    assert process.returncode == 0
    selection["required_checks"].append(client_requirement())
    path.write_text(json.dumps(selection))
    process, report = run_collector(path, assets, "--output", str(output))
    assert process.returncode == 1 and json.loads(output.read_text()) == report
    assert report["decision"] == "hold"


def test_public_cli_output_failure_preserves_unowned_target(candidate, tmp_path: Path) -> None:
    """The actual CLI rejects a linked output and returns a bounded recovery result."""
    path, _, assets = candidate
    target = tmp_path / "manual.json"
    target.write_text("manual report")
    output = tmp_path / "linked.json"
    try:
        output.symlink_to(target)
    except OSError:
        pytest.skip("Creating test symlinks isn't supported here.")
    process, report = run_collector(path, assets, "--output", str(output))
    assert process.returncode == 2 and report["output_failure"] == "ValueError"
    assert target.read_text() == "manual report"
    assert "Inspect the selected output directory" in report["output_recovery"]


@pytest.mark.parametrize(
    "payload", [b"{", b"x" * (1024 * 1024 + 1)], ids=["malformed", "oversized"]
)
def test_malformed_or_oversized_receipt_cant_erase_prior_facts(
    candidate, tmp_path: Path, payload: bytes
) -> None:
    """Bad supplied input holds the aggregate while valid artifact observations remain visible."""
    path, _, assets = candidate
    receipt = tmp_path / "bad.json"
    receipt.write_bytes(payload)
    process, report = run_collector(path, assets, "--execution-receipt", str(receipt))
    assert process.returncode == 1 and report["input_errors"]
    assert report["checks"][0]["status"] == "passed"


def test_deployment_context_requires_exact_selected_source(candidate) -> None:
    """A deployment ID can't point at an unrelated application revision in the selection."""
    path, selection, assets = candidate
    selection["required_checks"].append(
        {
            "id": "production",
            "kind": "deployment",
            "context": {
                "provider": "railway",
                "project_id": "fixture-project",
                "environment_id": "fixture-env",
                "service_id": "fixture-service",
                "deployment_id": "fixture-deployment",
                "application_source_sha": "f" * 40,
            },
            "cases": ["identity", "inventory", "auth"],
        }
    )
    path.write_text(json.dumps(selection))
    process, report = run_collector(path, assets)
    assert process.returncode == 2 and report["reason"] == "invalid_selection"


def test_published_schema_rejects_empty_execution_requirements(candidate) -> None:
    """A schema-only producer gets the same empty-execution rejection as the collector."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

    _, selection, _ = candidate
    schema = json.loads((ROOT / "docs/release-evidence-selection.schema.json").read_text())
    requirement = client_requirement()
    requirement["cases"] = []
    selection["required_checks"].append(requirement)
    with pytest.raises(JSONSchemaValidationError):
        Draft202012Validator(schema).validate(selection)


def test_execution_only_cli_doesnt_hash_unselected_artifacts(candidate, tmp_path: Path) -> None:
    """Supplying an asset directory doesn't expand an execution-only selection's work."""
    path, selection, assets = candidate
    selection["required_checks"] = [client_requirement()]
    path.write_text(json.dumps(selection))
    receipt = tmp_path / "client.json"
    receipt.write_text(json.dumps(execution_receipt(selection)))
    script = """
import sys
sys.path.insert(0, 'scripts')
import collect_release_evidence as collector

def reject_unselected_hash(*args, **kwargs):
    '''Fail on actual artifact hashing during the execution-only CLI path.'''
    raise AssertionError('Unselected artifact hashing occurred.')

collector.file_digest = reject_unselected_hash
sys.argv = ['collector', '--selection', sys.argv[1], '--artifact-dir', sys.argv[2],
            '--execution-receipt', sys.argv[3]]
raise SystemExit(collector.main())
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(path), str(assets), str(receipt)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    assert report["decision"] == "evidence_consistent"
    assert [check["kind"] for check in report["checks"]] == ["client"]


def test_artifact_hashing_uses_the_selected_size_bound(candidate) -> None:
    """The CLI's artifact adapter rejects enlarged bytes before hashing their payload."""
    path, _, assets = candidate
    script = """
import sys
sys.path.insert(0, 'scripts')
import collect_release_evidence as collector
original = collector.file_digest

def bounded_digest(path, *, limit=2 * 1024**3):
    '''Observe the actual manifest-to-reader limit without reading oversized data.'''
    assert limit == len(b'fixture artifact'), 'manifest size must bound the reader'
    return original(path, limit=limit)

collector.file_digest = bounded_digest
sys.argv = ['collector', '--selection', sys.argv[1], '--artifact-dir', sys.argv[2]]
raise SystemExit(collector.main())
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(path), str(assets)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize("missing", ["channel", "distribution", "both"])
def test_published_install_needs_selected_channel_and_distribution(candidate, missing) -> None:
    """A named installation case can't stand in for its missing publication target."""
    path, selection, assets = candidate
    context = {"channel": "github-release", "distribution": "wheel"}
    if missing == "both":
        context.clear()
    else:
        context.pop(missing)
    selection["required_checks"].append(
        {"id": "published", "kind": "published_install", "context": context, "cases": ["install"]}
    )
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

    schema = json.loads((ROOT / "docs/release-evidence-selection.schema.json").read_text())
    with pytest.raises(JSONSchemaValidationError):
        Draft202012Validator(schema).validate(selection)
    path.write_text(json.dumps(selection))
    process, report = run_collector(path, assets)
    assert process.returncode == 2 and report["reason"] == "invalid_selection"


def test_success_label_with_recorded_failure_holds(candidate, tmp_path: Path) -> None:
    """Contradictory failure evidence can't be ignored by a passing execution label."""
    path, selection, assets = candidate
    selection["required_checks"].append(client_requirement())
    path.write_text(json.dumps(selection))
    receipt = execution_receipt(selection)
    receipt["failure_class"] = "assertion_failed"
    receipt_path = tmp_path / "contradictory.json"
    receipt_path.write_text(json.dumps(receipt))
    process, report = run_collector(path, assets, "--execution-receipt", str(receipt_path))
    assert process.returncode == 1 and report["checks"][1]["reason"] == "recorded_execution_failed"


@pytest.mark.parametrize("target", ["selection", "artifact", "new_asset_entry"])
def test_output_cant_replace_selected_input_or_mutate_asset_inventory(candidate, target) -> None:
    """Report publication can't invalidate the input bytes it just verified."""
    path, _, assets = candidate
    output = {
        "selection": path,
        "artifact": assets / "fixture.whl",
        "new_asset_entry": assets / "report.json",
    }[target]
    before = output.read_bytes() if output.exists() else None
    process, report = run_collector(path, assets, "--output", str(output))
    assert process.returncode == 2 and report["decision"] == "hold"
    assert (output.read_bytes() if output.exists() else None) == before


@pytest.mark.parametrize(
    "target", [None, "f" * 39, "f" * 41, "F" * 40, "g" * 40, "f" * 40 + "\n", "\n" + "f" * 40]
)
def test_rollback_cli_rejects_omitted_or_malformed_selected_target(
    candidate, tmp_path: Path, target: str | None
) -> None:
    """A passing rollback receipt can't hide an absent or invalid selected revision."""
    path, selection, assets = candidate
    context = {} if target is None else {"rollback_target_source_sha": target}
    selection["required_checks"] = [
        {"id": "rollback", "kind": "rollback", "context": context, "cases": ["restore"]}
    ]
    path.write_text(json.dumps(selection))
    receipt = execution_receipt(selection)
    receipt.update(
        check_id="rollback", context=context, cases=[{"id": "restore", "status": "passed"}]
    )
    receipt_path = tmp_path / "rollback.json"
    receipt_path.write_text(json.dumps(receipt))
    process, report = run_collector(path, assets, "--execution-receipt", str(receipt_path))
    assert process.returncode == 2 and report["reason"] == "invalid_selection"


@pytest.mark.parametrize(
    "target", [None, "f" * 39, "f" * 41, "F" * 40, "g" * 40, "f" * 40 + "\n", "\n" + "f" * 40]
)
def test_rollback_schema_rejects_omitted_or_malformed_selected_target(
    candidate, target: str | None
) -> None:
    """Schema-only producers must name the same lowercase full revision as the CLI."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

    _, selection, _ = candidate
    selection["required_checks"] = [
        {
            "id": "rollback",
            "kind": "rollback",
            "context": {} if target is None else {"rollback_target_source_sha": target},
            "cases": ["restore"],
        }
    ]
    schema = json.loads((ROOT / "docs/release-evidence-selection.schema.json").read_text())
    with pytest.raises(JSONSchemaValidationError):
        Draft202012Validator(schema).validate(selection)


@pytest.mark.parametrize("target", ["a" * 40, "f" * 40])
@pytest.mark.parametrize("receipt_target", ["matching", "changed", "missing"])
def test_rollback_receipt_must_match_the_explicit_selected_target(
    candidate, tmp_path: Path, target: str, receipt_target: str
) -> None:
    """Policy-only rollback may retain source, but its receipt must name that exact target."""
    from jsonschema import Draft202012Validator

    path, selection, assets = candidate
    context = {"rollback_target_source_sha": target}
    selection["required_checks"] = [
        {"id": "rollback", "kind": "rollback", "context": context, "cases": ["restore"]}
    ]
    schema = json.loads((ROOT / "docs/release-evidence-selection.schema.json").read_text())
    Draft202012Validator(schema).validate(selection)
    path.write_text(json.dumps(selection))
    observed_context = (
        context
        if receipt_target == "matching"
        else {"rollback_target_source_sha": "b" * 40}
        if receipt_target == "changed"
        else {}
    )
    receipt = execution_receipt(selection)
    receipt.update(
        check_id="rollback", context=observed_context, cases=[{"id": "restore", "status": "passed"}]
    )
    receipt_path = tmp_path / "rollback.json"
    receipt_path.write_text(json.dumps(receipt))
    process, report = run_collector(path, assets, "--execution-receipt", str(receipt_path))
    if receipt_target == "matching":
        assert process.returncode == 0 and report["decision"] == "evidence_consistent"
        assert report["checks"][0]["producer_authenticated"] is False
    else:
        assert process.returncode == 1 and report["decision"] == "hold"
        assert report["checks"][0]["reason"] == "context_mismatch"
