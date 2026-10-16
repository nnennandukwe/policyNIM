"""Captured policy bytes that can be validated without their original filesystem paths."""

from __future__ import annotations

import tomllib
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import Field, field_validator

from policynim.errors import InvalidPolicyDocumentError
from policynim.ingest.catalog import (
    PolicyPackManifest,
    normalize_pack_member_path,
    read_pack_bytes,
)
from policynim.ingest.loader import discover_policy_paths, load_policy_documents
from policynim.ingest.parser import MarkdownParser
from policynim.runtime_paths import resolve_corpus_root
from policynim.services.catalog import (
    CatalogPack,
    CatalogPolicy,
    PolicyCatalog,
    compile_policy_catalog,
)
from policynim.services.profiles import (
    PackPin,
    PolicyProfile,
    compile_profile_catalog,
    read_profile,
    resolve_profile,
)
from policynim.settings import Settings
from policynim.types import ParsedDocument, PolicyProvenance, StrictModel

SchemaOne = Annotated[int, Field(strict=True, ge=1, le=1)]


def digest_bytes(value: bytes) -> str:
    """Name exact bytes with an explicit SHA256 algorithm."""
    return "sha256:" + sha256(value).hexdigest()


class SourceMember(StrictModel):
    """One explicitly selected member's original UTF-8 bytes and logical relative path."""

    path: str
    text: str

    @field_validator("path")
    @classmethod
    def bounded_member(cls, value: str) -> str:
        """Keep logical member paths relative; inspection never opens their original names."""
        path = PurePosixPath(value)
        if (
            not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or not path.name.lower().endswith(".md")
            or path.name == "TEMPLATE.md"
        ):
            raise ValueError("Snapshot members must be relative Markdown paths.")
        return path.as_posix()


class SourcePack(StrictModel):
    """Raw selected pack inputs, with notices retained separately from model-visible text."""

    root_name: str
    manifest_text: str | None = None
    members: list[SourceMember] = Field(min_length=1)
    notices: dict[str, str] = Field(default_factory=dict)
    unlisted_markdown: list[str] = Field(default_factory=list)

    def parse(self) -> tuple[list[ParsedDocument], PolicyCatalog, PolicyPackManifest | None]:
        """Reconstruct declared provenance and catalog solely from captured source bytes."""
        manifest = (
            PolicyPackManifest.model_validate(tomllib.loads(self.manifest_text))
            if self.manifest_text is not None
            else None
        )
        paths = [member.path for member in self.members]
        if len(set(paths)) != len(paths):
            raise InvalidPolicyDocumentError("Snapshot membership repeats a file.")
        declarations = (
            {
                normalize_pack_member_path(member.path).as_posix(): member
                for member in manifest.policies
            }
            if manifest
            else {}
        )
        if manifest is not None and len(declarations) != len(manifest.policies):
            raise InvalidPolicyDocumentError("Snapshot manifest repeats a normalized member.")
        if manifest is not None and set(paths) != set(declarations):
            raise InvalidPolicyDocumentError("Snapshot membership doesn't match its manifest.")
        documents: list[ParsedDocument] = []
        parser = MarkdownParser(strict_metadata=manifest is not None)
        for member in sorted(self.members, key=lambda entry: entry.path):
            source_path = f"{self.root_name}/{member.path}" if self.root_name else member.path
            document = parser.parse(source_path, member.text)
            digest = digest_bytes(member.text.encode("utf-8"))
            provenance = PolicyProvenance(content_digest=digest)
            if manifest is not None:
                declaration = declarations[member.path]
                if declaration.policy_id != document.metadata.policy_id:
                    raise InvalidPolicyDocumentError(
                        "Snapshot policy ID differs from its manifest."
                    )
                provenance = declaration.provenance(digest=digest, pack=manifest)
            documents.append(
                document.model_copy(
                    update={
                        "metadata": document.metadata.model_copy(update={"provenance": provenance})
                    }
                )
            )
        ids = [document.metadata.policy_id for document in documents]
        if len(set(ids)) != len(ids):
            raise InvalidPolicyDocumentError("Snapshot policy IDs must be unique.")
        catalog = compile_policy_catalog(
            documents, root=Path(self.root_name), unlisted_markdown=self.unlisted_markdown
        )
        return documents, catalog, manifest


class SourceSnapshot(StrictModel):
    """A self-contained selected source/profile snapshot with no runtime path resolution."""

    schema_version: SchemaOne
    profile: PolicyProfile | None = None
    packs: list[SourcePack] = Field(min_length=1)

    def pack_identities(self) -> list[CatalogPack]:
        """Expose explicit manifest identities even when no composition profile was selected."""
        if self.profile is not None:
            return [
                CatalogPack(**pin.model_dump(exclude={"policies"})) for pin in self.profile.packs
            ]
        result: list[CatalogPack] = []
        for source in self.packs:
            _, catalog, manifest = source.parse()
            if manifest is not None:
                result.append(
                    CatalogPack(
                        path=source.root_name,
                        pack_id=manifest.pack_id,
                        version=manifest.version,
                        digest=catalog.digest,
                    )
                )
        return result

    def resolve(self) -> tuple[list[ParsedDocument], PolicyCatalog]:
        """Revalidate pins and decisions before accepting any generation consumer."""
        if self.profile is None:
            if len(self.packs) != 1:
                raise InvalidPolicyDocumentError("An unprofiled snapshot requires one source root.")
            documents, catalog, _ = self.packs[0].parse()
            return documents, catalog
        if len(self.profile.packs) != len(self.packs):
            raise InvalidPolicyDocumentError("Snapshot packs don't match the selected profile.")
        documents: list[ParsedDocument] = []
        entries: list[CatalogPolicy] = []
        pins: list[PackPin] = []
        for source, expected in zip(self.packs, self.profile.packs, strict=True):
            loaded, catalog, manifest = source.parse()
            if manifest is None:
                raise InvalidPolicyDocumentError("Profile snapshots require explicit manifests.")
            current = PackPin(
                path=expected.path,
                pack_id=manifest.pack_id,
                version=manifest.version,
                digest=catalog.digest,
                policies={
                    entry.policy.policy_id: digest_bytes(entry.model_dump_json().encode())
                    for entry in catalog.policies
                },
            )
            if current != expected:
                raise InvalidPolicyDocumentError("Captured pack doesn't match its reviewed pin.")
            pins.append(current)
            by_id = {entry.policy.policy_id: entry for entry in catalog.policies}
            documents.extend(
                document.model_copy(
                    update={
                        "source_path": (
                            f"{current.pack_id}/{by_id[document.metadata.policy_id].path}"
                        )
                    }
                )
                for document in loaded
            )
            entries.extend(
                entry.model_copy(update={"path": f"{current.pack_id}/{entry.path}"})
                for entry in catalog.policies
            )
        return compile_profile_catalog(
            self.profile.model_copy(update={"packs": pins}), documents, entries
        )


def capture_sources(settings: Settings) -> tuple[SourceSnapshot, list[Path]]:
    """Capture only explicit sources and reject a source change before embedding begins."""
    profile: PolicyProfile | None = None
    if settings.profile is not None:
        resolved = resolve_profile(settings.profile)
        profile, _ = read_profile(settings.profile)
        expected, roots = resolved.catalog, resolved.roots
    else:
        roots = [resolve_corpus_root(settings.corpus_dir).resolve()]
        expected = compile_policy_catalog(load_policy_documents(roots[0]), root=roots[0])
    packs: list[SourcePack] = []
    for root in roots:
        manifest_path = root / "pack.toml"
        manifest_text = (
            read_pack_bytes(root, "pack.toml").decode("utf-8") if manifest_path.exists() else None
        )
        members = [
            SourceMember(
                path=path.relative_to(root).as_posix(),
                text=read_pack_bytes(root, path.relative_to(root).as_posix()).decode("utf-8"),
            )
            for path in discover_policy_paths(root)
        ]
        selected = {member.path for member in members}
        notices = {
            path.relative_to(root).as_posix(): read_pack_bytes(
                root, path.relative_to(root).as_posix()
            ).decode("utf-8")
            for path in sorted((root / "licenses").glob("*.txt"))
        }
        packs.append(
            SourcePack(
                root_name=root.name,
                manifest_text=manifest_text,
                members=members,
                notices=notices,
                unlisted_markdown=sorted(
                    path.relative_to(root).as_posix()
                    for path in root.rglob("*.md")
                    if path.name != "TEMPLATE.md"
                    and path.relative_to(root).as_posix() not in selected
                ),
            )
        )
    snapshot = SourceSnapshot(schema_version=1, profile=profile, packs=packs)
    _, actual = snapshot.resolve()
    if actual != expected:
        raise InvalidPolicyDocumentError("Sources changed while capturing; review and retry.")
    return snapshot, roots
