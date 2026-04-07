"""Runtime path helpers for repo checkouts and installed packages."""

from __future__ import annotations

import atexit
from contextlib import ExitStack
from importlib import resources
from pathlib import Path

from policynim.errors import InvalidPolicyDocumentError

_PACKAGED_RESOURCE_STACK = ExitStack()
atexit.register(_PACKAGED_RESOURCE_STACK.close)


def resolve_runtime_path(path: Path) -> Path:
    """Resolve a configured runtime path relative to the current working directory."""
    if path.is_absolute():
        return path.resolve(strict=False)
    return (Path.cwd() / path).resolve(strict=False)


def _normalize_optional_path(value: Path | str | None) -> Path | None:
    """Normalize optional path-like input and discard empty-string overrides."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return Path(stripped)
    return value


def resolve_corpus_root(configured_root: Path | str | None = None) -> Path:
    """Resolve the policy corpus from config, bundled package data, or a source checkout."""
    normalized_root = _normalize_optional_path(configured_root)
    if normalized_root is not None:
        return resolve_runtime_path(normalized_root)

    bundled_corpus = _resolve_packaged_resource("policies")
    if bundled_corpus is not None and bundled_corpus.is_dir():
        return bundled_corpus

    checkout_corpus = _resolve_checkout_resource("policies")
    if checkout_corpus is not None:
        return checkout_corpus

    raise InvalidPolicyDocumentError(
        "Could not locate the policy corpus in installed package resources or a source checkout. "
        "Set `POLICYNIM_CORPUS_DIR` to the directory containing your policy Markdown files."
    )


def resolve_eval_suite_path() -> Path:
    """Resolve the bundled default eval suite."""
    bundled_suite = _resolve_packaged_resource("evals", "default_cases.json")
    if bundled_suite is not None and bundled_suite.is_file():
        return bundled_suite

    checkout_suite = _resolve_checkout_resource("evals", "default_cases.json")
    if checkout_suite is not None:
        return checkout_suite

    raise InvalidPolicyDocumentError(
        "Could not locate the default eval suite in installed package resources or a source "
        "checkout. Reinstall PolicyNIM or run from a source checkout that contains "
        "`evals/default_cases.json`."
    )


def _resolve_packaged_resource(*parts: str) -> Path | None:
    """Resolve a packaged resource to a filesystem path when the package ships it."""
    resource = resources.files("policynim")
    for part in parts:
        resource = resource.joinpath(part)
    if not resource.exists():
        return None
    return _PACKAGED_RESOURCE_STACK.enter_context(resources.as_file(resource))


def _resolve_checkout_resource(*parts: str) -> Path | None:
    """Resolve a checkout-relative fallback by walking parents of the package path."""
    package_root = Path(__file__).resolve().parent
    for parent in package_root.parents:
        candidate = parent.joinpath(*parts)
        if candidate.exists():
            return candidate
    return None
