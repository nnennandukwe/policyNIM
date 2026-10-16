"""Credential-free container deployment contracts and actual public bundle validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from policynim.settings import Settings
from policynim.storage.generations import GenerationStore
from policynim.types import EmbeddingIdentity

REPO_ROOT = Path(__file__).resolve().parents[1]
NO_ENV: dict[str, Any] = {"_env_file": None}


@pytest.mark.parametrize("filename", ["Dockerfile", "Dockerfile.railway"])
def test_container_imports_verified_bundle_without_provider_credentials(filename: str) -> None:
    """Both image paths import the same complete artifact with source freshness checking."""
    text = (REPO_ROOT / filename).read_text()
    assert "NVIDIA_API_KEY" not in text
    assert "policynim ingest" not in text
    assert "type=secret" not in text
    assert "policynim generation import" in text
    assert '--expect-digest "$(cat artifacts/public-generation/generation.sha256)"' in text
    assert "--require-source-match" in text
    assert "COPY --from=builder /app/data/generations /app/data/generations" in text
    assert "POLICYNIM_GENERATIONS_DIR=/app/data/generations" in text
    assert "COPY pyproject.toml uv.lock README.md LICENSE ./" in text
    assert "uv sync --frozen" in text


def test_checked_in_public_bundle_matches_current_sources_and_default_identity(
    tmp_path: Path,
) -> None:
    """Inspect real SQLite, hashes, source text, runtime rules, notices, and model offline."""
    settings = Settings(
        **NO_ENV, corpus_dir=REPO_ROOT / "policies", generations_dir=tmp_path / "generations"
    )
    identity = EmbeddingIdentity(
        model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
    )
    store = GenerationStore(root=settings.generations_dir, embedding=identity)
    bundle_dir = REPO_ROOT / "artifacts/public-generation"
    receipt = store.import_bundle(
        bundle_dir / "generation.zip",
        expected_digest=(bundle_dir / "generation.sha256").read_text().strip(),
        source_settings=settings,
    )
    with store.pin() as snapshot:
        assert snapshot.generation_id == receipt.generation_id
        assert snapshot.manifest.document_count > 0 and snapshot.manifest.chunk_count > 0
        assert snapshot.indexed_catalog.generation_id == receipt.generation_id
    import json

    captured = json.loads(
        (store.root / "objects" / receipt.generation_id / "snapshot.json").read_text()
    )
    notices = captured["packs"][0]["notices"]
    assert "licenses/CC-BY-SA-4.0.txt" in notices
    assert "licenses/OWASP-CHEATSHEETS-UPSTREAM.txt" in notices
    assert "licenses/MCP-UPSTREAM.txt" in notices


def test_hosted_deployment_docs_describe_offline_import() -> None:
    """Deployment docs agree with both image entry points and startup recovery."""
    text = (REPO_ROOT / "docs/hosted-beta-operations.md").read_text()
    assert "docker build -t policynim-hosted ." in text
    assert "generation import" in text
    assert "POLICYNIM_GENERATIONS_DIR" in text
    assert "--secret id=nvidia_api_key" not in text
    assert "ARG NVIDIA_API_KEY" not in text
    assert 'dockerfilePath = "Dockerfile.railway"' in (REPO_ROOT / "railway.toml").read_text()
