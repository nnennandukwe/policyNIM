"""Tests for service factory import boundaries."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import policynim.services.ingest as ingest_module
import policynim.services.preflight as preflight_module
import policynim.services.search as search_module
from policynim.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeIndexStore:
    """Lightweight store used to validate factory wiring."""

    def __init__(self, *, uri: Path, table_name: str) -> None:
        self.uri = uri
        self.table_name = table_name


def test_service_modules_import_without_provider_package() -> None:
    script = f"""
import importlib.abc
import sys

sys.path.insert(0, {str(PROJECT_ROOT / "src")!r})

class BlockProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "policynim.providers" or fullname.startswith("policynim.providers."):
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockProviders())

import policynim.services
import policynim.services.ingest
import policynim.services.search
import policynim.services.preflight
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_create_ingest_service_builds_default_components(monkeypatch, tmp_path: Path) -> None:
    fake_embedder = object()
    monkeypatch.setattr(ingest_module, "_create_default_embedder", lambda settings: fake_embedder)
    monkeypatch.setattr(ingest_module, "LanceDBIndexStore", FakeIndexStore)

    settings = Settings(
        corpus_dir=tmp_path / "policies",
        lancedb_uri=tmp_path / "ingest-index",
        nvidia_embed_model="test-embed-model",
    )

    service = ingest_module.create_ingest_service(settings)

    assert service._embedder is fake_embedder
    assert isinstance(service._index_store, FakeIndexStore)
    assert service._index_store.uri == (tmp_path / "ingest-index").resolve(strict=False)
    assert service._index_store.table_name == settings.lancedb_table
    assert service._corpus_root == (tmp_path / "policies").resolve(strict=False)
    assert service._embedding_model == "test-embed-model"


def test_create_search_service_builds_default_components(monkeypatch, tmp_path: Path) -> None:
    fake_embedder = object()
    fake_reranker = object()
    monkeypatch.setattr(
        search_module,
        "_create_default_search_components",
        lambda settings: (fake_embedder, fake_reranker),
    )
    monkeypatch.setattr(search_module, "LanceDBIndexStore", FakeIndexStore)

    settings = Settings(lancedb_uri=tmp_path / "search-index")

    service = search_module.create_search_service(settings)

    assert service._embedder is fake_embedder
    assert service._reranker is fake_reranker
    assert isinstance(service._index_store, FakeIndexStore)
    assert service._index_store.uri == (tmp_path / "search-index").resolve(strict=False)
    assert service._index_store.table_name == settings.lancedb_table


def test_create_preflight_service_builds_default_components(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_embedder = object()
    fake_reranker = object()
    fake_generator = object()
    monkeypatch.setattr(
        preflight_module,
        "_create_default_preflight_components",
        lambda settings: (fake_embedder, fake_reranker, fake_generator),
    )
    monkeypatch.setattr(preflight_module, "LanceDBIndexStore", FakeIndexStore)

    settings = Settings(lancedb_uri=tmp_path / "preflight-index")

    service = preflight_module.create_preflight_service(settings)

    assert service._embedder is fake_embedder
    assert service._reranker is fake_reranker
    assert service._generator is fake_generator
    assert isinstance(service._index_store, FakeIndexStore)
    assert service._index_store.uri == (tmp_path / "preflight-index").resolve(strict=False)
    assert service._index_store.table_name == settings.lancedb_table
