"""Tests for runtime path resolution across dev and packaged installs."""

from __future__ import annotations

from pathlib import Path

import pytest

from policynim.errors import InvalidPolicyDocumentError
from policynim.runtime_paths import (
    resolve_corpus_root,
    resolve_eval_suite_path,
    resolve_runtime_path,
)


def test_resolve_runtime_path_uses_current_working_directory(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    assert resolve_runtime_path(Path("data/lancedb")) == workspace / "data/lancedb"


def test_resolve_corpus_root_prefers_configured_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    corpus_root = tmp_path / "custom-corpus"
    corpus_root.mkdir()
    monkeypatch.chdir(workspace)

    assert resolve_corpus_root(Path("../custom-corpus")) == corpus_root


def test_resolve_corpus_root_finds_bundled_package_policies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "site-packages" / "policynim"
    bundled_corpus = package_root / "policies"
    bundled_corpus.mkdir(parents=True)
    monkeypatch.setattr(
        "policynim.runtime_paths._resolve_packaged_resource",
        lambda *parts: bundled_corpus,
    )

    assert resolve_corpus_root() == bundled_corpus


def test_resolve_corpus_root_ignores_empty_string_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "site-packages" / "policynim"
    bundled_corpus = package_root / "policies"
    bundled_corpus.mkdir(parents=True)
    monkeypatch.setattr(
        "policynim.runtime_paths._resolve_packaged_resource",
        lambda *parts: bundled_corpus,
    )

    assert resolve_corpus_root("") == bundled_corpus


def test_resolve_corpus_root_falls_back_to_checkout_when_package_resource_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout_root = tmp_path / "checkout"
    checkout_corpus = checkout_root / "policies"
    checkout_corpus.mkdir(parents=True)
    package_root = checkout_root / ".venv" / "lib" / "site-packages" / "policynim"
    package_root.mkdir(parents=True)
    monkeypatch.setattr("policynim.runtime_paths.__file__", str(package_root / "runtime_paths.py"))

    assert resolve_corpus_root() == checkout_corpus


def test_resolve_corpus_root_raises_with_override_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    isolated_package = tmp_path / "isolated" / "policynim"
    isolated_package.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "policynim.runtime_paths.__file__", str(isolated_package / "runtime_paths.py")
    )

    with pytest.raises(InvalidPolicyDocumentError, match="POLICYNIM_CORPUS_DIR"):
        resolve_corpus_root()


def test_resolve_eval_suite_path_finds_bundled_package_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "site-packages" / "policynim"
    bundled_suite = package_root / "evals" / "default_cases.json"
    bundled_suite.parent.mkdir(parents=True)
    bundled_suite.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        "policynim.runtime_paths._resolve_packaged_resource",
        lambda *parts: bundled_suite,
    )

    assert resolve_eval_suite_path() == bundled_suite


def test_resolve_eval_suite_path_falls_back_to_checkout_when_package_resource_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout_root = tmp_path / "checkout"
    checkout_suite = checkout_root / "evals" / "default_cases.json"
    checkout_suite.parent.mkdir(parents=True)
    checkout_suite.write_text("[]", encoding="utf-8")
    package_root = checkout_root / ".venv" / "lib" / "site-packages" / "policynim"
    package_root.mkdir(parents=True)
    monkeypatch.setattr("policynim.runtime_paths.__file__", str(package_root / "runtime_paths.py"))

    assert resolve_eval_suite_path() == checkout_suite


def test_resolve_eval_suite_path_raises_with_override_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    isolated_package = tmp_path / "isolated" / "policynim"
    isolated_package.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "policynim.runtime_paths.__file__", str(isolated_package / "runtime_paths.py")
    )

    with pytest.raises(InvalidPolicyDocumentError, match="default eval suite"):
        resolve_eval_suite_path()


def test_resolve_eval_suite_path_raises_with_actionable_recovery_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    isolated_package = tmp_path / "isolated" / "policynim"
    isolated_package.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "policynim.runtime_paths.__file__", str(isolated_package / "runtime_paths.py")
    )

    with pytest.raises(InvalidPolicyDocumentError) as exc:
        resolve_eval_suite_path()

    message = str(exc.value)
    assert "source checkout" in message
    assert "reinstall" in message.lower()


def test_resolve_eval_suite_path_error_no_longer_references_cases_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    isolated_package = tmp_path / "isolated" / "policynim"
    isolated_package.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "policynim.runtime_paths.__file__", str(isolated_package / "runtime_paths.py")
    )

    with pytest.raises(InvalidPolicyDocumentError) as exc:
        resolve_eval_suite_path()

    assert "--cases" not in str(exc.value)
