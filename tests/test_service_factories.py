"""Tests for service factory import boundaries."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import policynim.services.eval as eval_module
import policynim.services.ingest as ingest_module
import policynim.services.preflight as preflight_module
import policynim.services.runtime_decision as runtime_decision_module
import policynim.services.runtime_execution as runtime_execution_module
import policynim.services.search as search_module
from policynim.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MockIndexStore:
    """Lightweight store used to validate factory wiring."""

    def __init__(self, *, uri: Path, table_name: str) -> None:
        self.uri = uri
        self.table_name = table_name


class MockRuntimeEvidenceStore:
    """Lightweight evidence store used to validate factory wiring."""

    def __init__(self, *, path: Path) -> None:
        self.path = path


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
import policynim.services.eval
import policynim.services.search
import policynim.services.preflight
import policynim.services.runtime_decision
import policynim.services.runtime_execution
    """
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_create_ingest_service_builds_default_components(monkeypatch, tmp_path: Path) -> None:
    mock_embedder = object()
    monkeypatch.setattr(ingest_module, "_create_default_embedder", lambda settings: mock_embedder)
    monkeypatch.setattr(ingest_module, "LanceDBIndexStore", MockIndexStore)

    settings = Settings(
        corpus_dir=tmp_path / "policies",
        lancedb_uri=tmp_path / "ingest-index",
        runtime_rules_artifact_path=tmp_path / "runtime" / "rules.json",
        nvidia_embed_model="test-embed-model",
    )

    service = ingest_module.create_ingest_service(settings)

    assert service._embedder is mock_embedder
    assert isinstance(service._index_store, MockIndexStore)
    assert service._index_store.uri == (tmp_path / "ingest-index").resolve(strict=False)
    assert service._index_store.table_name == settings.lancedb_table
    assert service._corpus_root == (tmp_path / "policies").resolve(strict=False)
    assert service._embedding_model == "test-embed-model"
    assert service._runtime_rules_artifact_path == (tmp_path / "runtime" / "rules.json").resolve(
        strict=False
    )


def test_create_search_service_builds_default_components(monkeypatch, tmp_path: Path) -> None:
    mock_embedder = object()
    mock_reranker = object()
    monkeypatch.setattr(
        search_module,
        "_create_default_search_components",
        lambda settings: (mock_embedder, mock_reranker),
    )
    monkeypatch.setattr(search_module, "LanceDBIndexStore", MockIndexStore)

    settings = Settings(lancedb_uri=tmp_path / "search-index")

    service = search_module.create_search_service(settings)

    assert service._embedder is mock_embedder
    assert service._reranker is mock_reranker
    assert isinstance(service._index_store, MockIndexStore)
    assert service._index_store.uri == (tmp_path / "search-index").resolve(strict=False)
    assert service._index_store.table_name == settings.lancedb_table


def test_create_preflight_service_builds_default_components(
    monkeypatch,
    tmp_path: Path,
) -> None:
    mock_embedder = object()
    mock_reranker = object()
    mock_generator = object()
    monkeypatch.setattr(
        preflight_module,
        "_create_default_preflight_components",
        lambda settings: (mock_embedder, mock_reranker, mock_generator),
    )
    monkeypatch.setattr(preflight_module, "LanceDBIndexStore", MockIndexStore)

    settings = Settings(lancedb_uri=tmp_path / "preflight-index")

    service = preflight_module.create_preflight_service(settings)

    assert service._embedder is mock_embedder
    assert service._reranker is mock_reranker
    assert service._generator is mock_generator
    assert isinstance(service._index_store, MockIndexStore)
    assert service._index_store.uri == (tmp_path / "preflight-index").resolve(strict=False)
    assert service._index_store.table_name == settings.lancedb_table


def test_create_runtime_decision_service_uses_runtime_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runtime_decision_module, "LanceDBIndexStore", MockIndexStore)

    settings = Settings(
        lancedb_uri=tmp_path / "runtime-index",
        runtime_rules_artifact_path=tmp_path / "runtime" / "runtime_rules.json",
    )

    service = runtime_decision_module.create_runtime_decision_service(settings)

    assert isinstance(service._index_store, MockIndexStore)
    assert service._index_store.uri == (tmp_path / "runtime-index").resolve(strict=False)
    assert service._index_store.table_name == settings.lancedb_table
    assert service._runtime_rules_artifact_path == (
        tmp_path / "runtime" / "runtime_rules.json"
    ).resolve(strict=False)


def test_create_eval_service_uses_runtime_workspace_path(tmp_path: Path) -> None:
    settings = Settings(eval_workspace_dir=tmp_path / "eval-workspace")

    service = eval_module.create_eval_service(settings)

    assert service.workspace_path == (tmp_path / "eval-workspace").resolve(strict=False)


def test_create_runtime_execution_service_uses_runtime_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    mock_decision_service = object()
    monkeypatch.setattr(
        runtime_execution_module,
        "create_runtime_decision_service",
        lambda settings: mock_decision_service,
    )
    monkeypatch.setattr(
        runtime_execution_module,
        "RuntimeEvidenceStore",
        MockRuntimeEvidenceStore,
    )

    settings = Settings(
        lancedb_uri=tmp_path / "runtime-index",
        runtime_rules_artifact_path=tmp_path / "runtime" / "runtime_rules.json",
        runtime_evidence_db_path=tmp_path / "runtime" / "runtime_evidence.sqlite3",
    )

    service = runtime_execution_module.create_runtime_execution_service(settings)

    assert service._decision_service is mock_decision_service
    assert isinstance(service._evidence_store, MockRuntimeEvidenceStore)
    assert service._evidence_store.path == (
        tmp_path / "runtime" / "runtime_evidence.sqlite3"
    ).resolve(strict=False)
    assert service._shell_timeout_seconds == settings.runtime_shell_timeout_seconds
