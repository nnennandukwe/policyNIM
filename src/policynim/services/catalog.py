"""Deterministic read-only source catalogs compiled before embeddings."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field

from policynim.errors import InvalidPolicyDocumentError
from policynim.ingest.loader import load_policy_documents
from policynim.runtime_paths import resolve_corpus_root
from policynim.settings import Settings, get_settings
from policynim.types import ParsedDocument, PolicyMetadata, StrictModel


class CatalogPolicy(StrictModel):
    """One selected source policy and the text users can inspect without a model."""

    state: Literal["active", "excluded", "replaced"] = "active"
    target_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reason: str | None = None
    replacement_id: str | None = None
    path: str
    policy: PolicyMetadata
    body: str


class CatalogPack(StrictModel):
    """The exact selected local pack identity shown during source inspection."""

    path: str
    pack_id: str
    version: str
    digest: str


class PolicyCatalog(StrictModel):
    """Describe authored/effective sources without claiming an indexed generation."""

    schema_version: Literal[1] = 1
    scope: Literal["source"] = "source"
    digest: str
    profile_id: str | None = None
    profile_digest: str | None = None
    pack_pins: list[CatalogPack] = Field(default_factory=list)
    authored_count: int
    effective_count: int
    indexed_count: int | None = None
    unlisted_markdown: list[str] = Field(default_factory=list)
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
    if root is None:
        active_settings = settings or get_settings()
        if active_settings.profile is not None:
            from policynim.services.profiles import resolve_profile

            return resolve_profile(active_settings.profile).catalog
    corpus = (
        root if root is not None else resolve_corpus_root((settings or get_settings()).corpus_dir)
    )
    documents = load_policy_documents(corpus)
    return compile_policy_catalog(documents, root=corpus)


def compile_policy_catalog(
    documents: Sequence[ParsedDocument],
    *,
    root: Path,
    unlisted_markdown: Sequence[str] | None = None,
) -> PolicyCatalog:
    """Compile the exact loaded source snapshot before any model work starts."""
    policies = sorted(
        [
            CatalogPolicy(
                target_digest="sha256:"
                + sha256(
                    (
                        doc.metadata.model_dump_json()
                        + "\n"
                        + doc.body
                        + "\n"
                        + doc.source_path.removeprefix(root.name + "/")
                    ).encode("utf-8")
                ).hexdigest(),
                path=doc.source_path.removeprefix(root.name + "/"),
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
    selected_paths = {entry.path for entry in policies}
    return PolicyCatalog(
        digest=digest,
        authored_count=len(policies),
        effective_count=len(policies),
        policies=policies,
        unlisted_markdown=(
            sorted(unlisted_markdown)
            if unlisted_markdown is not None
            else sorted(
                path.relative_to(root).as_posix()
                for path in root.rglob("*.md")
                if path.name != "TEMPLATE.md"
                and path.relative_to(root).as_posix() not in selected_paths
            )
        ),
    )
