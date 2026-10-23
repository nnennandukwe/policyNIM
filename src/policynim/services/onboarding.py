"""Local setup workflow: reviewed sources, storage, explicit build, verified result, new config."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shlex
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Lock
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator

from policynim.config_discovery import LITERAL_CONFIG_HEADER
from policynim.errors import ConfigurationError
from policynim.runtime_paths import resolve_corpus_root, resolve_runtime_path
from policynim.services.catalog import PolicyCatalog, build_policy_catalog
from policynim.services.index_store import create_index_store
from policynim.services.ingest import create_ingest_service
from policynim.services.preflight import create_preflight_service
from policynim.services.profiles import initialize_profile, serialize_profile
from policynim.services.vector_storage import probe_vector_backend
from policynim.settings import Settings
from policynim.types import PreflightRequest, StrictModel

TOPICS = ("security", "api", "data", "observability", "reliability", "supply-chain", "testing")


class OnboardingRequest(StrictModel):
    """Explicit local choices; secrets never appear in response models or diagnostics."""

    deployment: Literal["local", "self_hosted", "shared_hosted"] = "local"
    source_mode: Literal["baseline", "topics", "profile", "directory"] = "baseline"
    topics: list[str] = Field(default_factory=list, max_length=7)
    additional_packs: list[str] = Field(default_factory=list, max_length=10)
    source_path: str = Field(default="", max_length=4096)
    generations_dir: str = Field(default="", max_length=4096)
    vector_backend: Literal["sqlite", "qdrant", "pgvector"] = "sqlite"
    vector_url: str = Field(default="", max_length=4096)
    vector_namespace: str = "policynim"
    vector_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    vector_password: SecretStr = Field(default=SecretStr(""), repr=False)
    nvidia_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    task: str = Field(
        default="Review a data migration for interruption and recovery.", max_length=2000
    )
    review_digest: str = Field(default="", max_length=64)
    consent: bool = False

    @field_validator("nvidia_api_key", "vector_api_key", "vector_password")
    @classmethod
    def single_line_secret(cls, value: SecretStr) -> SecretStr:
        """Prevent control characters from becoming a second saved configuration assignment."""
        if any(ord(character) < 32 for character in value.get_secret_value()):
            raise ValueError("Credentials must be single-line values without control characters.")
        return value


def _write_new_private(path: Path, text: str) -> list[str]:
    """Commit a new private file by exclusive link; postcommit sync failures are warnings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    warnings: list[str] = []
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        if os.name != "nt":
            try:
                descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                warnings.append("File saved; directory sync needs inspection before retrying.")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                warnings.append("Owned setup staging cleanup needs inspection.")
    return warnings


def _env_line(key: str, value: str) -> str:
    """Quote dotenv values without shell expansion or accidental multiline assignments."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"{key}='{escaped}'"


def _profile_identity(path: Path) -> tuple[int, int, int, int, int]:
    """Bind rollback to the observed inode, timestamps, and size of a newly created profile."""
    observed = path.lstat()
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
        observed.st_size,
    )


def _remove_created_profile(
    path: Path, identity: tuple[int, int, int, int, int], text: str
) -> bool:
    """Remove only an unchanged profile owned by this failed save attempt."""
    try:
        if _profile_identity(path) != identity or path.is_symlink():
            return False
        if path.read_text(encoding="utf-8") != text or _profile_identity(path) != identity:
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except (OSError, UnicodeError):
        return False


class OnboardingService:
    """Bind every effectful setup step to one reviewed configuration and source digest."""

    def __init__(self, *, settings: Settings, destination: Path) -> None:
        """Keep a process-local workflow and a new config destination selected at launch."""
        self._base = settings.model_copy(deep=True)
        self.destination = destination.expanduser().absolute()
        self._key = secrets.token_bytes(32)
        self._lock = Lock()
        self._reviewed: str | None = None
        self._built: str | None = None
        self._generation: str | None = None
        self._verified: tuple[str, str] | None = None
        self._saved = False
        self._pending_profile_identity: tuple[int, int] | None = None

    def status(self) -> dict[str, object]:
        """Expose only progress and the public generation identity after client disconnects."""
        return {
            "reviewed": self._reviewed is not None,
            "generation_id": self._generation,
            "verified": self._verified is not None,
            "saved": self._saved,
            "config_path": str(self.destination),
            "api_key_configured": bool(self._base.nvidia_api_key),
            "generations_dir": str(resolve_runtime_path(self._base.generations_dir)),
        }

    @contextmanager
    def _selection(
        self, request: OnboardingRequest
    ) -> Iterator[tuple[Settings, PolicyCatalog, str | None]]:
        """Preview explicit local sources in private temporary space without activating them."""
        if request.deployment == "shared_hosted":
            raise ConfigurationError(
                "Use `policynim quickstart --target hosted-mcp` for the shared public service. "
                "Custom policies and storage need local or self-hosted PolicyNIM."
            )
        values = self._base.model_dump()
        values.update(
            {
                "profile": None,
                "corpus_dir": None,
                "generation_id": None,
                "vector_backend": request.vector_backend,
                "vector_url": request.vector_url or None,
                "vector_namespace": request.vector_namespace,
                "generations_dir": resolve_runtime_path(
                    Path(request.generations_dir)
                    if request.generations_dir
                    else self._base.generations_dir
                ),
            }
        )
        if request.nvidia_api_key.get_secret_value():
            values["nvidia_api_key"] = request.nvidia_api_key.get_secret_value()
        values["vector_api_key"] = (
            request.vector_api_key.get_secret_value() or None
            if request.vector_backend == "qdrant"
            else None
        )
        values["vector_password"] = (
            request.vector_password.get_secret_value() or None
            if request.vector_backend == "pgvector"
            else None
        )
        profile_text = None
        with TemporaryDirectory(prefix="policynim-setup-") as temporary:
            if request.source_mode in {"profile", "directory"}:
                if not request.source_path.strip() or request.additional_packs:
                    raise ConfigurationError(
                        "Choose a source path; extra packs need topic selection."
                    )
                values["profile" if request.source_mode == "profile" else "corpus_dir"] = (
                    Path(request.source_path).expanduser().absolute()
                )
            elif request.source_mode == "topics" or request.additional_packs:
                if request.source_mode == "topics":
                    if not request.topics or len(set(request.topics)) != len(request.topics):
                        raise ConfigurationError("Select at least one unique policy topic.")
                    if set(request.topics) - set(TOPICS):
                        raise ConfigurationError("Select a supported policy topic.")
                    roots = [resolve_corpus_root() / topic for topic in request.topics]
                else:
                    roots = [resolve_corpus_root()]
                roots.extend(
                    Path(path).expanduser().absolute() for path in request.additional_packs
                )
                profile_path = Path(temporary) / "profile.toml"
                profile = initialize_profile(profile_path, roots, profile_id="onboarding")
                absolute_pins = [
                    pin.model_copy(update={"path": str(root.resolve())})
                    for pin, root in zip(profile.packs, roots, strict=True)
                ]
                profile_text = serialize_profile(
                    profile.model_copy(update={"packs": absolute_pins})
                )
                profile_path.write_text(profile_text, encoding="utf-8")
                values["profile"] = profile_path
            values["_env_file"] = None
            settings = Settings(**values)
            catalog = build_policy_catalog(settings=settings, source_only=True)
            yield settings, catalog, profile_text

    def _digest(self, request: OnboardingRequest, catalog: PolicyCatalog) -> str:
        """Bind choices, credentials, and source pins without exposing a password hash."""
        payload = request.model_dump(mode="json", exclude={"task", "review_digest", "consent"})
        for field in ("nvidia_api_key", "vector_api_key", "vector_password"):
            payload[field] = getattr(request, field).get_secret_value()
        payload["catalog_digest"] = catalog.digest
        return hmac.new(
            self._key, json.dumps(payload, sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()

    def run(self, action: str, request: OnboardingRequest) -> dict[str, Any]:
        """Serialize setup effects and refuse stale preview, missing consent, or skipped gates."""
        if not self._lock.acquire(blocking=False):
            raise ConfigurationError("Setup is busy. Wait for the current operation, then retry.")
        try:
            with self._selection(request) as (settings, catalog, profile_text):
                digest = self._digest(request, catalog)
                if action == "preview":
                    if self._reviewed != digest:
                        self._built = self._verified = self._generation = None
                    self._reviewed = digest
                    return {
                        "status": "reviewed",
                        "review_digest": digest,
                        "catalog": catalog.model_dump(mode="json"),
                        "generation_id": self._generation if self._built == digest else None,
                        "verified": self._verified == (digest, request.task),
                        "privacy": "Effective policy text and task context can reach NVIDIA. "
                        "External storage receives vector identifiers, domains, and embeddings.",
                    }
                if digest != self._reviewed or request.review_digest != digest:
                    raise ConfigurationError("Choices or policy sources changed. Preview again.")
                if action == "probe":
                    return probe_vector_backend(settings)
                if not request.consent:
                    raise ConfigurationError(
                        "Approve the displayed data flow before provider work."
                    )
                if action == "build":
                    return self._build(settings, digest, catalog.digest)
                self._require_current(settings, digest)
                if action == "verify":
                    return self._verify(settings, request.task, digest)
                if action != "save" or self._verified != (digest, request.task):
                    raise ConfigurationError("Verify a policy-backed result before saving setup.")
                return self._save(settings, profile_text)
        finally:
            self._lock.release()

    def _build(self, settings: Settings, digest: str, catalog_digest: str) -> dict[str, Any]:
        """Publish the reviewed selection after consent and explicit external-write approval."""
        self._verified = None
        with create_ingest_service(
            settings,
            sync_vectors=settings.vector_backend != "sqlite",
            expected_catalog_digest=catalog_digest,
        ) as service:
            result = service.run()
        self._built, self._generation = digest, result.generation_id
        return {"status": "active", **result.model_dump(mode="json")}

    def _require_current(self, settings: Settings, digest: str) -> None:
        """Reject skipped builds or an active generation changed by another operator."""
        if self._built != digest or self._generation is None:
            raise ConfigurationError("Build and validate this selection before continuing.")
        with closing(create_index_store(settings)) as store:
            store.validate_identity()
            if store.generation_id != self._generation:
                raise ConfigurationError("Active generation changed. Build and verify again.")

    def _verify(self, settings: Settings, task: str, digest: str) -> dict[str, Any]:
        """Advance only after a source-backed result from the generation this session built."""
        self._verified = None
        if not task.strip():
            raise ConfigurationError("Enter a task to verify the first policy result.")
        with create_preflight_service(settings) as service:
            answer = service.preflight(PreflightRequest(task=task, top_k=5))
        if answer.insufficient_context or not answer.citations:
            raise ConfigurationError(
                "Policy evidence was insufficient. Inspect the selected packs, "
                "narrow the task, and verify again."
            )
        if answer.generation_id != self._generation:
            raise ConfigurationError("Result generation changed. Verify again.")
        self._verified = (digest, task)
        return {"status": "verified", "result": answer.model_dump(mode="json")}

    def _save(self, settings: Settings, profile_text: str | None) -> dict[str, Any]:
        """Publish only new profile/config files and preserve manual or concurrent edits."""
        warnings: list[str] = []
        created_profile: tuple[Path, tuple[int, int, int, int, int], str] | None = None
        profile_path = settings.profile
        if profile_text is not None:
            profile_path = self.destination.with_name(self.destination.name + ".profile.toml")
            if profile_path.exists() or profile_path.is_symlink():
                observed = profile_path.lstat()
                if (observed.st_dev, observed.st_ino) != self._pending_profile_identity or (
                    profile_path.read_text(encoding="utf-8") != profile_text
                ):
                    raise ConfigurationError(
                        "Setup profile already exists; choose a new config path."
                    )
            else:
                warnings.extend(_write_new_private(profile_path, profile_text))
                observed = profile_path.lstat()
                self._pending_profile_identity = (observed.st_dev, observed.st_ino)
                created_profile = (profile_path, _profile_identity(profile_path), profile_text)
        fields: dict[str, str] = {
            "NVIDIA_API_KEY": settings.nvidia_api_key or "",
            "POLICYNIM_NVIDIA_EMBED_MODEL": settings.nvidia_embed_model,
            "POLICYNIM_NVIDIA_CHAT_MODEL": settings.nvidia_chat_model,
            "POLICYNIM_NVIDIA_RERANK_MODEL": settings.nvidia_rerank_model,
            "POLICYNIM_NVIDIA_BASE_URL": settings.nvidia_base_url,
            "POLICYNIM_NVIDIA_RETRIEVAL_BASE_URL": settings.nvidia_retrieval_base_url,
            "POLICYNIM_NVIDIA_TIMEOUT_SECONDS": str(settings.nvidia_timeout_seconds),
            "POLICYNIM_NVIDIA_MAX_RETRIES": str(settings.nvidia_max_retries),
            "POLICYNIM_NVIDIA_RETRY_INITIAL_SECONDS": str(settings.nvidia_retry_initial_seconds),
            "POLICYNIM_NVIDIA_RETRY_MAX_SECONDS": str(settings.nvidia_retry_max_seconds),
            "POLICYNIM_OPERATION_TIMEOUT_SECONDS": str(settings.operation_timeout_seconds),
            "POLICYNIM_EMBED_BATCH_SIZE": str(settings.embed_batch_size),
            "POLICYNIM_DEFAULT_TOP_K": str(settings.default_top_k),
            "POLICYNIM_GENERATIONS_DIR": str(resolve_runtime_path(settings.generations_dir)),
            "POLICYNIM_VECTOR_BACKEND": settings.vector_backend,
            "POLICYNIM_VECTOR_NAMESPACE": settings.vector_namespace,
            "POLICYNIM_VECTOR_TIMEOUT_SECONDS": str(settings.vector_timeout_seconds),
        }
        if profile_path is not None:
            fields["POLICYNIM_PROFILE"] = str(profile_path)
        elif settings.corpus_dir is not None:
            fields["POLICYNIM_CORPUS_DIR"] = str(settings.corpus_dir)
        if settings.vector_url:
            fields["POLICYNIM_VECTOR_URL"] = settings.vector_url
        for name in ("vector_api_key", "vector_password"):
            secret = getattr(settings, name)
            if secret:
                fields["POLICYNIM_" + name.upper()] = secret.get_secret_value()
        contents = (
            LITERAL_CONFIG_HEADER
            + "\n"
            + "\n".join(_env_line(key, value) for key, value in fields.items())
            + "\n"
        )
        try:
            warnings.extend(_write_new_private(self.destination, contents))
        except OSError as error:
            if created_profile is not None:
                if _remove_created_profile(*created_profile):
                    self._pending_profile_identity = None
                else:
                    raise ConfigurationError(
                        "Config wasn't saved. The generated profile changed or couldn't be "
                        "removed and was preserved. Inspect the profile beside the config "
                        "before retrying with a new --config path."
                    ) from None
            if not isinstance(error, FileExistsError):
                raise ConfigurationError(
                    "Config couldn't be saved. Check output permissions and retry; "
                    "existing files were preserved."
                ) from None
            raise ConfigurationError(
                "Config already exists and wasn't overwritten. "
                "Restart setup with --config NEW-PATH."
            ) from None
        self._saved = True
        prefix = "POLICYNIM_CONFIG_FILE=" + shlex.quote(str(self.destination))
        if os.name == "nt":
            quoted = str(self.destination).replace("'", "''")
            prefix = "$env:POLICYNIM_CONFIG_FILE = '" + quoted + "';"
        return {
            "status": "saved",
            "config_path": str(self.destination),
            "warnings": warnings,
            "generation_id": self._generation,
            "commands": [
                prefix + " policynim vector inspect --format json",
                prefix + " policynim mcp --transport stdio",
            ],
        }
