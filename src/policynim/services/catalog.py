"""Deterministic read-only source catalogs compiled before embeddings."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal

from policynim.errors import InvalidPolicyDocumentError
from policynim.ingest.loader import load_policy_documents
from policynim.runtime_paths import resolve_corpus_root
from policynim.settings import Settings, get_settings
from policynim.types import PolicyMetadata, StrictModel


class CatalogPolicy(StrictModel):
    """One selected source policy and the text users can inspect without a model."""

    state: Literal["active", "excluded", "replaced"] = "active"
    path: str
    policy: PolicyMetadata
    body: str


class PolicyCatalog(StrictModel):
    """Describe authored/effective sources without claiming an indexed generation."""

    schema_version: Literal[1] = 1
    scope: Literal["source"] = "source"
    digest: str
    authored_count: int
    effective_count: int
    indexed_count: int | None = None
    policies: list[CatalogPolicy]

    def show(self, policy_id: str) -> CatalogPolicy:
        """Return an exact policy ID or an actionable missing-policy error."""
        for entry in self.policies:
            if entry.policy.policy_id == policy_id:
                return entry
        raise InvalidPolicyDocumentError(
            f"Policy ID {policy_id!r} isn't in the source catalog. Run `policynim policies list`."
        )


def build_policy_catalog(
    root: Path | None = None, *, settings: Settings | None = None
) -> PolicyCatalog:
    """Compile a stable catalog from explicit source bytes and declared metadata."""
    corpus = (
        root if root is not None else resolve_corpus_root((settings or get_settings()).corpus_dir)
    )
    documents = load_policy_documents(corpus)
    policies = sorted(
        [
            CatalogPolicy(
                path=doc.source_path.removeprefix(corpus.name + "/"),
                policy=doc.metadata,
                body=doc.body,
            )
            for doc in documents
        ],
        key=lambda entry: entry.policy.policy_id,
    )
    if not policies:
        raise InvalidPolicyDocumentError(
            "The source catalog has no policies. "
            "Check POLICYNIM_CORPUS_DIR or pack.toml membership."
        )
    digest = (
        "sha256:"
        + sha256(
            "\n".join(entry.model_dump_json() for entry in policies).encode("utf-8")
        ).hexdigest()
    )
    return PolicyCatalog(
        digest=digest,
        authored_count=len(policies),
        effective_count=len(policies),
        policies=policies,
    )
