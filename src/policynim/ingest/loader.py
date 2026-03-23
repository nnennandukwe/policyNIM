"""Corpus discovery and document loading for PolicyNIM ingest."""

from __future__ import annotations

from pathlib import Path

from policynim.errors import InvalidPolicyDocumentError
from policynim.ingest.parser import DocumentParser, MarkdownParser
from policynim.types import DocumentSection, ParsedDocument


def discover_policy_paths(root: Path | str) -> list[Path]:
    """Return all Markdown policy files under the provided corpus root."""
    root = Path(root)
    if not root.exists():
        raise InvalidPolicyDocumentError(
            f"Policy root {root} does not exist. Set `POLICYNIM_CORPUS_DIR` to override "
            "the default corpus location."
        )
    if not root.is_dir():
        raise InvalidPolicyDocumentError(
            f"Policy root {root} is not a directory. Set `POLICYNIM_CORPUS_DIR` to a "
            "directory containing policy Markdown files."
        )

    return sorted(path for path in root.rglob("*.md") if path.name != "TEMPLATE.md")


def load_policy_documents(
    root: Path | str,
    *,
    parser: DocumentParser | None = None,
) -> list[ParsedDocument]:
    """Load and normalize every policy document under the corpus root."""
    active_parser = parser or MarkdownParser()
    documents: list[ParsedDocument] = []
    seen_policy_ids: dict[str, str] = {}

    for path in discover_policy_paths(root):
        source_path = _repo_relative_path(path, root)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InvalidPolicyDocumentError(
                f"Could not read policy document {source_path}."
            ) from exc

        document = active_parser.parse(source_path, text)
        duplicate_path = seen_policy_ids.get(document.metadata.policy_id)
        if duplicate_path is not None:
            raise InvalidPolicyDocumentError(
                "Duplicate effective policy_id "
                f"{document.metadata.policy_id!r} in {duplicate_path} and {source_path}."
            )

        seen_policy_ids[document.metadata.policy_id] = source_path
        documents.append(document)

    return documents


def load_policy_document(
    path: Path | str,
    *,
    parser: DocumentParser | None = None,
) -> ParsedDocument:
    """Load and normalize one policy document."""
    active_parser = parser or MarkdownParser()
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidPolicyDocumentError(f"Could not read policy document {file_path}.") from exc

    source_path = _default_repo_relative_path(file_path)
    return active_parser.parse(source_path, text)


def load_policy_sections(
    path: Path | str,
    *,
    parser: DocumentParser | None = None,
) -> list[DocumentSection]:
    """Load one policy document and extract its sections."""
    active_parser = parser or MarkdownParser()
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidPolicyDocumentError(f"Could not read policy document {file_path}.") from exc

    source_path = _default_repo_relative_path(file_path)
    document = active_parser.parse(source_path, text)
    return active_parser.extract_sections(document)


def _repo_relative_path(path: Path, root: Path) -> str:
    """Return a stable repo-relative path for one policy source file."""
    root_parent = root.parent
    try:
        return path.relative_to(root_parent).as_posix()
    except ValueError:
        return path.resolve(strict=False).as_posix()


def _default_repo_relative_path(path: Path) -> str:
    """Best-effort repo-relative path for a single source file."""
    resolved = path.resolve(strict=False)
    if "policies" in resolved.parts:
        policies_index = resolved.parts.index("policies")
        return Path(*resolved.parts[policies_index:]).as_posix()
    return resolved.as_posix()
