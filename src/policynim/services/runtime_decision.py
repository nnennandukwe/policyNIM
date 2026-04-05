"""Read-only runtime decision service for compiled PolicyNIM rules."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from types import TracebackType

from pydantic import ValidationError

from policynim.contracts import IndexStore
from policynim.errors import (
    MissingIndexError,
    RuntimeCitationLinkError,
    RuntimeRulesArtifactInvalidError,
    RuntimeRulesArtifactMissingError,
)
from policynim.runtime_paths import resolve_runtime_path
from policynim.settings import Settings, get_settings
from policynim.storage import LanceDBIndexStore
from policynim.types import (
    Citation,
    CompiledRuntimeRule,
    FileWriteActionRequest,
    HTTPRequestActionRequest,
    PolicyChunk,
    RuntimeActionKind,
    RuntimeActionRequest,
    RuntimeDecision,
    RuntimeDecisionResult,
    RuntimeRulesArtifact,
    ShellCommandActionRequest,
)

_ALLOW_SUMMARY = "No runtime policy rules matched this action."
_LINE_SPAN_RE = re.compile(r"^(?P<start>[1-9]\d*)-(?P<end>[1-9]\d*)$")


@dataclass(frozen=True, slots=True)
class _NormalizedRuntimeAction:
    """One normalized runtime action target used for deterministic rule matching."""

    kind: RuntimeActionKind
    match_values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _IndexedChunkSpan:
    """One indexed chunk paired with a validated inclusive line span."""

    chunk: PolicyChunk
    start_line: int
    end_line: int


class RuntimeDecisionService:
    """Load compiled runtime rules, match actions, and attach indexed evidence."""

    def __init__(
        self,
        *,
        index_store: IndexStore,
        runtime_rules_artifact_path: Path,
    ) -> None:
        self._index_store = index_store
        self._runtime_rules_artifact_path = runtime_rules_artifact_path

    def __enter__(self) -> RuntimeDecisionService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release owned resources held by this service."""
        return None

    def decide(self, request: RuntimeActionRequest) -> RuntimeDecisionResult:
        """Return a deterministic runtime decision for one action request."""
        artifact = _load_runtime_rules_artifact(self._runtime_rules_artifact_path)
        _validate_runtime_rule_matchers(
            artifact,
            artifact_path=self._runtime_rules_artifact_path,
        )
        normalized_request = _normalize_runtime_action(request)
        matched_rules = [
            rule
            for rule in artifact.rules
            if rule.action == normalized_request.kind
            and _rule_matches(rule, normalized_request=normalized_request)
        ]
        if not matched_rules:
            _ensure_index_ready(self._index_store)
            return RuntimeDecisionResult(
                request=request,
                decision="allow",
                summary=_ALLOW_SUMMARY,
                matched_rules=[],
                citations=[],
            )
        indexed_chunks = _load_indexed_chunk_spans(self._index_store)

        return RuntimeDecisionResult(
            request=request,
            decision=_decision_for_rules(matched_rules),
            summary=_summary_for_rules(matched_rules),
            matched_rules=matched_rules,
            citations=_link_citations(matched_rules, indexed_chunks=indexed_chunks),
        )


def create_runtime_decision_service(settings: Settings | None = None) -> RuntimeDecisionService:
    """Build the default runtime decision service from application settings."""
    active_settings = settings or get_settings()
    return RuntimeDecisionService(
        index_store=LanceDBIndexStore(
            uri=resolve_runtime_path(active_settings.lancedb_uri),
            table_name=active_settings.lancedb_table,
        ),
        runtime_rules_artifact_path=resolve_runtime_path(
            active_settings.runtime_rules_artifact_path
        ),
    )


def _load_runtime_rules_artifact(artifact_path: Path) -> RuntimeRulesArtifact:
    """Read and validate the compiled runtime-rules artifact from disk."""
    if not artifact_path.exists():
        raise RuntimeRulesArtifactMissingError(
            f"Runtime rules artifact not found at {artifact_path}. "
            "Run `policynim ingest` before using runtime decisions."
        )
    if artifact_path.is_dir():
        raise RuntimeRulesArtifactInvalidError(
            f"Runtime rules artifact path {artifact_path} must be a file. "
            "Run `policynim ingest` to rebuild the runtime rules artifact."
        )

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeRulesArtifactInvalidError(
            f"Could not read runtime rules artifact at {artifact_path}. "
            "Fix the file permissions or run `policynim ingest` to rebuild it."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeRulesArtifactInvalidError(
            f"Runtime rules artifact at {artifact_path} is not valid JSON. "
            "Run `policynim ingest` to rebuild the runtime rules artifact."
        ) from exc

    try:
        return RuntimeRulesArtifact.model_validate(payload)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "artifact"
        raise RuntimeRulesArtifactInvalidError(
            f"Runtime rules artifact at {artifact_path} is invalid at {location}: {error['msg']}. "
            "Run `policynim ingest` to rebuild the runtime rules artifact."
        ) from exc


def _validate_runtime_rule_matchers(
    artifact: RuntimeRulesArtifact,
    *,
    artifact_path: Path,
) -> None:
    """Reject invalid matcher metadata before any decision is attempted."""
    for rule in artifact.rules:
        if rule.action != "shell_command":
            continue
        for pattern in rule.command_regexes:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise RuntimeRulesArtifactInvalidError(
                    "Runtime rules artifact at "
                    f"{artifact_path} contains an invalid command regex {pattern!r} "
                    "for policy "
                    f"{rule.policy_id} at {rule.source_path}:{rule.start_line}-{rule.end_line}. "
                    "Fix the source policy and run `policynim ingest`."
                ) from exc


def _load_indexed_chunk_spans(index_store: IndexStore) -> list[_IndexedChunkSpan]:
    """Return indexed chunks paired with validated line spans."""
    _ensure_index_ready(index_store)
    indexed_chunks: list[_IndexedChunkSpan] = []
    for chunk in index_store.list_chunks():
        start_line, end_line = _parse_chunk_line_span(chunk)
        indexed_chunks.append(
            _IndexedChunkSpan(
                chunk=chunk,
                start_line=start_line,
                end_line=end_line,
            )
        )
    return indexed_chunks


def _ensure_index_ready(index_store: IndexStore) -> None:
    """Require a non-empty local index before runtime decisions can proceed."""
    if not index_store.exists() or index_store.count() == 0:
        raise MissingIndexError("Run `policynim ingest` before using runtime decisions.")


def _normalize_runtime_action(request: RuntimeActionRequest) -> _NormalizedRuntimeAction:
    """Normalize one runtime request into a deterministic matching target."""
    if isinstance(request, ShellCommandActionRequest):
        return _NormalizedRuntimeAction(
            kind=request.kind,
            match_values=(shlex.join(request.command),),
        )
    if isinstance(request, FileWriteActionRequest):
        return _NormalizedRuntimeAction(
            kind=request.kind,
            match_values=_normalize_file_write_paths(request),
        )
    if isinstance(request, HTTPRequestActionRequest):
        host = request.url.host
        if host is None:
            raise ValueError("http_request url must include a hostname.")
        return _NormalizedRuntimeAction(
            kind=request.kind,
            match_values=(str(host).lower(),),
        )
    raise TypeError(f"Unsupported runtime action request type: {type(request)!r}.")


def _normalize_file_write_paths(request: FileWriteActionRequest) -> tuple[str, ...]:
    """Return deterministic file-write match candidates without symlink bypasses."""
    lexical_cwd = _absolute_lexical_path(request.cwd, base=Path.cwd())
    lexical_target = _absolute_lexical_path(request.path, base=lexical_cwd)
    resolved_cwd = _resolve_from_base(request.cwd, base=Path.cwd())
    resolved_target = _resolve_from_base(request.path, base=resolved_cwd)
    candidate_paths: list[str] = []

    if request.repo_root is not None:
        lexical_repo_root = _absolute_lexical_path(request.repo_root, base=lexical_cwd)
        repo_relative = _relative_posix_path(lexical_target, root=lexical_repo_root)
        if repo_relative is not None:
            candidate_paths.append(repo_relative)

    cwd_relative = _relative_posix_path(lexical_target, root=lexical_cwd)
    if cwd_relative is not None:
        candidate_paths.append(cwd_relative)

    candidate_paths.append(lexical_target.as_posix())
    candidate_paths.append(resolved_target.as_posix())
    return tuple(_ordered_unique(candidate_paths))


def _resolve_from_base(path: Path, *, base: Path) -> Path:
    """Resolve an absolute-or-relative path against the supplied base directory."""
    if path.is_absolute():
        return path.resolve(strict=False)
    return (base / path).resolve(strict=False)


def _absolute_lexical_path(path: Path, *, base: Path) -> Path:
    """Return an absolute path with `.` and `..` collapsed without following symlinks."""
    if path.is_absolute():
        return _lexicalize_path(path)
    return _lexicalize_path(base / path)


def _lexicalize_path(path: Path) -> Path:
    """Collapse lexical path segments without touching the filesystem."""
    anchor = path.anchor
    segments: list[str] = []
    for part in path.parts:
        if part in ("", ".", anchor):
            continue
        if part == "..":
            if segments and segments[-1] != "..":
                segments.pop()
                continue
            if not anchor:
                segments.append(part)
            continue
        segments.append(part)
    if anchor:
        return Path(anchor, *segments)
    if segments:
        return Path(*segments)
    return Path(".")


def _relative_posix_path(path: Path, *, root: Path) -> str | None:
    """Return a POSIX relative path when the target is rooted under the base path."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _ordered_unique(values: list[str]) -> list[str]:
    """Keep first-seen string order while dropping duplicates."""
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        ordered.append(value)
        seen.add(value)
    return ordered


def _rule_matches(
    rule: CompiledRuntimeRule,
    *,
    normalized_request: _NormalizedRuntimeAction,
) -> bool:
    """Return whether one normalized action matches one compiled rule."""
    if rule.action != normalized_request.kind:
        return False
    if rule.action == "shell_command":
        return any(
            re.search(pattern, normalized_request.match_values[0]) is not None
            for pattern in rule.command_regexes
        )
    if rule.action == "file_write":
        return any(
            PurePosixPath(candidate).match(pattern)
            for candidate in normalized_request.match_values
            for pattern in rule.path_globs
        )
    if rule.action == "http_request":
        hostname = normalized_request.match_values[0]
        return any(fnmatchcase(hostname, pattern.lower()) for pattern in rule.url_host_patterns)
    return False


def _decision_for_rules(matched_rules: list[CompiledRuntimeRule]) -> RuntimeDecision:
    """Return the highest-precedence decision for a matched rule set."""
    if any(rule.effect == "block" for rule in matched_rules):
        return "block"
    return "confirm"


def _summary_for_rules(matched_rules: list[CompiledRuntimeRule]) -> str:
    """Return a deterministic summary from matched rule reasons."""
    return "; ".join(rule.reason for rule in matched_rules)


def _link_citations(
    matched_rules: list[CompiledRuntimeRule],
    *,
    indexed_chunks: list[_IndexedChunkSpan],
) -> list[Citation]:
    """Return deduplicated citations for every matched runtime rule."""
    citations: list[Citation] = []
    seen_chunk_ids: set[str] = set()

    for rule in matched_rules:
        linked_chunks = [
            indexed_chunk
            for indexed_chunk in indexed_chunks
            if indexed_chunk.chunk.policy.policy_id == rule.policy_id
            and indexed_chunk.chunk.path == rule.source_path
            and _line_spans_overlap(
                left_start=indexed_chunk.start_line,
                left_end=indexed_chunk.end_line,
                right_start=rule.start_line,
                right_end=rule.end_line,
            )
        ]
        if not linked_chunks:
            raise RuntimeCitationLinkError(
                "Matched runtime rule "
                f"{rule.policy_id} at {rule.source_path}:{rule.start_line}-{rule.end_line} "
                "could not be linked to indexed policy chunks. "
                "Run `policynim ingest` to rebuild the local index and runtime rules artifact."
            )
        for indexed_chunk in linked_chunks:
            chunk = indexed_chunk.chunk
            if chunk.chunk_id in seen_chunk_ids:
                continue
            citations.append(
                Citation(
                    policy_id=chunk.policy.policy_id,
                    title=chunk.policy.title,
                    path=chunk.path,
                    section=chunk.section,
                    lines=chunk.lines,
                    chunk_id=chunk.chunk_id,
                )
            )
            seen_chunk_ids.add(chunk.chunk_id)
    return citations


def _parse_chunk_line_span(chunk: PolicyChunk) -> tuple[int, int]:
    """Parse and validate one indexed chunk line span."""
    match = _LINE_SPAN_RE.fullmatch(chunk.lines)
    if match is None:
        raise RuntimeCitationLinkError(
            f"Indexed chunk {chunk.chunk_id!r} has invalid line span {chunk.lines!r}. "
            "Run `policynim ingest` to rebuild the local index."
        )
    start_line = int(match.group("start"))
    end_line = int(match.group("end"))
    if end_line < start_line:
        raise RuntimeCitationLinkError(
            f"Indexed chunk {chunk.chunk_id!r} has inverted line span {chunk.lines!r}. "
            "Run `policynim ingest` to rebuild the local index."
        )
    return start_line, end_line


def _line_spans_overlap(
    *,
    left_start: int,
    left_end: int,
    right_start: int,
    right_end: int,
) -> bool:
    """Return whether two inclusive line spans overlap."""
    return left_start <= right_end and right_start <= left_end
