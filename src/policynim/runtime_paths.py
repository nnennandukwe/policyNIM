"""Runtime path helpers for repo checkouts and installed packages."""

from __future__ import annotations

from pathlib import Path

from policynim.errors import InvalidPolicyDocumentError


def resolve_runtime_path(path: Path) -> Path:
    """Resolve a configured runtime path relative to the current working directory."""
    if path.is_absolute():
        return path.resolve(strict=False)
    return (Path.cwd() / path).resolve(strict=False)


def resolve_corpus_root(configured_root: Path | None = None) -> Path:
    """Resolve the policy corpus from config, bundled package data, or a source checkout."""
    if configured_root is not None:
        return resolve_runtime_path(configured_root)

    package_root = Path(__file__).resolve().parent
    bundled_corpus = package_root / "policies"
    if bundled_corpus.is_dir():
        return bundled_corpus

    for parent in package_root.parents:
        checkout_corpus = parent / "policies"
        if checkout_corpus.is_dir():
            return checkout_corpus

    raise InvalidPolicyDocumentError(
        "Could not locate the policy corpus. Set `POLICYNIM_CORPUS_DIR` to the directory "
        "containing your policy Markdown files."
    )
