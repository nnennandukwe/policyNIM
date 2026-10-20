"""Grounded, generation-bound application operations for MCP clients."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from contextlib import closing
from threading import Lock
from uuid import uuid4

from policynim.errors import PolicyNIMError
from policynim.runtime_paths import resolve_runtime_path
from policynim.services.authorization import AuthenticatedPrincipal, authorize_operation
from policynim.services.compiler import create_policy_compiler_service
from policynim.services.evidence_trace import create_policy_evidence_trace_service
from policynim.services.generations import GenerationStore
from policynim.services.preflight import create_preflight_service
from policynim.settings import Settings
from policynim.storage.sqlite_vec import SQLiteVecIndexStore
from policynim.types import (
    Citation,
    CompileRequest,
    CompileResult,
    EmbeddingIdentity,
    PreflightEvidenceTraceResult,
    PreflightRequest,
)

_GENERATION_RE = re.compile(r"[a-f0-9]{64}\Z")
_KEY_RE = re.compile(r"[a-f0-9]{64}\Z")


class PolicyResourceError(PolicyNIMError):
    """Reject invalid or unavailable immutable policy references."""


def resource_key(identifier: str) -> str:
    """Hash a logical identifier into a bounded stable URI key, even for long headings."""
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()


def _validate_key(key: str) -> None:
    """Accept only fixed-length logical keys; never interpret them as paths."""
    if not _KEY_RE.fullmatch(key):
        raise PolicyResourceError("invalid_reference: Use a resource URI returned by PolicyNIM.")


def chunk_resource_uri(generation_id: str, chunk_id: str) -> str:
    """Return an immutable logical chunk reference."""
    return f"policynim://generations/{generation_id}/chunks/{resource_key(chunk_id)}"


def policy_resource_uri(generation_id: str, policy_id: str) -> str:
    """Return an immutable logical policy reference."""
    return f"policynim://generations/{generation_id}/policies/{resource_key(policy_id)}"


def _generation_settings(settings: Settings, generation_id: str) -> Settings:
    """Require an explicit generation identity before any historical lookup."""
    if not _GENERATION_RE.fullmatch(generation_id):
        raise PolicyResourceError("invalid_reference: A generation ID must contain 64 hex digits.")
    return settings.model_copy(update={"generation_id": generation_id}, deep=True)


def _generation_store(settings: Settings) -> GenerationStore:
    """Compose the existing validated generation reader without creating model clients."""
    return GenerationStore(
        root=resolve_runtime_path(settings.generations_dir),
        embedding=EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        ),
    )


def read_policy_catalog(
    settings: Settings, *, generation_id: str | None = None
) -> dict[str, object]:
    """Describe the effective catalog and stable source links without provider calls."""
    if generation_id is not None:
        settings = _generation_settings(settings, generation_id)
    generations = _generation_store(settings)
    selected = (
        generations.pin_retained(generation_id)
        if generation_id is not None
        else generations.pin(settings.generation_id)
    )
    with selected as snapshot:
        snapshot.validate_read()
        catalog = snapshot.indexed_catalog.model_dump(mode="json")
        catalog["resource_uri"] = f"policynim://generations/{snapshot.generation_id}/catalog"
        catalog["policy_resources"] = [
            {
                "policy_id": document.metadata.policy_id,
                "uri": policy_resource_uri(snapshot.generation_id, document.metadata.policy_id),
            }
            for document in snapshot.documents
        ]
        return catalog


def read_policy_resource(
    settings: Settings, *, generation_id: str, policy_key: str
) -> dict[str, object]:
    """Read one retained parsed source body by exact catalog identifier."""
    settings = _generation_settings(settings, generation_id)
    _validate_key(policy_key)
    with _generation_store(settings).pin_retained(generation_id) as snapshot:
        snapshot.validate_read()
        for document in snapshot.documents:
            if resource_key(document.metadata.policy_id) == policy_key:
                return {
                    "generation_id": snapshot.generation_id,
                    "resource_uri": policy_resource_uri(
                        snapshot.generation_id, document.metadata.policy_id
                    ),
                    "document": document.model_dump(mode="json"),
                }
    raise PolicyResourceError("unknown_policy: This policy isn't in the selected generation.")


_CHUNK_KEYS: OrderedDict[tuple[str, str], dict[str, str]] = OrderedDict()
_CHUNK_KEYS_LOCK = Lock()


def _chunk_key_lookup(store: SQLiteVecIndexStore, generation_id: str) -> dict[str, str]:
    """Cache IDs only after the caller's leased index passes validation around its read."""
    snapshot = store.generation_snapshot
    assert snapshot is not None
    snapshot.validate_read()
    key = (str(store.path), generation_id)
    with _CHUNK_KEYS_LOCK:
        cached = _CHUNK_KEYS.get(key)
        if cached is not None:
            _CHUNK_KEYS.move_to_end(key)
            return cached
    identifiers = {resource_key(chunk_id): chunk_id for chunk_id in store.list_chunk_ids()}
    snapshot.validate_read()
    with _CHUNK_KEYS_LOCK:
        _CHUNK_KEYS[key] = identifiers
        _CHUNK_KEYS.move_to_end(key)
        while len(_CHUNK_KEYS) > 8:
            _CHUNK_KEYS.popitem(last=False)
    return identifiers


def read_chunk_resource(
    settings: Settings, *, generation_id: str, chunk_key: str
) -> dict[str, object]:
    """Read an exact indexed chunk without allowing a caller to select a filesystem path."""
    settings = _generation_settings(settings, generation_id)
    _validate_key(chunk_key)
    with _generation_store(settings).pin_retained(generation_id) as snapshot:
        with closing(
            SQLiteVecIndexStore(
                path=snapshot.index_path,
                embedding_identity=snapshot.manifest.embedding,
                generation_snapshot=snapshot,
            )
        ) as store:
            chunk_id = _chunk_key_lookup(store, generation_id).get(chunk_key)
            snapshot.validate_read()
            chunk = store.get_chunk(chunk_id) if chunk_id is not None else None
            if chunk is not None:
                return {
                    "generation_id": generation_id,
                    "resource_uri": chunk_resource_uri(generation_id, chunk.chunk_id),
                    "policy_uri": policy_resource_uri(generation_id, chunk.policy.policy_id),
                    "chunk": chunk.model_dump(mode="json"),
                }
    raise PolicyResourceError("unknown_chunk: This chunk isn't in the selected generation.")


def _linked_citations(citations: list[Citation], generation_id: str | None) -> list[Citation]:
    """Attach immutable links only when the pipeline returned an authoritative generation."""
    if generation_id is None:
        return citations
    return [
        citation.model_copy(
            update={"resource_uri": chunk_resource_uri(generation_id, citation.chunk_id)}
        )
        for citation in citations
    ]


def compile_policy(
    settings: Settings,
    request: CompileRequest,
    *,
    principal: AuthenticatedPrincipal | None = None,
) -> CompileResult:
    """Compile effective generation constraints using the existing owned service graph."""
    if principal is not None:
        authorize_operation(principal, "policy_compile")
    with create_policy_compiler_service(settings) as service:
        result = service.compile(request)
    packet = result.packet.model_copy(
        update={"citations": _linked_citations(result.packet.citations, result.generation_id)}
    )
    return result.model_copy(update={"packet": packet})


def preflight_evidence(
    settings: Settings,
    request: PreflightRequest,
    *,
    principal: AuthenticatedPrincipal | None = None,
) -> PreflightEvidenceTraceResult:
    """Trace one existing preflight execution, retaining its exact compiled packet identity."""
    if principal is not None:
        authorize_operation(principal, "policy_preflight_evidence")
    with create_preflight_service(settings) as service:
        traced = service.preflight_with_trace(request, principal=principal)
    run_id = uuid4().hex
    result = traced.result.model_copy(
        update={
            "run_id": run_id,
            "citations": _linked_citations(traced.result.citations, traced.result.generation_id),
        }
    )
    evidence = create_policy_evidence_trace_service().build(traced)
    evidence = evidence.model_copy(update={"run_id": run_id})
    return PreflightEvidenceTraceResult(result=result, evidence_trace=evidence)
