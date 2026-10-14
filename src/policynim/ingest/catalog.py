"""Strict local pack manifests and bounded Markdown membership."""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from policynim.errors import InvalidPolicyDocumentError
from policynim.types import PolicyProvenance, PolicySource, StrictModel

MANIFEST_NAME = "pack.toml"


class PackPolicy(StrictModel):
    """One explicit file and its declared policy provenance."""

    policy_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    requirement_level: Literal["required", "recommended", "informational", "unknown"] = "unknown"
    applicability: list[str] = Field(default_factory=list)
    grounding_kind: Literal[
        "upstream_requirement", "adaptation", "project_recommendation", "legacy_unknown"
    ] = "legacy_unknown"
    sources: list[PolicySource] = Field(default_factory=list)

    @field_validator("path", "policy_id")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        """Keep filesystem inputs and policy identifiers nonempty and single-line."""
        if not value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Member paths and IDs mustn't be blank or contain control characters.")
        return value

    def provenance(self, *, digest: str, pack: PolicyPackManifest) -> PolicyProvenance:
        """Bind the actual file digest to the manifest's declared metadata."""
        return PolicyProvenance(
            content_digest=digest,
            pack_id=pack.pack_id,
            pack_version=pack.version,
            **self.model_dump(exclude={"policy_id", "path"}),
        )


class PolicyPackManifest(StrictModel):
    """A versioned manifest with no implicit files or ignored behavioral settings."""

    schema_version: Literal[1]
    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    policies: list[PackPolicy] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def reject_non_integer_schema(cls, value: object) -> object:
        """Reject boolean and string lookalikes for the schema version."""
        if type(value) is not int:
            raise ValueError("schema_version must be the integer 1.")
        return value

    @model_validator(mode="after")
    def reject_duplicate_members(self) -> Self:
        """Reject repeated policy IDs or member paths before reading source files."""
        for field in ("policy_id", "path"):
            values = [getattr(policy, field) for policy in self.policies]
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate {field} in pack membership.")
        return self


def load_pack_manifest(root: Path) -> PolicyPackManifest | None:
    """Read a local TOML manifest with actionable locations and no raw input echo."""
    path = root / MANIFEST_NAME
    try:
        if not path.exists() and not path.is_symlink():
            return None
        if not path.resolve().is_relative_to(root.resolve()):
            raise InvalidPolicyDocumentError("pack.toml must stay within the policy root.")
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return PolicyPackManifest.model_validate(data)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidPolicyDocumentError(f"Invalid pack.toml TOML: {exc}") from exc
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'manifest'}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise InvalidPolicyDocumentError(f"Invalid pack.toml: {errors}") from exc
    except (OSError, UnicodeError, RuntimeError) as exc:
        raise InvalidPolicyDocumentError("Couldn't read pack.toml as UTF-8 TOML.") from exc


def resolve_pack_members(root: Path, manifest: PolicyPackManifest) -> list[Path]:
    """Resolve explicit Markdown files without traversal, symlink escape, or aliases."""
    paths: list[Path] = []
    resolved_paths: set[Path] = set()
    for index, member in enumerate(manifest.policies):
        relative = PurePosixPath(member.path)
        path = root / member.path
        try:
            resolved = path.resolve()
            is_file = path.is_file()
            root_resolved = root.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise InvalidPolicyDocumentError(
                f"pack.toml policies.{index}.path couldn't be inspected. "
                "Check paths and permissions."
            ) from exc
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in member.path
            or ":" in member.path
            or relative.suffix != ".md"
            or relative.name == "TEMPLATE.md"
            or not resolved.is_relative_to(root_resolved)
        ):
            raise InvalidPolicyDocumentError(
                f"pack.toml policies.{index}.path must name a Markdown member within the pack."
            )
        if not is_file:
            raise InvalidPolicyDocumentError(f"pack.toml policies.{index}.path doesn't exist.")
        if resolved in resolved_paths:
            raise InvalidPolicyDocumentError(f"pack.toml policies.{index}.path repeats a member.")
        resolved_paths.add(resolved)
        paths.append(path)
    return paths
