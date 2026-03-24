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


def resolve_eval_suite_path() -> Path:
    """Resolve the bundled default eval suite."""
    package_root = Path(__file__).resolve().parent
    bundled_suite = package_root / "evals" / "default_cases.json"
    if bundled_suite.is_file():
        return bundled_suite

    for parent in package_root.parents:
        checkout_suite = parent / "evals" / "default_cases.json"
        if checkout_suite.is_file():
            return checkout_suite

    raise InvalidPolicyDocumentError(
        "Could not locate the default eval suite. Add `evals/default_cases.json` to the project."
    )
