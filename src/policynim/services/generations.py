"""Immutable policy generations with one serialized atomic activation pointer."""

from __future__ import annotations

import errno
import io
import json
import logging
import os
import re
import sqlite3
import stat
import zipfile
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp
from threading import Lock
from types import TracebackType
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from policynim.contracts import Embedder
from policynim.errors import ConfigurationError, GenerationError, PolicyNIMError, ProviderError
from policynim.ingest.chunking import chunk_policy_documents
from policynim.operations import ResourceStack, check_budget, close_components
from policynim.services.catalog import CatalogPack, PolicyCatalog
from policynim.services.generation_sources import (
    SchemaOne,
    SourceSnapshot,
    capture_sources,
    digest_bytes,
)
from policynim.services.ingest import _attach_embeddings, _compile_runtime_rules_artifact
from policynim.settings import Settings
from policynim.storage.file_lock import exclusive_file_lock, try_exclusive_file_lock
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import (
    EmbeddingIdentity,
    GenerationID,
    ParsedDocument,
    RuntimeRulesArtifact,
    StrictModel,
)

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
_ARTIFACTS = {"snapshot.json", "catalog.json", "index.sqlite3", "runtime_rules.json"}
_FILES = _ARTIFACTS | {"manifest.json"}
_OWNER = b'{"owner":"policynim-generations","schema_version":1}\n'
_STAGE_OWNER = b"policynim-generation-stage-v1\n"
LOGGER = logging.getLogger(__name__)


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
    rules: RuntimeRulesArtifact
    indexed_policy_count: int = 0
    activation: GenerationPointer | None = None
    _lease: AbstractContextManager[None] | None = field(default=None, repr=False)
    _lease_path: Path | None = field(default=None, repr=False)
    _lease_identity: tuple[int, int] | None = field(default=None, repr=False)
    _artifact_identity: ArtifactToken | None = field(default=None, repr=False)

    @property
    def indexed_catalog(self) -> PolicyCatalog:
        """Return generation metadata without changing the sealed source catalog digest."""
        return self.catalog.model_copy(
            update={
                "scope": "generation",
                "generation_id": self.generation_id,
                "pack_pins": self.manifest.pack_pins,
                "indexed_count": self.indexed_policy_count,
            },
            deep=True,
        )

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
        path, self._lease_path = self._lease_path, None
        identity, self._lease_identity = self._lease_identity, None
        try:
            if lease is not None:
                lease.__exit__(None, None, None)
        finally:
            if path is not None:
                _remove_reader_marker(path, identity)

    def validate_read(self) -> None:
        """Check identities and validate content when change stamps aren't available."""
        if self._lease is None or self._artifact_identity is None:
            raise GenerationError("read", "The pinned reader is closed; create a new service.")
        token = GenerationStore._artifact_token(self.directory)
        unknown_stamp = False
        for previous, current in zip(self._artifact_identity, token, strict=True):
            if previous[:-1] != current[:-1] or (
                previous[-1] is not None and current[-1] is not None and previous[-1] != current[-1]
            ):
                raise GenerationError("read", "Pinned generation artifacts changed; inspect them.")
            unknown_stamp = unknown_stamp or previous[-1] is None or current[-1] is None
        if unknown_stamp:
            inspector = GenerationStore(
                root=self.directory.parent.parent, embedding=self.manifest.embedding
            )
            inspector._inspect(
                self.directory,
                expected_id=self.generation_id,
                expected_embedding=self.manifest.embedding,
            )
            if GenerationStore._artifact_token(self.directory) != token:
                raise GenerationError("read", "Generation changed during content validation.")
            self._artifact_identity = token


def _remove_reader_marker(path: Path, identity: tuple[int, int] | None) -> None:
    """Remove only the empty marker this reader created, preserving substituted entries."""
    try:
        observed = path.lstat()
        if (
            identity is not None
            and (observed.st_dev, observed.st_ino) == identity
            and stat.S_ISREG(observed.st_mode)
            and observed.st_size == 0
        ):
            with try_exclusive_file_lock(path) as acquired:
                if not acquired:
                    return
            current = path.lstat()
            if (current.st_dev, current.st_ino) == identity and current.st_size == 0:
                path.unlink()
            else:
                LOGGER.warning("An unexpected reader-lease entry was preserved for inspection.")
        else:
            LOGGER.warning("An unexpected reader-lease entry was preserved for inspection.")
    except FileNotFoundError:
        pass
    except OSError:
        LOGGER.warning("Reader lease released; its stale marker needs retention cleanup.")


ArtifactToken = tuple[tuple[str, int, int, int, int, int, int, int | None], ...]


def _file_change_stamp(path: Path, observed: os.stat_result) -> int | None:
    """Use a metadata-change stamp, or disable cache reuse when the filesystem can't supply one."""
    if os.name != "nt":
        return observed.st_ctime_ns
    # Windows st_ctime records creation. FILE_BASIC_INFO supplies ChangeTime:
    # https://learn.microsoft.com/windows/win32/api/winbase/ns-winbase-file_basic_info
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileBasicInfo(ctypes.Structure):
        """Match the documented native FILE_BASIC_INFO layout."""

        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        )
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
                return None
            kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
            get_info = kernel.GetFileInformationByHandleEx
            get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            get_info.restype = wintypes.BOOL
            info = FileBasicInfo()
            if not get_info(
                getattr(msvcrt, "get_osfhandle")(handle.fileno()),
                0,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                return None
            stamp = int(getattr(info, "ChangeTime"))
            return stamp if stamp > 0 else None
    except (AttributeError, OSError):
        return None


@dataclass(frozen=True)
class _CachedGeneration:
    """One verified immutable object identity; cache entries never own reader handles."""

    token: ArtifactToken
    snapshot: GenerationSnapshot
    size: int


_VALIDATION_CACHE: OrderedDict[tuple[str, str, str], _CachedGeneration] = OrderedDict()
_CACHE_LOCK = Lock()
_VALIDATION_LOCKS = tuple(Lock() for _ in range(16))
_CACHE_MAX_ENTRIES = 8
_CACHE_MAX_BYTES = 32 * 1024 * 1024


@contextmanager
def _validation_slot(key: tuple[str, str, str]) -> Iterator[None]:
    """Share cold inspection work without holding the publication or warm-cache locks."""
    lock = _VALIDATION_LOCKS[hash(key) % len(_VALIDATION_LOCKS)]
    while not lock.acquire(timeout=0.05):
        check_budget()
    try:
        check_budget()
        yield
    finally:
        lock.release()


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
        """Honor cooperative cancellation and expose named transaction fault boundaries."""
        check_budget()
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
        if not self.root.exists():
            raise GenerationError(
                "open",
                "No managed generation exists.",
                recovery=(
                    "For a fresh install, run `policynim ingest` from verified sources "
                    "or import a verified bundle. If a standalone index and rules already "
                    "exist, preserve them and run `policynim generation migrate` for that pair."
                ),
            )
        if not self.root.is_dir():
            raise GenerationError("open", "The generation root isn't a directory.")
        owner = self.root / "owner.json"
        if not owner.exists() and not initialize:
            raise GenerationError("open", "The directory isn't an owned generation store.")
        with exclusive_file_lock(self.root / ".publish.lock", on_wait=check_budget):
            if not owner.exists():
                interrupted: list[tuple[Path, tuple[int, int], bytes]] = []
                for entry in self.root.iterdir():
                    if entry.name == ".publish.lock":
                        continue
                    if not re.fullmatch(r"\.owner-[0-9a-f]{32}\.tmp", entry.name):
                        raise GenerationError(
                            "open", "The directory isn't an owned generation store."
                        )
                    observed = entry.lstat()
                    content = _read_regular(entry, max_bytes=len(_OWNER))
                    if not _OWNER.startswith(content):
                        raise GenerationError("open", "An unknown owner record was preserved.")
                    interrupted.append((entry, (observed.st_dev, observed.st_ino), content))
                for entry, identity, content in interrupted:
                    current = entry.lstat()
                    if (current.st_dev, current.st_ino) != identity or _read_regular(
                        entry
                    ) != content:
                        raise GenerationError("open", "An owner record changed; preserve it.")
                    entry.unlink()
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

    @contextmanager
    def _writer(self, *, initialize: bool = False) -> Iterator[None]:
        """Serialize preparation and retention while allowing readers during provider I/O."""
        with self._locked(initialize=initialize):
            pass
        with exclusive_file_lock(self.root / ".writer.lock", on_wait=check_budget):
            with self._locked():
                self._pointer()
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
        cleanup_marker: bool = False,
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
            if cleanup_marker and ".owner" in names:
                _read_regular(directory / ".owner")
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
            return GenerationSnapshot(
                generation_id,
                directory,
                manifest,
                catalog,
                documents,
                rules,
                indexed_policy_count=len({chunk.policy.policy_id for chunk in expected_chunks}),
            )
        except GenerationError:
            raise
        except Exception as exc:
            raise GenerationError(
                "inspect",
                "Source, catalog, index, rules, schema or model identity couldn't be validated.",
            ) from exc

    def read_activation(self) -> GenerationPointer | None:
        """Read owned pointer metadata for recovery even if a referenced object is damaged."""
        with self._locked():
            return self._pointer()

    def inspect(self, generation_id: str | None = None) -> GenerationSnapshot:
        """Force full validation under a reader lease without retaining it after this call."""
        with self.pin(generation_id, force_validation=True) as snapshot:
            return replace(snapshot, _lease=None, _lease_path=None, _lease_identity=None)

    def pin_retained(self, generation_id: str) -> GenerationSnapshot:
        """Read explicit historical source evidence against its sealed embedding identity."""
        try:
            selected = self._selected_id(generation_id, None)
            with self._locked():
                directory = self.root / "objects" / selected
                self._artifact_token(directory)
                try:
                    manifest_bytes = _read_regular(directory / "manifest.json")
                    if sha256(manifest_bytes).hexdigest() != selected:
                        raise ValueError("Generation content ID mismatch.")
                    manifest = GenerationManifest.model_validate_json(manifest_bytes)
                except (ValueError, OSError) as exc:
                    raise GenerationError(
                        "read", "Retained generation identity couldn't be validated."
                    ) from exc
            # The ordinary reader still proves every artifact against this sealed
            # identity and acquires its lease before returning any source evidence.
            return GenerationStore(
                root=self.root, embedding=manifest.embedding, fault=self._fault
            ).pin(selected)
        except OSError as exc:
            raise GenerationError(
                "pin",
                "Reader admission couldn't access generation locks or leases. "
                "Check filesystem permissions and available storage before retrying.",
            ) from exc

    def _selected_id(self, generation_id: str | None, pointer: GenerationPointer | None) -> str:
        """Validate an explicit or active ID before using it as a local artifact name."""
        selected = (
            generation_id if generation_id is not None else (pointer.active if pointer else None)
        )
        if selected is None:
            raise GenerationError(
                "open",
                "No active generation exists; import a verified bundle "
                "or run explicit generation migration.",
            )
        try:
            return GenerationPointer(schema_version=1, active=selected).active
        except ValueError as exc:
            raise GenerationError(
                "open", "A generation ID must contain exactly 64 lowercase hex digits."
            ) from exc

    def _inspect_id(self, generation_id: str | None = None) -> GenerationSnapshot:
        """Fully inspect a selected object while the caller owns writer serialization."""
        selected = self._selected_id(generation_id, self._pointer())
        return self._inspect(
            self.root / "objects" / selected,
            expected_id=selected,
            expected_embedding=self.embedding,
        )

    @staticmethod
    def _artifact_token(directory: Path) -> ArtifactToken:
        """Check membership and file identities without reading or hashing corpus/index bytes."""
        try:
            observed = directory.lstat()
            if not stat.S_ISDIR(observed.st_mode) or directory.is_symlink():
                raise ValueError("Not an immutable object directory.")
            if {path.name for path in directory.iterdir()} != _FILES:
                raise ValueError("Generation membership changed.")
            token = []
            for name in [".", *sorted(_FILES)]:
                path = directory if name == "." else directory / name
                info = path.lstat()
                if name != "." and not stat.S_ISREG(info.st_mode):
                    raise ValueError("Not a regular immutable artifact.")
                token.append(
                    (
                        name,
                        info.st_dev,
                        info.st_ino,
                        info.st_mode,
                        info.st_nlink,
                        info.st_size,
                        info.st_mtime_ns,
                        info.st_ctime_ns if name == "." else _file_change_stamp(path, info),
                    )
                )
            return tuple(token)
        except (OSError, ValueError) as exc:
            raise GenerationError(
                "inspect", "Generation artifact identity or membership changed."
            ) from exc

    def _reader_snapshot(self, generation_id: str, *, force: bool) -> GenerationSnapshot:
        """Reuse full validation only for unchanged artifact identities and embedding settings."""
        directory = self.root / "objects" / generation_id
        key = (str(directory.absolute()), generation_id, self.embedding.model_dump_json())
        token = self._artifact_token(directory)
        reusable = all(item[-1] is not None for item in token)
        if reusable and not force and self._fault is None:
            with _CACHE_LOCK:
                cached = _VALIDATION_CACHE.get(key)
                if cached is not None and cached.token == token:
                    _VALIDATION_CACHE.move_to_end(key)
                else:
                    cached = None
            if cached is not None:
                result = deepcopy(cached.snapshot)
                if self._artifact_token(directory) != token:
                    raise GenerationError("inspect", "Generation changed while opening its reader.")
                return result
        with _validation_slot(key):
            token = self._artifact_token(directory)
            reusable = all(item[-1] is not None for item in token)
            if reusable and not force and self._fault is None:
                with _CACHE_LOCK:
                    cached = _VALIDATION_CACHE.get(key)
                    if cached is not None and cached.token == token:
                        _VALIDATION_CACHE.move_to_end(key)
                    else:
                        cached = None
                if cached is not None:
                    result = deepcopy(cached.snapshot)
                    if self._artifact_token(directory) != token:
                        raise GenerationError(
                            "inspect", "Generation changed while opening its reader."
                        )
                    return result
            snapshot = self._inspect(
                directory, expected_id=generation_id, expected_embedding=self.embedding
            )
            if self._artifact_token(directory) != token:
                raise GenerationError("inspect", "Generation changed during validation.")
            snapshot._artifact_identity = token
            if reusable and self._fault is None:
                size = (
                    len(snapshot.catalog.model_dump_json().encode())
                    + len(snapshot.rules.model_dump_json().encode())
                    + sum(len(doc.model_dump_json().encode()) for doc in snapshot.documents)
                )
                with _CACHE_LOCK:
                    _VALIDATION_CACHE.pop(key, None)
                    if size <= _CACHE_MAX_BYTES:
                        while _VALIDATION_CACHE and (
                            len(_VALIDATION_CACHE) >= _CACHE_MAX_ENTRIES
                            or sum(entry.size for entry in _VALIDATION_CACHE.values()) + size
                            > _CACHE_MAX_BYTES
                        ):
                            _VALIDATION_CACHE.popitem(last=False)
                        _VALIDATION_CACHE[key] = _CachedGeneration(token, snapshot, size)
            return deepcopy(snapshot)

    def _remove_failed_lease_directory(self, directory: Path, identity: tuple[int, int]) -> None:
        """Remove only this failed reader's empty directory, serialized with new readers."""
        try:
            # Cleanup still owns this obligation after the request budget expires.
            with exclusive_file_lock(self.root / ".publish.lock"):
                if _read_regular(self.root / "owner.json", max_bytes=len(_OWNER)) != _OWNER:
                    return
                observed = directory.lstat()
                if (
                    stat.S_ISDIR(observed.st_mode)
                    and (observed.st_dev, observed.st_ino) == identity
                ):
                    directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                LOGGER.warning("Failed reader's lease-directory cleanup needs inspection.")
        except GenerationError:
            LOGGER.warning("Failed reader's lease-directory ownership needs inspection.")

    def pin(
        self, generation_id: str | None = None, *, force_validation: bool = False
    ) -> GenerationSnapshot:
        """Register a lease atomically, then validate outside the short publication lock."""
        try:
            with ResourceStack() as cleanup:
                with self._locked():
                    pointer = self._pointer()
                    selected = self._selected_id(generation_id, pointer)
                    try:
                        object_mode = (self.root / "objects" / selected).lstat().st_mode
                    except FileNotFoundError as exc:
                        raise GenerationError(
                            "pin", "The selected generation doesn't exist."
                        ) from exc
                    if not stat.S_ISDIR(object_mode):
                        raise GenerationError(
                            "pin", "The selected generation isn't a regular directory."
                        )
                    directory = self.root / "leases" / selected
                    if directory.is_symlink():
                        raise GenerationError(
                            "pin", "The reader lease directory can't be a symlink."
                        )
                    try:
                        directory.mkdir(mode=0o700)
                    except FileExistsError:
                        if not directory.is_dir():
                            raise GenerationError("pin", "The reader lease directory isn't valid.")
                    else:
                        created = directory.lstat()
                        cleanup.callback(
                            self._remove_failed_lease_directory,
                            directory,
                            (created.st_dev, created.st_ino),
                        )
                    lease_path = directory / (uuid4().hex + ".lock")
                    descriptor = os.open(lease_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as handle:
                        observed = os.fstat(handle.fileno())
                        identity = observed.st_dev, observed.st_ino
                        cleanup.callback(_remove_reader_marker, lease_path, identity)
                    lease = exclusive_file_lock(lease_path)
                    lease.__enter__()
                    cleanup.callback(lease.__exit__, None, None, None)
                    observed = lease_path.lstat()
                    if (observed.st_dev, observed.st_ino) != identity:
                        raise GenerationError("pin", "Reader lease identity changed.")
                snapshot = self._reader_snapshot(selected, force=force_validation)
                check_budget()
                snapshot.activation = pointer
                snapshot._lease, snapshot._lease_path, snapshot._lease_identity = (
                    lease,
                    lease_path,
                    identity,
                )
                cleanup.pop_all()
                return snapshot
        except OSError as exc:
            raise GenerationError(
                "pin",
                "Reader admission couldn't access generation locks or leases. "
                "Check filesystem permissions and available storage before retrying.",
            ) from exc

    def _execute(
        self, operation: Callable[[], PublicationResult], *, initialize: bool = True
    ) -> PublicationResult:
        """Preserve a committed result if publisher cleanup fails after activation."""
        result: PublicationResult | None = None
        try:
            with self._writer(initialize=initialize):
                self._pointer()
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
            if isinstance(exc, PolicyNIMError):
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
        destination = self.root.resolve()
        if any(
            destination.is_relative_to(root.resolve()) or root.resolve().is_relative_to(destination)
            for root in roots
        ) or (
            settings.profile is not None and settings.profile.resolve().is_relative_to(destination)
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
        try:
            mode = stage.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(mode):
            raise GenerationError("cleanup", "Unexpected staging entry was preserved.")
        names = {path.name for path in stage.iterdir()}
        if not names <= (_FILES | {".owner"}):
            raise GenerationError("cleanup", "Unexpected staging contents were preserved.")
        observed = stage.lstat()
        if expected_identity is not None:
            if (observed.st_dev, observed.st_ino) != expected_identity:
                raise GenerationError("cleanup", "Staging directory identity changed.")
        elif ".owner" in names:
            marker = _read_regular(stage / ".owner", max_bytes=len(_STAGE_OWNER))
            if marker != _STAGE_OWNER and not (
                names == {".owner"}
                and re.fullmatch(r"build-[a-z0-9_]{8}", stage.name)
                and _STAGE_OWNER.startswith(marker)
            ):
                raise GenerationError("cleanup", "Stage ownership doesn't match.")
        elif names or not re.fullmatch(r"build-[a-z0-9_]{8}", stage.name):
            self._inspect(stage)
        for name in names:
            path = stage / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise GenerationError("cleanup", "Unexpected staging file was preserved.")
        removal_order = sorted(names - {".owner"})
        if ".owner" in names:
            removal_order.append(".owner")
        for name in removal_order:
            self._step("stage_remove_" + name)
            (stage / name).unlink()
        stage.rmdir()

    def _activate(self, prepared: GenerationSnapshot) -> PublicationResult:
        """Install a complete object before atomically replacing the active/previous pointer."""
        target = self.root / "objects" / prepared.generation_id
        try:
            target.lstat()
        except FileNotFoundError:
            occupied = False
        else:
            occupied = True
        if occupied:
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
        self._inspect(target, expected_id=prepared.generation_id, expected_embedding=self.embedding)
        token = self._artifact_token(target)
        with self._locked():
            return self._commit_activation(prepared, target, token)

    def _commit_activation(
        self, prepared: GenerationSnapshot, target: Path, token: ArtifactToken
    ) -> PublicationResult:
        """Commit verified identities while activation and reader registration are serialized."""
        pointer = self._pointer()
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
            if self._artifact_token(target) != token:
                raise GenerationError(
                    "activate", "Generation changed before commit; preserve and inspect it."
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
        except ProviderError as exc:
            raise ProviderError(
                "Generation preparation failed; prior active state is unchanged. " + str(exc),
                failure_class=exc.failure_class,
            ) from exc
        except ConfigurationError as exc:
            raise ConfigurationError(
                "Generation preparation failed; prior active state is unchanged. " + str(exc),
                failure_class=exc.failure_class,
            ) from exc
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

        def publish() -> PublicationResult:
            """Reject changed source selection after waiting for serialized publication."""
            self._require_fresh_sources(snapshot, settings)
            return self._publish(snapshot, copy_index, rules_bytes)

        result = self._execute(publish)
        result.source_roots = [root.as_posix() for root in roots]
        result.profile_path = settings.profile.as_posix() if settings.profile else None
        return result

    def _require_fresh_sources(self, snapshot: SourceSnapshot, settings: Settings) -> None:
        """Keep queued producers from activating inputs changed while writer ownership waited."""
        current, roots = capture_sources(settings)
        self._guard_outputs(roots, settings)
        if current != snapshot:
            raise GenerationError(
                "prepare", "Sources changed while waiting for publication; review and retry."
            )

    def build(
        self,
        settings: Settings,
        embedder: Embedder | None = None,
        *,
        embedder_factory: Callable[[], Embedder] | None = None,
    ) -> PublicationResult:
        """Validate sources before using a borrowed embedder or command-owned lazy factory."""
        if (embedder is None) == (embedder_factory is None):
            raise GenerationError(
                "prepare", "Provide exactly one embedder or owned embedder factory."
            )
        snapshot, roots = capture_sources(settings)
        self._guard_outputs(roots, settings)
        documents, _ = snapshot.resolve()
        _validated_runtime_rules(documents)
        chunks = chunk_policy_documents(documents)

        def prepare_index(destination: Path) -> None:
            """Embed only captured effective chunks and write the staged SQLite artifact."""
            with ResourceStack() as cleanup:
                component = embedder
                if component is None:
                    assert embedder_factory is not None
                    component = embedder_factory()
                    cleanup.callback(close_components, component)
                vectors = component.embed_documents([chunk.text for chunk in chunks])
            SQLiteVecIndexStore(path=destination, embedding_identity=self.embedding).replace(
                _attach_embeddings(chunks, vectors)
            )

        def publish() -> PublicationResult:
            """Reject damaged active state before constructing an embedding provider."""
            self._require_fresh_sources(snapshot, settings)
            pointer = self._pointer()
            if pointer is not None:
                self._inspect(self.root / "objects" / pointer.active, expected_id=pointer.active)
            return self._publish(snapshot, prepare_index)

        result = self._execute(publish)
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

        return self._execute(operation, initialize=False)

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

        return self._execute(operation, initialize=False)

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
        if not names:
            path.rmdir()
            return
        marker = path / ".owner"
        if marker.exists():
            marker_bytes = _read_regular(marker)
            try:
                record = TrashRecord.model_validate_json(marker_bytes)
            except ValueError:
                # Marker creation precedes deletion. A partial marker can recover
                # only while every original artifact still validates independently.
                expected_id = path.name.split("-", 1)[0]
                snapshot = self._inspect(path, expected_id=expected_id, cleanup_marker=True)
                record = TrashRecord(
                    schema_version=1,
                    owner="policynim-generation-trash",
                    generation_id=expected_id,
                    artifacts={
                        **snapshot.manifest.artifacts,
                        "manifest.json": "sha256:" + expected_id,
                    },
                )
                expected_bytes = _json_bytes(record)
                if len(marker_bytes) >= len(expected_bytes) or not expected_bytes.startswith(
                    marker_bytes
                ):
                    raise GenerationError(
                        "cleanup", "Unknown trash ownership was preserved."
                    ) from None
                observed = marker.lstat()
                unchanged = _read_regular(marker) == marker_bytes
                current = marker.lstat()
                if not unchanged or (
                    current.st_dev,
                    current.st_ino,
                    current.st_size,
                    current.st_mtime_ns,
                    current.st_ctime_ns,
                ) != (
                    observed.st_dev,
                    observed.st_ino,
                    observed.st_size,
                    observed.st_mtime_ns,
                    observed.st_ctime_ns,
                ):
                    raise GenerationError("cleanup", "Trash ownership changed; preserve it.")
                marker.unlink()
                self._write(marker, expected_bytes, "trash_owner")
                _sync_directory(path)
            if (
                record.generation_id != path.name.split("-", 1)[0]
                or set(record.artifacts) != _FILES
                or record.artifacts["manifest.json"] != "sha256:" + record.generation_id
            ):
                raise GenerationError("cleanup", "Trash ownership doesn't match.")
            if names != {".owner"}:
                manifest_bytes = _read_regular(path / "manifest.json")
                if digest_bytes(manifest_bytes) != "sha256:" + record.generation_id:
                    raise GenerationError("cleanup", "Changed trash manifest was preserved.")
                manifest = GenerationManifest.model_validate_json(manifest_bytes)
                if record.artifacts != {
                    **manifest.artifacts,
                    "manifest.json": "sha256:" + record.generation_id,
                }:
                    raise GenerationError("cleanup", "Trash hashes don't match its manifest.")
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
        # Keep the authenticated manifest through every other artifact's removal.
        removal_order = sorted(names - {".owner", "manifest.json"})
        if "manifest.json" in names:
            removal_order.append("manifest.json")
        for name in removal_order:
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
            check_budget()
            try:
                if not re.fullmatch(r"[0-9a-f]{64}", directory.name):
                    raise ValueError("Unknown object name.")
                snapshot = self._inspect(directory, expected_id=directory.name)
                candidates.append((directory.stat().st_mtime_ns, snapshot))
            except ProviderError:
                raise
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
            check_budget()
            generation_id = snapshot.generation_id
            if generation_id in protected:
                result.kept.append(generation_id)
                continue
            try:
                self._inspect(snapshot.directory, expected_id=generation_id)
                token = self._artifact_token(snapshot.directory)
            except ProviderError:
                raise
            except Exception as error:
                result.warnings.append(self._retention_warning(snapshot.directory, error))
                continue
            with self._locked():
                if self._has_readers(generation_id, result.warnings):
                    result.kept.append(generation_id)
                    continue
                if self._pointer() != pointer:
                    raise GenerationError(
                        "prune", "Activation changed during retention; inspect before retrying."
                    )
                trash = self.root / "trash" / (generation_id + "-" + uuid4().hex)
                self._step("prune_rename")
                try:
                    if self._artifact_token(snapshot.directory) != token:
                        raise GenerationError(
                            "prune", "An object changed before retention; preserve and inspect it."
                        )
                    snapshot.directory.rename(trash)
                except (GenerationError, OSError) as error:
                    result.warnings.append(self._retention_warning(snapshot.directory, error))
                    continue
                _sync_directory(self.root / "objects")
                _sync_directory(trash.parent)
                result.removed.append(generation_id)
        for trash in (self.root / "trash").iterdir():
            check_budget()
            try:
                self._clean_trash(trash)
            except ProviderError:
                raise
            except Exception as error:
                result.warnings.append(self._retention_warning(trash, error))
        for stage in (self.root / "staging").iterdir():
            check_budget()
            try:
                self._remove_stage(stage)
            except ProviderError:
                raise
            except Exception as error:
                result.warnings.append(self._retention_warning(stage, error))
        result.kept.sort()
        result.removed.sort()
        return result

    def _retention_warning(self, path: Path, error: Exception) -> str:
        """Record managed locations and safe causes without echoing raw filesystem error text."""
        reason = type(error).__name__
        if isinstance(error, GenerationError):
            reason += ": " + str(error)
        elif isinstance(error, OSError):
            reason += f" (errno={error.errno})"
        warning = (
            f"Retention cleanup for {path.relative_to(self.root).as_posix()!r} "
            f"needs inspection: {reason}"
        )
        LOGGER.warning("%s", warning)
        return warning

    def prune(self, *, keep_recent: int = 2) -> RetentionResult:
        """Retain active, previous, recent, and every leased generation without provider calls."""
        with self._writer():
            return self._prune(keep_recent=keep_recent)

    def export_bundle(self, destination: Path, generation_id: str | None = None) -> str:
        """Write a deterministic verified ZIP without overwriting existing operator files."""
        if destination.resolve().is_relative_to(self.root.resolve()):
            raise GenerationError("export", "Bundle outputs must stay outside the managed store.")
        with self.pin(generation_id, force_validation=True) as snapshot:
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
                    expected = (
                        "sha256:" + snapshot.generation_id
                        if name == "manifest.json"
                        else snapshot.manifest.artifacts[name]
                    )
                    if digest_bytes(artifact) != expected:
                        raise GenerationError(
                            "export",
                            "An artifact changed after validation; preserve and inspect it.",
                        )
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

        incoming: SourceSnapshot | None = None
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

        def publish() -> PublicationResult:
            """Recheck source-match inputs after any writer wait before installing the bundle."""
            if source_settings is not None:
                assert incoming is not None
                self._require_fresh_sources(incoming, source_settings)
            return self._publish_stage(prepare)

        return self._execute(publish)
