"""Aggregate selected release evidence offline without executing supplied commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from release_evidence_io import (
    MAX_DOCUMENT_BYTES,
    decode_json,
    read_bytes,
    read_json,
    require_separate_output,
    write_document,
)

Hex = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}$")]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
]
Value = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/@+=-]{0,199}$")
]
Status = Literal["passed", "failed", "skipped", "not_run", "timed_out"]
Kind = Literal[
    "artifacts",
    "provenance",
    "client",
    "deployment",
    "rollback",
    "published_install",
    "ci",
    "optional_extras",
]
CLIENT_CONTEXT = {"client", "client_version", "server_sdk", "protocol", "transport", "auth_mode"}
DEPLOYMENT_CONTEXT = {
    "provider",
    "project_id",
    "environment_id",
    "service_id",
    "deployment_id",
    "application_source_sha",
}
OTHER_CONTEXT = {
    "producer",
    "platform",
    "python_version",
    "distribution",
    "channel",
    "rollback_target_source_sha",
}


CLIENT_VALUES = ("codex", "claude-code")
TRANSPORT_VALUES = ("stdio", "http")
AUTH_VALUES = ("beta_key", "operator_key", "oauth", "local_stdio", "auth_disabled")
CLIENT_CONTEXT_SCHEMA: dict[str, Any] = {
    "required": sorted(CLIENT_CONTEXT),
    "propertyNames": {"enum": sorted(CLIENT_CONTEXT)},
    "properties": {
        "client": {"enum": list(CLIENT_VALUES)},
        "transport": {"enum": list(TRANSPORT_VALUES)},
        "auth_mode": {"enum": list(AUTH_VALUES)},
    },
}


class StrictModel(BaseModel):
    """Reject unknown input fields and keep validation errors free of private values."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Subject(StrictModel):
    """Name the operator's selected release, never an observation inferred from it."""

    repository: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=200)]
    release_tag: Annotated[str, Field(pattern=r"^v[0-9][A-Za-z0-9.+-]{0,63}$")]
    source_sha: Revision
    dependency_lock: Digest
    generation_id: Hex
    catalog_digest: Digest
    public_bundle_digest: Digest
    manifest_sha256: Hex


class ObservedSubject(StrictModel):
    """Retain only validated observed identity fields; absent values remain unavailable."""

    repository: (
        Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=200)] | None
    ) = None
    release_tag: Annotated[str, Field(pattern=r"^v[0-9][A-Za-z0-9.+-]{0,63}$")] | None = None
    source_sha: Revision | None = None
    dependency_lock: Digest | None = None
    generation_id: Hex | None = None
    catalog_digest: Digest | None = None
    public_bundle_digest: Digest | None = None
    manifest_sha256: Hex | None = None


REQUIREMENT_SCHEMA_EXTRA: dict[str, Any] = {
    "allOf": [
        {
            "if": {"properties": {"kind": {"enum": ["artifacts", "provenance"]}}},
            "then": {"properties": {"context": {"maxProperties": 0}, "cases": {"maxItems": 0}}},
            "else": {"required": ["cases"], "properties": {"cases": {"minItems": 1}}},
        },
        {
            "if": {"properties": {"kind": {"const": "client"}}},
            "then": {"required": ["context"], "properties": {"context": CLIENT_CONTEXT_SCHEMA}},
        },
        {
            "if": {"properties": {"kind": {"const": "deployment"}}},
            "then": {
                "required": ["context"],
                "properties": {
                    "context": {
                        "required": sorted(DEPLOYMENT_CONTEXT),
                        "propertyNames": {
                            "enum": sorted(DEPLOYMENT_CONTEXT | {"image_digest", "auth_mode"})
                        },
                    }
                },
            },
        },
        {
            "if": {"properties": {"kind": {"const": "published_install"}}},
            "then": {
                "required": ["context"],
                "properties": {"context": {"required": ["channel", "distribution"]}},
            },
        },
        {
            "if": {
                "properties": {
                    "kind": {"enum": ["rollback", "published_install", "ci", "optional_extras"]}
                }
            },
            "then": {"properties": {"context": {"propertyNames": {"enum": sorted(OTHER_CONTEXT)}}}},
        },
    ]
}


class Requirement(StrictModel):
    """Select a scoped check and its explicit context and nonempty execution cases."""

    model_config = ConfigDict(json_schema_extra=REQUIREMENT_SCHEMA_EXTRA)

    id: Identifier
    kind: Kind
    context: dict[Identifier, Value] = Field(default_factory=dict, max_length=16)
    cases: list[Identifier] = Field(
        default_factory=list, max_length=64, json_schema_extra={"uniqueItems": True}
    )

    @model_validator(mode="after")
    def validate_scope(self) -> Requirement:
        """Keep empty requirements and vague client/deployment identities from passing."""
        if len(set(self.cases)) != len(self.cases):
            raise ValueError("Duplicate case IDs.")
        if self.kind in {"artifacts", "provenance"}:
            if self.context or self.cases:
                raise ValueError("Artifact/provenance requirements don't take execution cases.")
            return self
        if not self.cases:
            raise ValueError("Execution requirements need named cases.")
        required = (
            CLIENT_CONTEXT
            if self.kind == "client"
            else DEPLOYMENT_CONTEXT
            if self.kind == "deployment"
            else {"channel", "distribution"}
            if self.kind == "published_install"
            else set()
        )
        allowed = required if self.kind in {"client", "deployment"} else OTHER_CONTEXT
        if self.kind == "deployment":
            allowed = allowed | {"image_digest", "auth_mode"}
        if not required <= self.context.keys() or self.context.keys() - allowed:
            raise ValueError("Required context is missing or unsupported.")
        if self.kind == "client":
            if self.context["client"] not in CLIENT_VALUES or self.context["transport"] not in {
                "stdio",
                "http",
            }:
                raise ValueError("Unsupported client or transport.")
            if self.context["auth_mode"] not in AUTH_VALUES:
                raise ValueError("Unsupported authentication mode.")
        return self


class Selection(StrictModel):
    """Bind all requested gates to one explicit expected release subject."""

    schema_version: Literal["1"]
    expected: Subject
    required_checks: list[Requirement] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_checks(self) -> Selection:
        """Reject duplicate check IDs and incompatible selected deployment source."""
        if len({check.id for check in self.required_checks}) != len(self.required_checks):
            raise ValueError("Duplicate check IDs.")
        for check in self.required_checks:
            if (
                check.kind == "deployment"
                and check.context["application_source_sha"] != self.expected.source_sha
            ):
                raise ValueError("Selected deployment source differs from release.")
        return self


class CaseResult(StrictModel):
    """Retain one named recorded outcome without response bodies or query content."""

    id: Identifier
    status: Status


class ExecutionReceipt(StrictModel):
    """Accept recorded results as supplied evidence, without authenticating their producer."""

    schema_version: Literal["1"]
    evidence_type: Literal["execution_receipt"]
    check_id: Identifier
    producer: Identifier
    producer_version: Value
    status: Status
    exit_code: int | None = Field(default=None, ge=-255, le=255)
    observed: ObservedSubject = Field(default_factory=ObservedSubject)
    context: dict[Identifier, Value] = Field(default_factory=dict, max_length=16)
    cases: list[CaseResult] = Field(default_factory=list, max_length=64)
    reference: str | None = Field(default=None, max_length=512)
    failure_class: Identifier | None = None

    @model_validator(mode="after")
    def validate_record(self) -> ExecutionReceipt:
        """Bound observed identity fields and reject secret-bearing reference components."""
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case results.")
        validate_reference(self.reference)
        return self


class ClientDeclaration(StrictModel):
    """Keep entered client information outside execution acceptance."""

    schema_version: Literal["1"]
    evidence_type: Literal["operator_declaration"]
    context: dict[Identifier, Value] = Field(max_length=6, json_schema_extra=CLIENT_CONTEXT_SCHEMA)
    sanitized: Literal[True]
    reference: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_declaration(self) -> ClientDeclaration:
        """Apply the same client context shape without manufacturing a successful run."""
        Requirement(id="declaration", kind="client", context=self.context, cases=["declared"])
        validate_reference(self.reference)
        return self


def validate_reference(reference: str | None) -> None:
    """Permit a sanitized HTTPS reference without credentials, query or fragment."""
    if reference is None:
        return
    parsed = urlsplit(reference)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Reference must be sanitized HTTPS.")


def file_digest(path: Path, *, limit: int = 2 * 1024**3) -> tuple[str, int]:
    """Hash a bounded regular artifact in chunks, rejecting changed file identity."""
    import os
    import stat

    if path.is_symlink():
        raise ValueError("Artifact links aren't accepted.")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("Artifact isn't a bounded regular file.")
        checksum = hashlib.sha256()
        remaining = limit + 1
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                break
            checksum.update(block)
            remaining -= len(block)
        after = os.fstat(stream.fileno())
        if remaining == 0 or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("Artifact changed or exceeded its limit.")
        return checksum.hexdigest(), before.st_size


def artifact_facts(directory: Path | None, expected: Subject) -> dict[str, Any]:
    """Recompute local bytes against the selected manifest without rebuilding artifacts."""
    result: dict[str, Any] = {
        "status": "failed",
        "reason": "artifact_mismatch",
        "basis": "recomputed_artifact_bytes",
        "observed": {name: None for name in Subject.model_fields},
    }
    if directory is None:
        result["reason"] = "missing_artifact_directory"
        return result
    try:
        raw = read_bytes(directory / "RELEASE_MANIFEST.json")
        manifest = decode_json(raw)
        if not isinstance(manifest, dict) or manifest.get("schema_version") != "1":
            raise ValueError("Invalid manifest.")
        observed = ObservedSubject.model_validate(
            {
                **{name: manifest.get(name) for name in Subject.model_fields},
                "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            }
        ).model_dump()
        result["observed"] = observed
        selected = expected.model_dump()
        if any(observed[name] != value for name, value in selected.items() if name != "repository"):
            raise ValueError("Manifest identity differs.")
        entries = manifest.get("assets")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
            raise ValueError("Invalid asset set.")
        checksums: dict[str, str] = {}
        total_size = 0
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"name", "sha256", "size_bytes"}:
                raise ValueError("Invalid asset entry.")
            name = entry["name"]
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,199}", name)
                or name in {"SHA256SUMS", "RELEASE_MANIFEST.json"}
                or name in checksums
            ):
                raise ValueError("Invalid asset name.")
            if type(entry["size_bytes"]) is not int or not 1 <= entry["size_bytes"] <= 2 * 1024**3:
                raise ValueError("Invalid asset size.")
            total_size += entry["size_bytes"]
            if total_size > 8 * 1024**3:
                raise ValueError("Asset set exceeds its limit.")
            checksum, size = file_digest(directory / name, limit=entry["size_bytes"])
            if checksum != entry["sha256"] or size != entry["size_bytes"]:
                raise ValueError("Asset bytes differ.")
            checksums[name] = checksum
        checksums["RELEASE_MANIFEST.json"] = observed["manifest_sha256"]
        actual: dict[str, str] = {}
        for line in read_bytes(directory / "SHA256SUMS").decode("ascii").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.+-]+)", line)
            if match is None or match[2] in actual:
                raise ValueError("Invalid checksums.")
            actual[match[2]] = match[1]
        expected_names = set(checksums) | {"SHA256SUMS"}
        observed_names: set[str] = set()
        with os.scandir(directory) as contents:
            for item in contents:
                if item.name not in expected_names:
                    raise ValueError("Asset inventory differs.")
                observed_names.add(item.name)
        if actual != checksums or observed_names != expected_names:
            raise ValueError("Asset inventory differs.")
        result.update(status="passed", reason=None, asset_sha256=checksums)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        pass
    return result


def provenance_result(
    path: Path | None, expected: Subject, artifacts: dict[str, Any]
) -> dict[str, Any]:
    """Join a supplied #145 verifier receipt with recomputed artifact subjects."""
    result: dict[str, Any] = {
        "status": "failed",
        "reason": "missing_provenance_receipt",
        "basis": "supplied_provenance_receipt",
        "producer_authenticated": False,
        "observed": {},
    }
    if path is None:
        return result
    try:
        raw = read_bytes(path)
        receipt = decode_json(raw)
        if not isinstance(receipt, dict):
            raise ValueError("Provenance receipt must be an object.")
        result["recorded_status"] = (
            receipt.get("status")
            if receipt.get("status") in {"passed", "failed", "working"}
            else "invalid"
        )
        failure = receipt.get("failure")
        result["recorded_failure"] = (
            failure
            if isinstance(failure, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", failure)
            else None
        )
        result["observed"] = ObservedSubject.model_validate(
            {
                "repository": receipt.get("repository"),
                "source_sha": receipt.get("source_revision"),
            }
        ).model_dump()
        result["receipt_sha256"] = hashlib.sha256(raw).hexdigest()
        if (
            artifacts["status"] != "passed"
            or receipt.get("status") != "passed"
            or receipt.get("failure") is not None
            or receipt.get("repository") != expected.repository
            or receipt.get("source_revision") != expected.source_sha
            or receipt.get("signer_workflow")
            != expected.repository + "/.github/workflows/release.yml"
        ):
            raise ValueError("Provenance identity or status differs.")
        subjects = receipt.get("subjects")
        if not isinstance(subjects, list) or len(subjects) != len(artifacts["asset_sha256"]):
            raise ValueError("Provenance subjects differ.")
        verified: dict[str, str] = {}
        for subject in subjects:
            if subject.get("verified") is not True or subject.get("name") in verified:
                raise ValueError("Subject wasn't verified.")
            verified[subject["name"]] = subject["sha256"]
        if verified != artifacts["asset_sha256"]:
            raise ValueError("Provenance hashes differ.")
        result.update(status="passed", reason=None)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        result["reason"] = "provenance_mismatch"
    return result


def recorded_result(
    requirement: Requirement, receipt: ExecutionReceipt | None, expected: Subject
) -> dict[str, Any]:
    """Validate recorded contents without upgrading them into independently witnessed execution."""
    result: dict[str, Any] = {
        "status": "failed",
        "reason": "missing_execution_receipt",
        "basis": "supplied_execution_receipt",
        "producer_authenticated": False,
        "observed": {},
    }
    if receipt is None:
        return result
    result.update(
        observed=receipt.observed.model_dump(),
        context=receipt.context,
        producer=receipt.producer,
        producer_version=receipt.producer_version,
        recorded_status=receipt.status,
        exit_code=receipt.exit_code,
        cases=[case.model_dump() for case in receipt.cases],
        reference=receipt.reference,
        failure_class=receipt.failure_class,
    )
    if receipt.observed.model_dump() != expected.model_dump():
        result["reason"] = "identity_mismatch"
    elif receipt.context != requirement.context:
        result["reason"] = "context_mismatch"
    elif receipt.status != "passed" or receipt.exit_code != 0 or receipt.failure_class is not None:
        result["reason"] = "recorded_execution_failed"
    elif {case.id for case in receipt.cases} != set(requirement.cases) or any(
        case.status != "passed" for case in receipt.cases
    ):
        result["reason"] = "incomplete_execution_cases"
    elif receipt.reference is None:
        result["reason"] = "missing_execution_reference"
    else:
        result.update(status="passed", reason=None)
    return result


def collect(
    selection: Selection,
    *,
    artifact_dir: Path | None,
    provenance: Path | None,
    receipts: list[Path],
    declarations: list[Path],
) -> dict[str, Any]:
    """Evaluate only selected scopes and retain bounded input failures instead of dropping them."""
    report: dict[str, Any] = {
        "schema_version": "1",
        "decision": "hold",
        "expected": selection.model_dump(),
        "checks": [],
        "declarations": [],
        "input_errors": [],
    }
    recorded: dict[str, ExecutionReceipt] = {}
    receipt_digests: dict[str, str] = {}
    executable_ids = {
        check.id
        for check in selection.required_checks
        if check.kind not in {"artifacts", "provenance"}
    }
    for index, path in enumerate(receipts):
        try:
            raw = read_bytes(path)
            receipt = ExecutionReceipt.model_validate(decode_json(raw))
            if receipt.check_id in recorded or receipt.check_id not in executable_ids:
                raise ValueError("Duplicate or unselected execution receipt.")
            recorded[receipt.check_id] = receipt
            receipt_digests[receipt.check_id] = hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            report["input_errors"].append(
                {"input": f"receipt-{index + 1}", "error_class": type(exc).__name__}
            )
    for index, path in enumerate(declarations):
        try:
            raw = read_bytes(path)
            declaration = ClientDeclaration.model_validate(decode_json(raw))
            report["declarations"].append(
                {
                    "status": "declared_only",
                    "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                    "basis": "operator_declaration",
                    "context": declaration.context,
                    "reference": declaration.reference,
                }
            )
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            report["input_errors"].append(
                {"input": f"declaration-{index + 1}", "error_class": type(exc).__name__}
            )
    artifacts = artifact_facts(artifact_dir, selection.expected)
    for requirement in selection.required_checks:
        if requirement.kind == "artifacts":
            result = artifacts.copy()
        elif requirement.kind == "provenance":
            result = provenance_result(provenance, selection.expected, artifacts)
        else:
            result = recorded_result(requirement, recorded.get(requirement.id), selection.expected)
        if requirement.id in receipt_digests:
            result["receipt_sha256"] = receipt_digests[requirement.id]
        report["checks"].append({"id": requirement.id, "kind": requirement.kind, **result})
    if not report["input_errors"] and all(
        check["status"] == "passed" for check in report["checks"]
    ):
        report["decision"] = "evidence_consistent"
    return report


def main() -> int:
    """Write one complete report; exit nonzero for held gates or failed report replacement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--provenance-receipt", type=Path)
    parser.add_argument("--execution-receipt", type=Path, action="append", default=[])
    parser.add_argument("--client-declaration", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if len(args.execution_receipt) + len(args.client_declaration) > 32:
        parser.error("At most 32 receipt/declaration inputs are supported.")
    try:
        selection = Selection.model_validate(read_json(args.selection))
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "1",
                    "decision": "hold",
                    "reason": "invalid_selection",
                    "error_class": type(exc).__name__,
                    "recovery": (
                        "Check the selection schema, identity fields and required context/cases."
                    ),
                }
            )
        )
        return 2
    report = collect(
        selection,
        artifact_dir=args.artifact_dir,
        provenance=args.provenance_receipt,
        receipts=args.execution_receipt,
        declarations=args.client_declaration,
    )
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    if len(payload) > MAX_DOCUMENT_BYTES:
        print(
            json.dumps({"schema_version": "1", "decision": "hold", "reason": "report_size_limit"})
        )
        return 2
    if args.output is not None:
        try:
            inputs = [args.selection, *args.execution_receipt, *args.client_declaration]
            if args.provenance_receipt is not None:
                inputs.append(args.provenance_receipt)
            require_separate_output(args.output, inputs, protected_directory=args.artifact_dir)
            write_document(args.output, payload)
        except (OSError, ValueError) as exc:
            report.update(
                decision="hold",
                output_failure=type(exc).__name__,
                output_recovery=(
                    "Inspect the selected output directory, permissions and owned "
                    ".policynim-evidence-*.tmp files before retrying. "
                    "Keep output separate from inputs and release assets."
                ),
            )
            print(json.dumps(report, sort_keys=True))
            return 2
    print(payload.decode(), end="")
    return 0 if report["decision"] == "evidence_consistent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
