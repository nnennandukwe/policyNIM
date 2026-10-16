"""Immutable policy generations with one serialized atomic activation pointer."""

from __future__ import annotations

import io
import json
import logging
import os
import re
import sqlite3
import stat
import zipfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp
from types import TracebackType
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from policynim.contracts import Embedder
from policynim.errors import PolicyNIMError
from policynim.ingest.chunking import chunk_policy_documents
from policynim.operations import close_components
from policynim.services.catalog import CatalogPack, PolicyCatalog
from policynim.services.ingest import _attach_embeddings, _compile_runtime_rules_artifact
from policynim.settings import Settings
from policynim.storage.file_lock import exclusive_file_lock, try_exclusive_file_lock
from policynim.storage.generation_sources import (
    SchemaOne,
    SourceSnapshot,
    capture_sources,
    digest_bytes,
)
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import EmbeddingIdentity, ParsedDocument, RuntimeRulesArtifact, StrictModel

GenerationID = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
_ARTIFACTS = {"snapshot.json", "catalog.json", "index.sqlite3", "runtime_rules.json"}
_FILES = _ARTIFACTS | {"manifest.json"}
_OWNER = b'{"owner":"policynim-generations","schema_version":1}\n'
_STAGE_OWNER = b"policynim-generation-stage-v1\n"
LOGGER = logging.getLogger(__name__)


class GenerationError(PolicyNIMError):
    """A bounded generation failure with a phase and source-preserving recovery action."""

    def __init__(self, phase: str, detail: str) -> None:
        """Preserve a safe phase and recovery action without echoing artifact contents."""
        self.phase = phase
        super().__init__(
            f"Generation {phase} failed: {detail} Preserve existing data and run "
            "`policynim generation inspect` before retrying.",
            failure_class=f"generation_{phase}",
        )


class GenerationManifest(StrictModel):
    """Artifact identities and semantic schemas bound by the generation content ID."""

    schema_version: SchemaOne
    index_schema_version: str
    rules_schema_version: SchemaOne
    embedding: EmbeddingIdentity
    dimension: int = Field(strict=True, gt=0)
    document_count: int = Field(strict=True, gt=0)
    chunk_count: int = Field(strict=True, gt=0)
    catalog_digest: Digest
    profile_id: str | None = None
    profile_digest: str | None = None
    pack_pins: list[CatalogPack] = Field(default_factory=list)
    artifacts: dict[str, Digest]


class GenerationPointer(StrictModel):
    """The complete active/previous state changed by exactly one file replacement."""

    schema_version: SchemaOne
    active: GenerationID
    previous: GenerationID | None = None

    @model_validator(mode="after")
    def separate_previous(self) -> GenerationPointer:
        """Reject an ambiguous rollback pointer that repeats the active object."""
        if self.previous == self.active:
            raise ValueError("Previous generation must differ from active generation.")
        return self


class PublicationResult(StrictModel):
    """A committed pointer transition, including any postcommit cleanup warning."""

    generation_id: GenerationID
    manifest: GenerationManifest
    previous_generation_id: GenerationID | None = None
    warnings: list[str] = Field(default_factory=list)
    source_roots: list[str] = Field(default_factory=list)
    profile_path: str | None = None


class RetentionResult(StrictModel):
    """Observed owned-state retention, including preserved entries needing inspection."""

    kept: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TrashRecord(StrictModel):
    """Durable ownership and remaining-file hashes for interrupted retention cleanup."""

    schema_version: SchemaOne
    owner: Literal["policynim-generation-trash"]
    generation_id: GenerationID
    artifacts: dict[str, Digest]


@dataclass
class GenerationSnapshot:
    """One validated immutable object retained for its reader's entire lifetime."""

    generation_id: str
    directory: Path
    manifest: GenerationManifest
    catalog: PolicyCatalog
    documents: list[ParsedDocument]
    _lease: AbstractContextManager[None] | None = field(default=None, repr=False)
    _lease_path: Path | None = field(default=None, repr=False)

    @property
    def index_path(self) -> Path:
        """Return this generation's fixed SQLite artifact."""
        return self.directory / "index.sqlite3"

    @property
    def rules_path(self) -> Path:
        """Return this generation's fixed runtime-rules artifact."""
        return self.directory / "runtime_rules.json"

    def __enter__(self) -> GenerationSnapshot:
        """Retain this generation for the context lifetime."""
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the context-owned reader lease."""
        self.close()

    def close(self) -> None:
        """Release the reader lease once; dead processes also release the kernel lock."""
        lease, self._lease = self._lease, None
        if lease is not None:
            lease.__exit__(None, None, None)
        if self._lease_path is not None:
            try:
                observed = self._lease_path.lstat()
                if stat.S_ISREG(observed.st_mode) and observed.st_size == 0:
                    self._lease_path.unlink()
                else:
                    LOGGER.warning("An unexpected reader-lease entry was preserved for inspection.")
            except FileNotFoundError:
                pass
            except OSError:
                LOGGER.warning("Reader lease released; its stale marker needs retention cleanup.")
            self._lease_path = None


def _json_bytes(model: StrictModel) -> bytes:
    """Serialize canonical UTF-8 JSON for content IDs and portable bundles."""
    return (
        json.dumps(
            model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        + "\n"
    ).encode("utf-8")


def _read_regular(path: Path, *, max_bytes: int | None = None) -> bytes:
    """Reject artifact entry replacement before reading immutable store contents."""
    observed = path.lstat()
    if not stat.S_ISREG(observed.st_mode):
        raise GenerationError("inspect", "Artifacts must be regular files without symlinks.")
    with path.open("rb") as handle:
        if (os.fstat(handle.fileno()).st_dev, os.fstat(handle.fileno()).st_ino) != (
            observed.st_dev,
            observed.st_ino,
        ):
            raise GenerationError("inspect", "An artifact changed while opening it.")
        if max_bytes is not None and os.fstat(handle.fileno()).st_size > max_bytes:
            raise GenerationError("inspect", "An artifact exceeds the configured size limit.")
        data = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
        if max_bytes is not None and len(data) > max_bytes:
            raise GenerationError("inspect", "An artifact grew beyond the configured size limit.")
        return data


def _sync_directory(path: Path) -> None:
    """Sync directory entries on POSIX; Windows claims process-crash consistency only."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validated_runtime_rules(documents: list[ParsedDocument]) -> RuntimeRulesArtifact:
    """Use the real runtime consumer's matcher checks before any embedding or activation."""
    from policynim.services.runtime_decision import _validate_runtime_rule_matchers

    artifact = _compile_runtime_rules_artifact(documents)
    try:
        _validate_runtime_rule_matchers(artifact, artifact_path=Path("runtime_rules.json"))
    except PolicyNIMError as exc:
        raise GenerationError("validate", "Source runtime matchers aren't valid.") from exc
    return artifact


class GenerationStore:
    """Own publication, reader leases, inspection and recovery for one local generation root."""

    def __init__(
        self,
        *,
        root: Path,
        embedding: EmbeddingIdentity,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        """Select one owned root and expected embedding space without provider initialization."""
        self.root = root.absolute()
        self.embedding = embedding
        self._fault = fault

    def _step(self, name: str) -> None:
        """Expose named transaction boundaries to fault and process-termination tests."""
        if self._fault is not None:
            self._fault(name)

    @contextmanager
    def _locked(self, *, initialize: bool = False) -> Iterator[None]:
        """Verify root ownership and serialize supported readers, publishers and retention."""
        if self.root.is_symlink():
            raise GenerationError("open", "The selected generation root can't be a symlink.")
        if initialize:
            self._step("root_mkdir")
            missing: list[Path] = []
            ancestor = self.root
            while not ancestor.exists():
                if ancestor.parent == ancestor:
                    raise GenerationError("open", "The generation root's ancestor isn't available.")
                missing.append(ancestor)
                ancestor = ancestor.parent
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            for directory in missing:
                self._step("root_parent_fsync")
                _sync_directory(directory.parent)
        if not self.root.is_dir():
            raise GenerationError(
                "open",
                "No managed generation exists. Import a verified bundle "
                "or explicitly migrate the standalone index and rules.",
            )
        owner = self.root / "owner.json"
        if not owner.exists() and (
            not initialize or any(path.name != ".publish.lock" for path in self.root.iterdir())
        ):
            raise GenerationError("open", "The directory isn't an owned generation store.")
        with exclusive_file_lock(self.root / ".publish.lock"):
            if not owner.exists():
                temporary = self.root / (".owner-" + uuid4().hex + ".tmp")
                try:
                    self._write(temporary, _OWNER, "owner")
                    self._step("owner_link")
                    os.link(temporary, owner)
                    _sync_directory(self.root)
                finally:
                    temporary.unlink(missing_ok=True)
            if _read_regular(owner) != _OWNER:
                raise GenerationError("open", "Generation-root ownership doesn't match.")
            for name in ("objects", "staging", "leases", "trash"):
                path = self.root / name
                if path.is_symlink():
                    raise GenerationError("open", "Managed directories can't be symlinks.")
                if initialize:
                    path.mkdir(exist_ok=True, mode=0o700)
                if not path.is_dir():
                    raise GenerationError("open", "A managed directory is missing or invalid.")
            if initialize:
                self._step("managed_directories_fsync")
                _sync_directory(self.root)
            yield

    def _pointer(self) -> GenerationPointer | None:
        """Read the sole canonical activation record without inferring missing state."""
        path = self.root / "active.json"
        if not path.exists() and not path.is_symlink():
            return None
        try:
            return GenerationPointer.model_validate_json(_read_regular(path))
        except (ValueError, OSError) as exc:
            raise GenerationError(
                "inspect",
                "The activation pointer isn't valid; preserve it "
                "for diagnosis rather than guessing an active generation.",
            ) from exc

    def _write(self, path: Path, data: bytes, label: str) -> None:
        """Create one owned artifact exclusively and sync its bytes before returning."""
        self._step(label + "_create")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            self._step(label + "_write")
            handle.write(data)
            self._step(label + "_flush")
            handle.flush()
            self._step(label + "_fsync")
            os.fsync(handle.fileno())
            self._step(label + "_close")

    def _inspect(
        self,
        directory: Path,
        *,
        expected_id: str | None = None,
        expected_embedding: EmbeddingIdentity | None = None,
        staging: bool = False,
    ) -> GenerationSnapshot:
        """Prove hashes, schemas, embedding identity and source/catalog/index/rules agreement."""
        self._step("inspect")
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("Not a regular generation directory.")
            names = {path.name for path in directory.iterdir()}
            if staging and ".owner" in names:
                if _read_regular(directory / ".owner") != _STAGE_OWNER:
                    raise ValueError("Stage ownership mismatch.")
                names.remove(".owner")
            if names != _FILES:
                raise ValueError("Missing or unexpected generation artifact.")
            manifest_bytes = _read_regular(directory / "manifest.json")
            generation_id = sha256(manifest_bytes).hexdigest()
            if expected_id is not None and generation_id != expected_id:
                raise ValueError("Generation content ID mismatch.")
            manifest = GenerationManifest.model_validate_json(manifest_bytes)
            if manifest.index_schema_version != "2" or set(manifest.artifacts) != _ARTIFACTS:
                raise ValueError("Unsupported artifact schema or membership.")
            if expected_embedding is not None and manifest.embedding != expected_embedding:
                raise ValueError(
                    "Configured embedding model/endpoint doesn't match this generation."
                )
            contents = {name: _read_regular(directory / name) for name in _ARTIFACTS}
            if any(digest_bytes(contents[name]) != manifest.artifacts[name] for name in _ARTIFACTS):
                raise ValueError("Artifact digest mismatch.")
            snapshot = SourceSnapshot.model_validate_json(contents["snapshot.json"])
            documents, expected_catalog = snapshot.resolve()
            raw_catalog = json.loads(contents["catalog.json"])
            raw_rules = json.loads(contents["runtime_rules.json"])
            for payload in (raw_catalog, raw_rules):
                if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
                    raise ValueError("Unsupported source or rules schema.")
            catalog = PolicyCatalog.model_validate(raw_catalog)
            rules = RuntimeRulesArtifact.model_validate(raw_rules)
            if catalog != expected_catalog or (
                manifest.catalog_digest,
                manifest.profile_id,
                manifest.profile_digest,
                manifest.pack_pins,
            ) != (
                catalog.digest,
                catalog.profile_id,
                catalog.profile_digest,
                snapshot.pack_identities(),
            ):
                raise ValueError("Source/catalog identity mismatch.")
            if rules != _validated_runtime_rules(documents):
                raise ValueError("Runtime rules don't match the effective source snapshot.")
            expected_chunks = chunk_policy_documents(documents)
            if (manifest.document_count, manifest.chunk_count) != (
                len(documents),
                len(expected_chunks),
            ):
                raise ValueError("Manifest inventory doesn't match its effective source snapshot.")
            store = SQLiteVecIndexStore(
                path=directory / "index.sqlite3", embedding_identity=manifest.embedding
            )
            store.validate_identity()
            if store.inspect_identity().dimension != manifest.dimension:
                raise ValueError("Index dimension mismatch.")
            if sorted(store.list_chunks(), key=lambda chunk: chunk.chunk_id) != sorted(
                expected_chunks, key=lambda chunk: chunk.chunk_id
            ):
                raise ValueError("Index chunks don't match the effective source snapshot.")
            return GenerationSnapshot(generation_id, directory, manifest, catalog, documents)
        except GenerationError:
            raise
        except Exception as exc:
            raise GenerationError(
                "inspect",
                "Source, catalog, index, rules, schema or model identity couldn't be validated.",
            ) from exc

    def inspect(self, generation_id: str | None = None) -> GenerationSnapshot:
        """Inspect one retained generation without providers; pin() retains it for ongoing work."""
        with self._locked():
            return self._inspect_id(generation_id)

    def _inspect_id(self, generation_id: str | None = None) -> GenerationSnapshot:
        """Resolve an explicit or active object ID under the publisher lock."""
        pointer = self._pointer()
        selected = (
            generation_id if generation_id is not None else (pointer.active if pointer else None)
        )
        if selected is None:
            raise GenerationError(
                "open",
                "No active generation exists; import a verified bundle "
                "or run explicit generation migration.",
            )
        # Validate explicit IDs before converting them into local artifact paths.
        try:
            selected = GenerationPointer(schema_version=1, active=selected).active
        except ValueError as exc:
            raise GenerationError(
                "open", "A generation ID must contain exactly 64 lowercase hex digits."
            ) from exc
        return self._inspect(
            self.root / "objects" / selected,
            expected_id=selected,
            expected_embedding=self.embedding,
        )

    def pin(self, generation_id: str | None = None) -> GenerationSnapshot:
        """Acquire a unique reader lease while publication and retention are serialized."""
        snapshot: GenerationSnapshot | None = None
        try:
            with self._locked():
                snapshot = self._inspect_id(generation_id)
                directory = self.root / "leases" / snapshot.generation_id
                if directory.is_symlink():
                    raise GenerationError("pin", "The reader lease directory can't be a symlink.")
                directory.mkdir(exist_ok=True, mode=0o700)
                lease_path = directory / (uuid4().hex + ".lock")
                lease = exclusive_file_lock(lease_path)
                lease.__enter__()
                snapshot._lease, snapshot._lease_path = lease, lease_path
            return snapshot
        except BaseException as error:
            close_components(snapshot, primary_error=error)
            raise

    def _execute(self, operation: Callable[[], PublicationResult]) -> PublicationResult:
        """Preserve a committed result if publisher cleanup fails after activation."""
        result: PublicationResult | None = None
        try:
            with self._locked(initialize=True):
                pointer = self._pointer()
                if pointer is not None:
                    self._inspect(
                        self.root / "objects" / pointer.active, expected_id=pointer.active
                    )
                result = operation()
                try:
                    retention = self._prune(keep_recent=2)
                    result.warnings.extend(retention.warnings)
                except Exception:
                    result.warnings.append(
                        "Generation activated; retention cleanup needs inspection."
                    )
        except Exception as exc:
            if result is not None:
                result.warnings.append("Generation activated; publisher cleanup needs inspection.")
                return result
            if isinstance(exc, GenerationError):
                raise
            raise GenerationError(
                "publish",
                "Preparation didn't complete; preserve the prior active state "
                "and inspect owned staging.",
            ) from exc
        return result

    def _guard_outputs(self, roots: list[Path], settings: Settings) -> None:
        """Reject source-overlapping outputs and inconsistent configured embedding identity."""
        if self.embedding != EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        ):
            raise GenerationError(
                "prepare", "Configured embedding settings don't match store identity."
            )
        if any(self.root.resolve().is_relative_to(root.resolve()) for root in roots) or (
            settings.profile is not None and self.root.resolve() == settings.profile.resolve()
        ):
            raise GenerationError("prepare", "Generation outputs must stay outside policy sources.")

    def _seal(self, stage: Path, snapshot: SourceSnapshot) -> GenerationSnapshot:
        """Bind staged artifacts in a manifest and run the production inspector."""
        documents, catalog = snapshot.resolve()
        store = SQLiteVecIndexStore(path=stage / "index.sqlite3", embedding_identity=self.embedding)
        identity = store.inspect_identity()
        manifest = GenerationManifest(
            schema_version=1,
            index_schema_version="2",
            rules_schema_version=1,
            embedding=self.embedding,
            dimension=identity.dimension,
            document_count=len(documents),
            chunk_count=store.count(),
            catalog_digest=catalog.digest,
            profile_id=catalog.profile_id,
            profile_digest=catalog.profile_digest,
            pack_pins=snapshot.pack_identities(),
            artifacts={name: digest_bytes(_read_regular(stage / name)) for name in _ARTIFACTS},
        )
        self._write(stage / "manifest.json", _json_bytes(manifest), "manifest")
        self._step("staging_validate")
        return self._inspect(stage, expected_embedding=self.embedding, staging=True)

    def _remove_stage(self, stage: Path, expected_identity: tuple[int, int] | None = None) -> None:
        """Remove only the observed transaction directory or a verified owned stale stage."""
        if not stage.exists():
            return
        if stage.is_symlink() or not stage.is_dir():
            raise GenerationError("cleanup", "Unexpected staging entry was preserved.")
        names = {path.name for path in stage.iterdir()}
        if not names <= (_FILES | {".owner"}):
            raise GenerationError("cleanup", "Unexpected staging contents were preserved.")
        observed = stage.lstat()
        if expected_identity is not None:
            if (observed.st_dev, observed.st_ino) != expected_identity:
                raise GenerationError("cleanup", "Staging directory identity changed.")
        elif ".owner" in names:
            if _read_regular(stage / ".owner") != _STAGE_OWNER:
                raise GenerationError("cleanup", "Stage ownership doesn't match.")
        else:
            self._inspect(stage)
        for name in names:
            path = stage / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise GenerationError("cleanup", "Unexpected staging file was preserved.")
        for name in names:
            self._step("stage_remove_" + name)
            (stage / name).unlink()
        stage.rmdir()

    def _activate(self, prepared: GenerationSnapshot) -> PublicationResult:
        """Install a complete object before atomically replacing the active/previous pointer."""
        pointer = self._pointer()
        if pointer is not None:
            self._inspect(self.root / "objects" / pointer.active, expected_id=pointer.active)
        target = self.root / "objects" / prepared.generation_id
        if target.exists():
            self._inspect(
                target, expected_id=prepared.generation_id, expected_embedding=self.embedding
            )
        else:
            self._step("object_rename")
            (prepared.directory / ".owner").unlink(missing_ok=True)
            prepared.directory.rename(target)
            self._step("objects_fsync")
            _sync_directory(target.parent)
            self._step("staging_parent_fsync")
            _sync_directory(prepared.directory.parent)
        previous = (
            pointer.active
            if pointer and pointer.active != prepared.generation_id
            else (pointer.previous if pointer else None)
        )
        result = PublicationResult(
            generation_id=prepared.generation_id,
            manifest=prepared.manifest,
            previous_generation_id=previous,
        )
        temporary = self.root / (".active-" + uuid4().hex + ".tmp")
        try:
            self._write(
                temporary,
                _json_bytes(
                    GenerationPointer(
                        schema_version=1, active=prepared.generation_id, previous=previous
                    )
                ),
                "pointer",
            )
            self._step("before_activate")
            if self._pointer() != pointer:
                raise GenerationError(
                    "activate", "Active state changed before commit; inspect and retry."
                )
            self._inspect(
                target, expected_id=prepared.generation_id, expected_embedding=self.embedding
            )
            os.replace(temporary, self.root / "active.json")
            # No operation after this point may report an aborted publication.
            try:
                self._step("after_activate")
                self._step("active_directory_fsync")
                _sync_directory(self.root)
            except Exception:
                result.warnings.append(
                    "Generation activated, but directory sync couldn't finish. "
                    "Inspect active state before retrying."
                )
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                result.warnings.append("Activation pointer staging cleanup needs inspection.")
        return result

    def _publish_stage(self, prepare: Callable[[Path], GenerationSnapshot]) -> PublicationResult:
        """Own preparation, the commit point, and cleanup without false postcommit aborts."""
        stage: Path | None = None
        stage_identity: tuple[int, int] | None = None
        result: PublicationResult | None = None
        try:
            self._step("stage_mkdir")
            stage = Path(mkdtemp(prefix="build-", dir=self.root / "staging"))
            observed = stage.lstat()
            stage_identity = (observed.st_dev, observed.st_ino)
            self._write(stage / ".owner", _STAGE_OWNER, "stage_owner")
            prepared = prepare(stage)
            self._step("stage_directory_fsync")
            _sync_directory(stage)
            result = self._activate(prepared)
            return result
        except GenerationError:
            raise
        except Exception as exc:
            raise GenerationError(
                "publish", "Preparation didn't complete; the prior active pointer is unchanged."
            ) from exc
        finally:
            try:
                if stage is not None:
                    self._remove_stage(stage, stage_identity)
            except Exception:
                if result is not None:
                    result.warnings.append(
                        "Generation activated; owned staging cleanup needs inspection "
                        "before retrying."
                    )
                else:
                    LOGGER.warning(
                        "Publication aborted; owned staging needs inspection before retrying."
                    )

    def _publish(
        self,
        snapshot: SourceSnapshot,
        prepare_index: Callable[[Path], None],
        rules_bytes: bytes | None = None,
    ) -> PublicationResult:
        """Prepare source, catalog, rules and SQLite through the common publication transaction."""

        def prepare(stage: Path) -> GenerationSnapshot:
            """Prepare and validate all artifact bytes before entering the common commit point."""
            documents, catalog = snapshot.resolve()
            self._write(stage / "snapshot.json", _json_bytes(snapshot), "snapshot")
            self._write(stage / "catalog.json", _json_bytes(catalog), "catalog")
            self._write(
                stage / "runtime_rules.json",
                rules_bytes
                if rules_bytes is not None
                else _json_bytes(_validated_runtime_rules(documents)),
                "rules",
            )
            self._step("index_prepare")
            prepare_index(stage / "index.sqlite3")
            self._step("index_open_sync")
            with (stage / "index.sqlite3").open("r+b") as handle:
                self._step("index_fsync")
                os.fsync(handle.fileno())
                self._step("index_close_sync")
            return self._seal(stage, snapshot)

        return self._publish_stage(prepare)

    def migrate(
        self, settings: Settings, *, index_path: Path, rules_path: Path
    ) -> PublicationResult:
        """Validate and snapshot a standalone SQLite/rules pair without changing either input."""
        snapshot, roots = capture_sources(settings)
        self._guard_outputs(roots, settings)
        rules_bytes = _read_regular(rules_path)

        def copy_index(destination: Path) -> None:
            """Use a consistent read-only SQLite backup without modifying standalone originals."""
            self._step("migration_backup")
            with closing(
                sqlite3.connect(index_path.resolve().as_uri() + "?mode=ro", uri=True)
            ) as source:
                with closing(sqlite3.connect(destination)) as output:
                    source.backup(output)

        result = self._execute(lambda: self._publish(snapshot, copy_index, rules_bytes))
        result.source_roots = [root.as_posix() for root in roots]
        result.profile_path = settings.profile.as_posix() if settings.profile else None
        return result

    def build(self, settings: Settings, embedder: Embedder) -> PublicationResult:
        """Embed a captured effective snapshot and activate only its complete validated object."""
        snapshot, roots = capture_sources(settings)
        self._guard_outputs(roots, settings)
        documents, _ = snapshot.resolve()
        _validated_runtime_rules(documents)
        chunks = chunk_policy_documents(documents)

        def prepare_index(destination: Path) -> None:
            """Embed only captured effective chunks and write the staged SQLite artifact."""
            vectors = embedder.embed_documents([chunk.text for chunk in chunks])
            SQLiteVecIndexStore(path=destination, embedding_identity=self.embedding).replace(
                _attach_embeddings(chunks, vectors)
            )

        result = self._execute(lambda: self._publish(snapshot, prepare_index))
        result.source_roots = [root.as_posix() for root in roots]
        result.profile_path = settings.profile.as_posix() if settings.profile else None
        return result

    def activate(self, generation_id: str, *, expected_active: str | None) -> PublicationResult:
        """Activate a complete retained/orphan object against the operator's observed pointer."""

        def operation() -> PublicationResult:
            """Recheck the observed activation token before selecting a complete retained object."""
            pointer = self._pointer()
            if (pointer.active if pointer else None) != expected_active:
                raise GenerationError(
                    "activate", "Active generation changed; inspect before retrying."
                )
            return self._activate(self._inspect_id(generation_id))

        return self._execute(operation)

    def rollback(self, *, expected_active: str) -> PublicationResult:
        """Restore the previous generation against the operator's observed active ID."""

        def operation() -> PublicationResult:
            """Recheck the observed activation token before selecting a complete retained object."""
            pointer = self._pointer()
            if pointer is None or pointer.active != expected_active:
                raise GenerationError(
                    "rollback", "Active generation changed; inspect before retrying."
                )
            if pointer.previous is None:
                raise GenerationError("rollback", "No complete previous generation is retained.")
            return self._activate(self._inspect_id(pointer.previous))

        return self._execute(operation)

    def _has_readers(self, generation_id: str, warnings: list[str]) -> bool:
        """Probe unique kernel leases and remove only unlocked stale lease markers."""
        directory = self.root / "leases" / generation_id
        if not directory.exists() and not directory.is_symlink():
            return False
        if directory.is_symlink() or not directory.is_dir():
            warnings.append("Unexpected reader-lease state was preserved; inspect before pruning.")
            return True
        for path in directory.iterdir():
            try:
                observed = path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                warnings.append(
                    "A reader lease couldn't be inspected; its generation was retained."
                )
                return True
            if (
                not re.fullmatch(r"[0-9a-f]{32}\.lock", path.name)
                or not stat.S_ISREG(observed.st_mode)
                or observed.st_size != 0
            ):
                warnings.append("Unexpected reader-lease entry was preserved.")
                return True
            try:
                with try_exclusive_file_lock(path) as acquired:
                    if not acquired:
                        return True
                path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
            except OSError:
                warnings.append("Reader lease couldn't be inspected; its generation was retained.")
                return True
        directory.rmdir()
        return False

    def _clean_trash(self, path: Path) -> None:
        """Resume deletion only in a verified owned flat trash directory."""
        if (
            not re.fullmatch(r"[0-9a-f]{64}-[0-9a-f]{32}", path.name)
            or path.is_symlink()
            or not path.is_dir()
        ):
            raise GenerationError("cleanup", "Unexpected trash entry was preserved.")
        names = {entry.name for entry in path.iterdir()}
        if not names <= (_FILES | {".owner"}):
            raise GenerationError("cleanup", "Unexpected trash contents were preserved.")
        marker = path / ".owner"
        if marker.exists():
            record = TrashRecord.model_validate_json(_read_regular(marker))
            if (
                record.generation_id != path.name.split("-", 1)[0]
                or set(record.artifacts) != _FILES
            ):
                raise GenerationError("cleanup", "Trash ownership doesn't match.")
        else:
            expected_id = path.name.split("-", 1)[0]
            snapshot = self._inspect(path, expected_id=expected_id)
            record = TrashRecord(
                schema_version=1,
                owner="policynim-generation-trash",
                generation_id=expected_id,
                artifacts={**snapshot.manifest.artifacts, "manifest.json": "sha256:" + expected_id},
            )
            self._write(marker, _json_bytes(record), "trash_owner")
            _sync_directory(path)
            names.add(".owner")
        for name in names:
            if not stat.S_ISREG((path / name).lstat().st_mode):
                raise GenerationError("cleanup", "Unexpected trash file was preserved.")
            if (
                name != ".owner"
                and digest_bytes(_read_regular(path / name)) != record.artifacts[name]
            ):
                raise GenerationError("cleanup", "Changed trash data was preserved for inspection.")
        for name in sorted(names - {".owner"}):
            self._step("trash_remove_" + name)
            (path / name).unlink()
        marker.unlink()
        path.rmdir()

    def _prune(self, *, keep_recent: int) -> RetentionResult:
        """Retain protected objects and move validated unused generations into owned trash."""
        if keep_recent < 0 or keep_recent > 20:
            raise GenerationError("prune", "keep_recent must be between 0 and 20.")
        result = RetentionResult()
        pointer = self._pointer()
        protected = {pointer.active, pointer.previous} if pointer else set()
        candidates: list[tuple[int, GenerationSnapshot]] = []
        for directory in (self.root / "objects").iterdir():
            try:
                if not re.fullmatch(r"[0-9a-f]{64}", directory.name):
                    raise ValueError("Unknown object name.")
                snapshot = self._inspect(directory, expected_id=directory.name)
                candidates.append((directory.stat().st_mtime_ns, snapshot))
            except Exception:
                result.warnings.append("An invalid or unowned object was preserved for inspection.")
        remaining = keep_recent
        for _, snapshot in sorted(
            candidates, key=lambda item: (item[0], item[1].generation_id), reverse=True
        ):
            if snapshot.generation_id not in protected and remaining > 0:
                protected.add(snapshot.generation_id)
                remaining -= 1
        for _, snapshot in candidates:
            generation_id = snapshot.generation_id
            if generation_id in protected or self._has_readers(generation_id, result.warnings):
                result.kept.append(generation_id)
                continue
            self._inspect(snapshot.directory, expected_id=generation_id)
            trash = self.root / "trash" / (generation_id + "-" + uuid4().hex)
            self._step("prune_rename")
            snapshot.directory.rename(trash)
            _sync_directory(self.root / "objects")
            _sync_directory(trash.parent)
            result.removed.append(generation_id)
        for trash in (self.root / "trash").iterdir():
            try:
                self._clean_trash(trash)
            except Exception:
                result.warnings.append(
                    "Owned trash cleanup needs inspection; unexpected entries remain."
                )
        for stage in (self.root / "staging").iterdir():
            try:
                self._remove_stage(stage)
            except Exception:
                result.warnings.append(
                    "Staging cleanup needs inspection; unexpected entries remain."
                )
        result.kept.sort()
        result.removed.sort()
        return result

    def prune(self, *, keep_recent: int = 2) -> RetentionResult:
        """Retain active, previous, recent, and every leased generation without provider calls."""
        with self._locked():
            return self._prune(keep_recent=keep_recent)

    def export_bundle(self, destination: Path, generation_id: str | None = None) -> str:
        """Write a deterministic verified ZIP without overwriting existing operator files."""
        if destination.resolve().is_relative_to(self.root.resolve()):
            raise GenerationError("export", "Bundle outputs must stay outside the managed store.")
        with self.pin(generation_id) as snapshot:
            buffer = io.BytesIO()
            expanded_size = 0
            with zipfile.ZipFile(
                buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                for name in sorted(_FILES):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    artifact = _read_regular(snapshot.directory / name, max_bytes=256 * 1024 * 1024)
                    expanded_size += len(artifact)
                    if expanded_size > 512 * 1024 * 1024:
                        raise GenerationError(
                            "export", "Expanded bundle exceeds the 512 MiB limit."
                        )
                    archive.writestr(info, artifact)
            data = buffer.getvalue()
            if len(data) > 256 * 1024 * 1024:
                raise GenerationError("export", "Compressed bundle exceeds the 256 MiB limit.")
            expected_digest = digest_bytes(data)
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            observed = os.fstat(descriptor)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                _sync_directory(destination.parent)
                if (
                    digest_bytes(_read_regular(destination, max_bytes=256 * 1024 * 1024))
                    != expected_digest
                ):
                    raise GenerationError("export", "Bundle output changed before verification.")
            except BaseException:
                try:
                    current = destination.lstat()
                    if (current.st_dev, current.st_ino) == (observed.st_dev, observed.st_ino):
                        destination.unlink()
                except OSError:
                    LOGGER.warning("Bundle export failed; its owned output needs inspection.")
                raise
            return expected_digest

    def import_bundle(
        self, bundle: Path, *, expected_digest: str, source_settings: Settings | None = None
    ) -> PublicationResult:
        """Import verified bytes offline; optionally require current packaged sources to match."""
        try:
            raw = _read_regular(bundle, max_bytes=256 * 1024 * 1024)
            if digest_bytes(raw) != expected_digest:
                raise ValueError("Bundle checksum doesn't match the expected SHA256.")
            contents: dict[str, bytes] = {}
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) != len(_FILES) or {entry.filename for entry in entries} != _FILES:
                    raise ValueError("Bundle membership isn't the five required flat files.")
                if sum(entry.file_size for entry in entries) > 512 * 1024 * 1024:
                    raise ValueError("Expanded bundle exceeds the size limit.")
                for entry in entries:
                    kind = stat.S_IFMT(entry.external_attr >> 16)
                    if (
                        entry.is_dir()
                        or kind not in {0, stat.S_IFREG}
                        or entry.flag_bits & 1
                        or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or entry.file_size > 256 * 1024 * 1024
                    ):
                        raise ValueError("Unsupported or oversized archive member.")
                    with archive.open(entry) as handle:
                        data = handle.read(entry.file_size + 1)
                    if len(data) != entry.file_size:
                        raise ValueError("Archive member size doesn't match its declaration.")
                    contents[entry.filename] = data
        except GenerationError:
            raise
        except Exception as exc:
            raise GenerationError(
                "import", "Bundle checksum, membership, size or encoding isn't valid."
            ) from exc

        if source_settings is not None:
            try:
                incoming = SourceSnapshot.model_validate_json(contents["snapshot.json"])
                current, roots = capture_sources(source_settings)
                self._guard_outputs(roots, source_settings)
            except GenerationError:
                raise
            except Exception as exc:
                raise GenerationError(
                    "import", "Packaged source agreement couldn't be validated."
                ) from exc
            if incoming != current:
                raise GenerationError(
                    "import",
                    "Bundle sources don't match the packaged source snapshot. "
                    "Publish an updated bundle explicitly before building this application.",
                )

        def prepare(stage: Path) -> GenerationSnapshot:
            """Prepare and validate all artifact bytes before entering the common commit point."""
            for name in sorted(_FILES):
                self._write(stage / name, contents[name], "bundle_" + name)
            self._step("staging_validate")
            return self._inspect(stage, expected_embedding=self.embedding, staging=True)

        return self._execute(lambda: self._publish_stage(prepare))
