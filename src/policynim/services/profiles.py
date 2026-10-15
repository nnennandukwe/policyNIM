"""Pinned local pack composition and reviewable, provider-free profile updates."""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal, Self

import tomlkit
from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator
from tomlkit.items import AoT, Array, InlineTable, String, Table

from policynim.errors import InvalidPolicyDocumentError
from policynim.ingest.catalog import load_pack_manifest
from policynim.ingest.loader import load_policy_documents
from policynim.services.catalog import (
    CatalogPack,
    CatalogPolicy,
    PolicyCatalog,
    compile_policy_catalog,
)
from policynim.storage.file_lock import exclusive_file_lock
from policynim.types import ParsedDocument, StrictModel

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Name = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PackPin(StrictModel):
    """An explicitly selected local pack and its reviewed source snapshot."""

    path: Text
    pack_id: Name
    version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    digest: Digest
    policies: dict[str, Digest] = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def local_path_only(cls, value: str) -> str:
        """Normalize native local paths while rejecting network shares and remote schemes."""
        try:
            return _normalize_pack_path(value, windows=os.name == "nt")
        except InvalidPolicyDocumentError as exc:
            raise ValueError(str(exc)) from exc


def _normalize_pack_path(value: str, *, windows: bool) -> str:
    """Reject network/device prefixes before any path resolution can access a share."""
    normalized = value.replace("\\", "/")
    invalid = normalized.startswith("//") or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    )
    if windows:
        drive = PureWindowsPath(value)
        invalid = invalid or (
            ":" in value and not (re.fullmatch(r"[A-Za-z]:", drive.drive) and drive.is_absolute())
        )
    else:
        invalid = invalid or ":" in value or "\\" in value
    if invalid:
        raise InvalidPolicyDocumentError(
            "Pack paths must name local directories; "
            "network shares and remote schemes aren't allowed."
        )
    return normalized


class Exclusion(StrictModel):
    """A reviewed exclusion bound to the target's authored bytes."""

    target_id: Text
    expected_digest: Digest
    reason: Text


class Replacement(Exclusion):
    """A distinct selected policy that deliberately replaces one target."""

    replacement_id: Text


class DeclaredConflict(StrictModel):
    """An operator-declared pair that mustn't both remain effective."""

    left: Text
    right: Text
    reason: Text


class PolicyProfile(StrictModel):
    """Git-friendly local inputs with no implicit precedence or remote fetching."""

    schema_version: Literal[1]
    profile_id: Name
    packs: list[PackPin] = Field(min_length=1)
    exclusions: list[Exclusion] = Field(default_factory=list)
    replacements: list[Replacement] = Field(default_factory=list)
    conflicts: list[DeclaredConflict] = Field(default_factory=list)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema(cls, value: object) -> object:
        """Reject boolean and string schema lookalikes."""
        if type(value) is not int:
            raise ValueError("schema_version must be the integer 1.")
        return value

    @model_validator(mode="after")
    def unique_pack_ids(self) -> Self:
        """Reject repeated pack identities before opening their files."""
        if len({pin.pack_id for pin in self.packs}) != len(self.packs):
            raise ValueError("Each pack_id must appear once.")
        return self


@dataclass
class ResolvedProfile:
    """One validated effective snapshot reused by inspection and ingestion."""

    catalog: PolicyCatalog
    documents: list[ParsedDocument]
    roots: list[Path]


def _digest(value: str) -> str:
    """Hash canonical source or configuration text with an explicit algorithm."""
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def read_profile(path: Path) -> tuple[PolicyProfile, str]:
    """Read strict TOML without echoing potentially private validation inputs."""
    try:
        text = path.read_text(encoding="utf-8")
        return PolicyProfile.model_validate(tomllib.loads(text)), text
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise InvalidPolicyDocumentError(
            "Couldn't read profile TOML. Check the profile path, permissions, and TOML syntax."
        ) from exc
    except ValidationError as exc:
        locations = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        raise InvalidPolicyDocumentError(
            f"Invalid profile fields: {locations}. Run `policynim profile init --help`."
        ) from exc


def _load_pack(root: Path) -> tuple[PackPin, list[ParsedDocument], list[CatalogPolicy]]:
    """Load only explicit manifest members and pin their normalized source snapshot."""
    manifest = load_pack_manifest(root)
    if manifest is None:
        raise InvalidPolicyDocumentError(
            "Selected packs require pack.toml; legacy directories use POLICYNIM_CORPUS_DIR instead."
        )
    documents = load_policy_documents(root)
    catalog = compile_policy_catalog(documents, root=root)
    pin = PackPin(
        path=root.as_posix(),
        pack_id=manifest.pack_id,
        version=manifest.version,
        digest=catalog.digest,
        policies={
            entry.policy.policy_id: _digest(entry.model_dump_json()) for entry in catalog.policies
        },
    )
    for document in documents:
        provenance = document.metadata.provenance
        if provenance is None or (provenance.pack_id, provenance.pack_version) != (
            pin.pack_id,
            pin.version,
        ):
            raise InvalidPolicyDocumentError(
                "Pack identity changed while reading; retry validation."
            )
    # Namespaced source paths stay stable when the pack directory moves.
    documents = [
        document.model_copy(update={"source_path": f"{pin.pack_id}/{entry.path}"})
        for document, entry in _match_entries(documents, catalog.policies)
    ]
    entries = [
        entry.model_copy(update={"path": f"{pin.pack_id}/{entry.path}"})
        for entry in catalog.policies
    ]
    return pin, documents, entries


def _match_entries(
    documents: Sequence[ParsedDocument], entries: Sequence[CatalogPolicy]
) -> list[tuple[ParsedDocument, CatalogPolicy]]:
    """Match documents by identity rather than filesystem or catalog sorting order."""
    by_id = {entry.policy.policy_id: entry for entry in entries}
    return [(document, by_id[document.metadata.policy_id]) for document in documents]


def _current_inputs(
    profile: PolicyProfile, path: Path, *, enforce_pins: bool
) -> tuple[PolicyProfile, list[ParsedDocument], list[CatalogPolicy], list[Path]]:
    """Load selected inputs only; reject changes before exposing effective documents."""
    pins: list[PackPin] = []
    documents: list[ParsedDocument] = []
    entries: list[CatalogPolicy] = []
    roots: list[Path] = []
    for expected in profile.packs:
        root = (path.parent.resolve() / expected.path).resolve()
        if root in roots:
            raise InvalidPolicyDocumentError("A local pack directory was selected more than once.")
        current, loaded, catalog_entries = _load_pack(root)
        current = current.model_copy(update={"path": expected.path})
        if current.pack_id != expected.pack_id:
            raise InvalidPolicyDocumentError(
                "Selected pack_id changed; review the profile selection."
            )
        if enforce_pins and current != expected:
            raise InvalidPolicyDocumentError(
                f"Pack {expected.pack_id} doesn't match its pin. Run `policynim profile diff` "
                "and review the update before `policynim profile update`."
            )
        pins.append(current)
        documents.extend(loaded)
        entries.extend(catalog_entries)
        roots.append(root)
    return profile.model_copy(update={"packs": pins}), documents, entries, roots


def _apply_decisions(
    profile: PolicyProfile, documents: list[ParsedDocument], entries: list[CatalogPolicy]
) -> tuple[list[ParsedDocument], list[CatalogPolicy]]:
    """Validate the complete decision graph before filtering any source document."""
    by_id = {entry.policy.policy_id: entry for entry in entries}
    if len(by_id) != len(entries):
        raise InvalidPolicyDocumentError("Duplicate effective policy IDs across selected packs.")
    decisions = [*profile.exclusions, *profile.replacements]
    targets = [decision.target_id for decision in decisions]
    replacements = [decision.replacement_id for decision in profile.replacements]
    if len(set(targets)) != len(targets) or len(set(replacements)) != len(replacements):
        raise InvalidPolicyDocumentError("Contradictory exclusions or multiple replacements.")
    # A replacement must remain active: this also rejects chains and cycles without precedence.
    if set(targets) & set(replacements):
        raise InvalidPolicyDocumentError(
            "Replacement chains, cycles, or excluded replacements "
            "aren't supported; keep replacements active."
        )
    for decision in decisions:
        target = by_id.get(decision.target_id)
        if target is None:
            raise InvalidPolicyDocumentError(f"Unknown target {decision.target_id!r}; review IDs.")
        if target.target_digest != decision.expected_digest:
            raise InvalidPolicyDocumentError(
                f"Stale target digest for {decision.target_id!r}; review its current content "
                "before editing expected_digest."
            )
        if isinstance(decision, Replacement) and decision.replacement_id not in by_id:
            raise InvalidPolicyDocumentError("Unknown replacement ID; select its pack first.")
        by_id[decision.target_id] = target.model_copy(
            update={
                "state": "replaced" if isinstance(decision, Replacement) else "excluded",
                "reason": decision.reason,
                "replacement_id": decision.replacement_id
                if isinstance(decision, Replacement)
                else None,
            }
        )
    active = set(by_id) - set(targets)
    if not active:
        raise InvalidPolicyDocumentError("The profile must retain at least one effective policy.")
    for conflict in profile.conflicts:
        if conflict.left == conflict.right or not {conflict.left, conflict.right} <= set(by_id):
            raise InvalidPolicyDocumentError(
                "Declared conflicts must name two known, distinct IDs."
            )
        if {conflict.left, conflict.right} <= active:
            raise InvalidPolicyDocumentError(
                f"Declared conflict: {conflict.left} / {conflict.right}. "
                "Add a reviewed exclusion or replacement."
            )
    effective = sorted(
        (document for document in documents if document.metadata.policy_id in active),
        key=lambda document: document.metadata.policy_id,
    )
    _check_runtime_conflicts(effective)
    return effective, sorted(by_id.values(), key=lambda entry: entry.policy.policy_id)


def _check_runtime_conflicts(documents: Sequence[ParsedDocument]) -> None:
    """Reject contradictory effects for identical declared runtime matchers."""
    effects: dict[str, str] = {}
    for document in documents:
        for rule in document.runtime_rules:
            matcher = json.dumps(
                [
                    rule.action,
                    sorted(set(rule.path_globs)),
                    sorted(set(rule.command_regexes)),
                    sorted({pattern.lower() for pattern in rule.url_host_patterns}),
                ]
            )
            if matcher in effects and effects[matcher] != rule.effect:
                raise InvalidPolicyDocumentError(
                    "Conflicting runtime effects for identical matchers; review selected policies."
                )
            effects[matcher] = rule.effect


def resolve_profile(path: Path) -> ResolvedProfile:
    """Resolve a pinned profile before chunking, compilation, or embedding."""
    profile, _ = read_profile(path)
    current, documents, entries, roots = _current_inputs(profile, path, enforce_pins=True)
    documents, entries = _apply_decisions(current, documents, entries)
    identity = _digest(current.model_dump_json())
    catalog = PolicyCatalog(
        digest=_digest(identity + "\n" + "\n".join(entry.model_dump_json() for entry in entries)),
        profile_id=current.profile_id,
        profile_digest=identity,
        pack_pins=[CatalogPack(**pin.model_dump(exclude={"policies"})) for pin in current.packs],
        authored_count=len(entries),
        effective_count=len(documents),
        policies=entries,
    )
    return ResolvedProfile(catalog=catalog, documents=documents, roots=roots)


def initialize_profile(path: Path, roots: Sequence[Path], *, profile_id: str) -> PolicyProfile:
    """Create a new pinned profile without overwriting any existing file."""
    pins = []
    for root in roots:
        root = Path(_normalize_pack_path(root.as_posix(), windows=os.name == "nt"))
        pin, _, _ = _load_pack(root.resolve())
        try:
            selected_path = Path(os.path.relpath(root.resolve(), path.parent.resolve())).as_posix()
        except ValueError:
            # Windows can't express a relative path across drive roots.
            selected_path = root.resolve().as_posix()
        pins.append(pin.model_copy(update={"path": selected_path}))
    try:
        profile = PolicyProfile(schema_version=1, profile_id=profile_id, packs=pins)
    except ValidationError as exc:
        raise InvalidPolicyDocumentError("Invalid profile ID or repeated pack selection.") from exc
    current, documents, entries, _ = _current_inputs(profile, path, enforce_pins=True)
    _apply_decisions(current, documents, entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            staged = Path(handle.name)
            handle.write(serialize_profile(profile))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(staged, path)
    except FileExistsError as exc:
        raise InvalidPolicyDocumentError("Profile already exists; choose a new path.") from exc
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return profile


class ProfileDiff(StrictModel):
    """A review token bound to the old profile and proposed local inputs."""

    profile_id: str
    review_digest: Digest
    added: list[str]
    changed: list[str]
    removed: list[str]
    changed_packs: list[str]
    invalidated_decisions: list[str]
    current_policies: list[CatalogPolicy]
    validation_error: str | None = None


def diff_profile(path: Path) -> tuple[ProfileDiff, PolicyProfile, str]:
    """Compare pinned snapshots with explicit current inputs without adopting them."""
    previous, original = read_profile(path)
    current, documents, entries, _ = _current_inputs(previous, path, enforce_pins=False)
    old_revisions = {
        (pin.pack_id, policy_id): digest
        for pin in previous.packs
        for policy_id, digest in pin.policies.items()
    }
    new_revisions = {
        (pin.pack_id, policy_id): digest
        for pin in current.packs
        for policy_id, digest in pin.policies.items()
    }
    current_digests = {entry.policy.policy_id: entry.target_digest for entry in entries}
    invalidated = sorted(
        decision.target_id
        for decision in [*current.exclusions, *current.replacements]
        if current_digests.get(decision.target_id) != decision.expected_digest
    )
    validation_error = None
    try:
        _apply_decisions(current, documents, entries)
    except InvalidPolicyDocumentError as exc:
        validation_error = str(exc)
    previous_pins = {pin.pack_id: pin for pin in previous.packs}
    report = ProfileDiff(
        profile_id=current.profile_id,
        review_digest=_digest(original + "\n" + current.model_dump_json()),
        added=sorted(policy_id for _, policy_id in new_revisions.keys() - old_revisions.keys()),
        removed=sorted(policy_id for _, policy_id in old_revisions.keys() - new_revisions.keys()),
        changed=sorted(
            policy_id
            for pack_id, policy_id in old_revisions.keys() & new_revisions.keys()
            if old_revisions[pack_id, policy_id] != new_revisions[pack_id, policy_id]
        ),
        changed_packs=sorted(
            pin.pack_id for pin in current.packs if pin != previous_pins.get(pin.pack_id)
        ),
        invalidated_decisions=invalidated,
        current_policies=sorted(entries, key=lambda entry: entry.policy.policy_id),
        validation_error=validation_error,
    )
    return report, current, original


def update_profile(path: Path, *, expected_digest: str) -> ProfileDiff:
    """Serialize cooperating CLI writers before checking review tokens or replacing pins."""
    lock_path = path.with_name(f".{path.name}.lock")
    with exclusive_file_lock(lock_path):
        return _update_profile_locked(path, expected_digest=expected_digest)


def _update_profile_locked(path: Path, *, expected_digest: str) -> ProfileDiff:
    """Repin reviewed inputs atomically; reject stale tokens and invalid decisions."""
    report, current, original = diff_profile(path)
    if report.review_digest != expected_digest:
        raise InvalidPolicyDocumentError("Review digest changed; rerun `policynim profile diff`.")
    if report.validation_error:
        raise InvalidPolicyDocumentError(report.validation_error)
    if path.is_symlink():
        raise InvalidPolicyDocumentError(
            "Profile updates won't replace a symlink; use its real path."
        )
    with path.open("r", encoding="utf-8") as original_handle:
        observed_text = original_handle.read()
        observed_mode = os.fstat(original_handle.fileno()).st_mode & 0o777
    if observed_text != original:
        raise InvalidPolicyDocumentError("Profile changed before staging; rerun the profile diff.")
    staged: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            staged = Path(handle.name)
            handle.write(_update_pin_text(original, current))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, observed_mode)
        # Revalidate inputs and manual edits immediately before replacing the profile.
        final_report, _, final_original = diff_profile(path)
        if final_original != original or final_report.review_digest != expected_digest:
            raise InvalidPolicyDocumentError(
                "Profile or sources changed; rerun `policynim profile diff`."
            )
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return report


def serialize_profile(profile: PolicyProfile) -> str:
    """Render the schema as editable TOML without interpreting user strings as syntax."""
    return tomlkit.dumps(profile.model_dump())


def _update_pin_text(original: str, profile: PolicyProfile) -> str:
    """Edit parsed pin fields while retaining operator comments and decision strings."""
    document = tomlkit.parse(original)
    tables = document["packs"]
    if not isinstance(tables, AoT | Array) or len(tables) != len(profile.packs):
        raise InvalidPolicyDocumentError("Profile pack tables changed; rerun the profile diff.")
    for table, pin in zip(tables, profile.packs, strict=True):
        if not isinstance(table, Table | InlineTable):
            raise InvalidPolicyDocumentError("Profile packs must contain TOML tables.")
        for field in ("path", "version", "digest"):
            table[field] = getattr(pin, field)
        revisions = table["policies"]
        if not isinstance(revisions, Table | InlineTable):
            raise InvalidPolicyDocumentError("Pack policy revisions must form a TOML table.")
        for policy_id in list(revisions):
            if policy_id not in pin.policies:
                removed = revisions.item(policy_id)
                if not isinstance(removed, String):
                    raise InvalidPolicyDocumentError("Policy digest values must be strings.")
                note = removed.trivia.comment
                del revisions[policy_id]
                if isinstance(revisions, Table):
                    revisions.add(
                        tomlkit.comment(
                            f"Removed policy pin: {json.dumps(policy_id)} = "
                            f"{json.dumps(str(removed))}"
                        )
                    )
                    if note:
                        revisions.add(tomlkit.comment(note.removeprefix("#")))
        for policy_id, digest in pin.policies.items():
            revisions[policy_id] = digest
    updated = tomlkit.dumps(document)
    if PolicyProfile.model_validate(tomllib.loads(updated)) != profile:
        raise InvalidPolicyDocumentError(
            "Profile pins couldn't be updated without changing other data."
        )
    return updated
