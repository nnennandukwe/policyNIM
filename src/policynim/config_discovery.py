"""Helpers for installed config discovery and standalone data defaults."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path, user_data_path

APP_NAME = "policynim"
APP_AUTHOR = "PolicyNIM"


@dataclass(frozen=True)
class StandalonePaths:
    """User-owned config and data paths for installed standalone use."""

    config_file: Path
    data_root: Path
    lancedb_uri: Path
    runtime_rules_artifact_path: Path
    runtime_evidence_db_path: Path
    eval_workspace_dir: Path


@dataclass(frozen=True)
class ConfigDiscovery:
    """Resolved env-file discovery order for env-backed settings loads."""

    env_files: tuple[Path, ...]
    active_config_file: Path | None
    user_config_file: Path
    has_discovered_config: bool


def standalone_paths() -> StandalonePaths:
    """Return the installed-runtime config and data layout."""
    config_root = user_config_path(APP_NAME, APP_AUTHOR)
    data_root = user_data_path(APP_NAME, APP_AUTHOR)
    return StandalonePaths(
        config_file=config_root / "config.env",
        data_root=data_root,
        lancedb_uri=data_root / "lancedb",
        runtime_rules_artifact_path=data_root / "runtime" / "runtime_rules.json",
        runtime_evidence_db_path=data_root / "runtime" / "runtime_evidence.sqlite3",
        eval_workspace_dir=data_root / "evals" / "workspace",
    )


def resolve_init_config_file(*, environ: Mapping[str, str] | None = None) -> Path:
    """Return the config file that `policynim init` should write."""
    process_env = os.environ if environ is None else environ
    explicit_config = _explicit_config_file(process_env)
    if explicit_config is not None:
        return explicit_config
    return standalone_paths().config_file


def discover_config_files(
    *,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ConfigDiscovery:
    """Return env files in low-to-high priority order for settings loading."""
    current_dir = Path.cwd() if cwd is None else cwd
    process_env = os.environ if environ is None else environ
    standalone = standalone_paths()

    env_files: list[Path] = []
    active_config_file: Path | None = None

    if not is_source_checkout(cwd=current_dir) and not is_hosted_process_environment(process_env):
        if standalone.config_file.is_file():
            env_files.append(standalone.config_file)
            active_config_file = standalone.config_file

    cwd_env_file = current_dir / ".env"
    if cwd_env_file.is_file():
        env_files.append(cwd_env_file)
        active_config_file = cwd_env_file

    explicit_config_file = _explicit_config_file(process_env)
    if explicit_config_file is not None and explicit_config_file.is_file():
        if explicit_config_file not in env_files:
            env_files.append(explicit_config_file)
        active_config_file = explicit_config_file

    return ConfigDiscovery(
        env_files=tuple(env_files),
        active_config_file=active_config_file,
        user_config_file=standalone.config_file,
        has_discovered_config=active_config_file is not None,
    )


def standalone_setup_missing(
    *,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether installed local CLI setup has not been initialized yet."""
    current_dir = Path.cwd() if cwd is None else cwd
    process_env = os.environ if environ is None else environ
    if is_source_checkout(cwd=current_dir) or is_hosted_process_environment(process_env):
        return False
    if str(process_env.get("NVIDIA_API_KEY", "")).strip():
        return False
    explicit_config_file = _explicit_config_file(process_env)
    raw_explicit_config = process_env.get("POLICYNIM_CONFIG_FILE")
    if raw_explicit_config is not None and explicit_config_file is None:
        return False
    if explicit_config_file is not None:
        return not _env_file_has_nonempty_value(explicit_config_file, "NVIDIA_API_KEY")
    discovery = discover_config_files(cwd=current_dir, environ=process_env)
    return not any(
        _env_file_has_nonempty_value(config_file, "NVIDIA_API_KEY")
        for config_file in discovery.env_files
    )


def build_init_config_contents(
    *,
    api_key: str,
    corpus_dir: Path | str | None,
) -> str:
    """Return the env-file contents for standalone local CLI setup."""
    normalized_api_key = str(api_key).strip()
    if not normalized_api_key:
        raise ValueError("NVIDIA_API_KEY is required.")

    standalone = standalone_paths()
    lines = [_env_assignment("NVIDIA_API_KEY", normalized_api_key)]

    normalized_corpus_dir = normalize_init_corpus_dir(corpus_dir)
    if normalized_corpus_dir is not None:
        lines.append(_env_assignment("POLICYNIM_CORPUS_DIR", normalized_corpus_dir.as_posix()))

    lines.extend(
        [
            _env_assignment("POLICYNIM_LANCEDB_URI", standalone.lancedb_uri.as_posix()),
            _env_assignment(
                "POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH",
                standalone.runtime_rules_artifact_path.as_posix(),
            ),
            _env_assignment(
                "POLICYNIM_RUNTIME_EVIDENCE_DB_PATH",
                standalone.runtime_evidence_db_path.as_posix(),
            ),
            _env_assignment(
                "POLICYNIM_EVAL_WORKSPACE_DIR",
                standalone.eval_workspace_dir.as_posix(),
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def write_init_config_file(
    *,
    destination: Path,
    api_key: str,
    corpus_dir: Path | str | None,
) -> Path:
    """Write the standalone init config atomically and return the destination."""
    resolved_destination = destination.expanduser()
    contents = build_init_config_contents(api_key=api_key, corpus_dir=corpus_dir)
    resolved_destination.parent.mkdir(parents=True, exist_ok=True)

    handle, temp_name = tempfile.mkstemp(
        dir=resolved_destination.parent,
        prefix=f".{resolved_destination.name}.",
        suffix=".tmp",
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        try:
            output = os.fdopen(handle, "w", encoding="utf-8")
        except OSError:
            with suppress(OSError):
                os.close(handle)
            raise

        with output:
            output.write(contents)
        os.replace(temp_path, resolved_destination)
    except OSError:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise
    return resolved_destination


def is_source_checkout(*, cwd: Path | None = None) -> bool:
    """Return whether PolicyNIM is running from a source checkout."""
    return find_source_checkout_root(cwd=cwd) is not None


def find_source_checkout_root(*, cwd: Path | None = None) -> Path | None:
    """Locate a contributor checkout from the current directory or package path."""
    current_dir = Path.cwd() if cwd is None else cwd
    candidate_roots = (current_dir, Path(__file__).resolve().parent)
    seen: set[Path] = set()

    for root in candidate_roots:
        for candidate in (root, *root.parents):
            resolved = candidate.resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            if _looks_like_source_checkout(resolved):
                return resolved

    return None


def is_hosted_process_environment(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the process already looks like a platform-hosted runtime."""
    process_env = os.environ if environ is None else environ
    return bool(str(process_env.get("PORT", "")).strip())


def _explicit_config_file(environ: Mapping[str, str]) -> Path | None:
    raw_value = str(environ.get("POLICYNIM_CONFIG_FILE", "")).strip()
    if not raw_value:
        return None
    return Path(raw_value).expanduser()


def _env_file_has_nonempty_value(path: Path, key: str) -> bool:
    """Return whether an env file defines a non-empty value for a specific key."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        candidate_key, separator, raw_value = stripped.partition("=")
        if separator and candidate_key.strip() == key and _env_value_is_nonempty(raw_value):
            return True
    return False


def _env_value_is_nonempty(raw_value: str) -> bool:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return bool(value)


def normalize_init_corpus_dir(value: Path | str | None) -> Path | None:
    """Normalize optional init prompt paths to stable absolute paths."""
    if value is None:
        return None
    raw_value = value.as_posix() if isinstance(value, Path) else str(value)
    stripped = raw_value.strip()
    if not stripped:
        return None

    candidate = Path(stripped).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve(strict=False)
    else:
        candidate = candidate.resolve(strict=False)
    if not candidate.is_dir():
        raise ValueError(
            f"Custom corpus directory {candidate} does not exist or is not a directory."
        )
    return candidate


def _env_assignment(key: str, value: str) -> str:
    return f"{key}={_quote_env_value(value)}"


def _quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"'{escaped}'"


def _looks_like_source_checkout(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (path / "src" / "policynim").is_dir()
