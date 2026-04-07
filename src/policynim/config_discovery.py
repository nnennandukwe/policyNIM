"""Helpers for installed config discovery and standalone data defaults."""

from __future__ import annotations

import os
from collections.abc import Mapping
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


def _looks_like_source_checkout(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (path / "src" / "policynim").is_dir()
