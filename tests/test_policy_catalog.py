"""Behavior tests for inspectable versioned policy sources and indexed metadata."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from policynim.interfaces.cli import app
from policynim.settings import get_settings


@pytest.fixture(autouse=True)
def clean_catalog_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent cached settings and personal credentials from entering source-only tests."""
    for key in list(os.environ):
        if key.startswith("POLICYNIM_") or key == "NVIDIA_API_KEY":
            monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_catalog_commands_work_without_provider_credentials(monkeypatch, tmp_path: Path) -> None:
    """List, show, and validate the actual packaged policies before model setup."""
    from test_cli import configure_standalone_cli_environment

    configure_standalone_cli_environment(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["policies", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    catalog = json.loads(result.stdout)
    assert catalog["scope"] == "source"
    assert catalog["authored_count"] == catalog["effective_count"] == 15
    assert catalog["indexed_count"] is None
    assert len(catalog["policies"]) == 15
    shown = runner.invoke(app, ["policies", "show", "SEC-AUTH-001", "--format", "json"])
    assert shown.exit_code == 0, shown.output
    policy = json.loads(shown.stdout)
    assert policy["policy"]["policy_id"] == "SEC-AUTH-001"
    assert policy["policy"]["provenance"]["pack_id"] == "policynim.baseline"
    assert policy["policy"]["provenance"]["content_digest"].startswith("sha256:")
    assert "Authentication" in policy["body"] or "Auth" in policy["body"]
    valid = runner.invoke(app, ["policies", "validate", "--format", "json"])
    assert valid.exit_code == 0, valid.output
    assert json.loads(valid.stdout)["status"] == "ok"


def write_pack(root: Path, *, extra: str = "") -> Path:
    """Write an explicit one-policy pack with a complete declared reference."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "policy.md").write_text(
        "---\npolicy_id: LOG-001\ntitle: Logging\ndomain: backend\n---\n"
        "# Logging\n\n## Rules\nInclude request identifiers.\n",
        encoding="utf-8",
    )
    manifest = root / "pack.toml"
    manifest.write_text(
        'schema_version = 1\npack_id = "team.logging"\nversion = "1.0.0"\n'
        '[[policies]]\npolicy_id = "LOG-001"\npath = "policy.md"\n'
        'requirement_level = "recommended"\napplicability = ["backend"]\n'
        'grounding_kind = "adaptation"\n'
        '[[policies.sources]]\ntitle = "Logging reference"\nurl = "https://example.com/logging"\n'
        'revision = "revision-1"\nsections = ["2.1"]\nretrieved_on = 2026-09-01\n'
        'license = "CC-BY-4.0"\nattribution = "Example Authors"\n'
        'change_notice = "Adapted to require request identifiers."\n' + extra,
        encoding="utf-8",
    )
    return manifest


def test_catalog_is_deterministic_and_ignores_unlisted_markdown(tmp_path: Path) -> None:
    """Explicit membership excludes README files and location changes don't change identity."""
    from shutil import copytree

    from policynim.services.catalog import build_policy_catalog

    root = tmp_path / "first"
    write_pack(root)
    (root / "README.md").write_text("# Author notes\nDon't ingest this.\n")
    (root / "TEMPLATE.md").write_text("# Template\n")
    other = tmp_path / "second"
    copytree(root, other)
    first = build_policy_catalog(root)
    assert first.digest == build_policy_catalog(root).digest == build_policy_catalog(other).digest
    assert first.authored_count == first.effective_count == 1
    original = first.show("LOG-001").policy.provenance
    assert original is not None
    assert original.sources[0].revision == "revision-1"
    assert original.sources[0].license == "CC-BY-4.0"
    path = root / "policy.md"
    path.write_text(path.read_text().replace("request identifiers", "correlation identifiers"))
    revised = build_policy_catalog(root)
    assert revised.digest != first.digest
    assert revised.show("LOG-001").policy.provenance != original
    assert revised.show("LOG-001").policy.policy_id == "LOG-001"


def test_provenance_round_trips_through_real_sqlite(tmp_path: Path) -> None:
    """Both dump and retrieval preserve declared source details and actual content revision."""
    from policynim.ingest.chunking import chunk_policy_documents
    from policynim.ingest.loader import load_policy_documents
    from policynim.settings import Settings
    from policynim.storage import create_index_store
    from policynim.types import EmbeddedChunk

    root = tmp_path / "pack"
    write_pack(root)
    chunks = chunk_policy_documents(load_policy_documents(root))
    settings = Settings(index_db_path=tmp_path / "index.sqlite3")
    store = create_index_store(settings, legacy=True)
    store.replace([EmbeddedChunk(**chunk.model_dump(), vector=[1.0, 0.0]) for chunk in chunks])
    dumped = store.list_chunks()
    found = store.search([1.0, 0.0], top_k=1)
    assert dumped[0].policy.provenance == chunks[0].policy.provenance
    assert found[0].policy.provenance == chunks[0].policy.provenance
    assert store.inspect_readiness().state == "ready"


def test_plain_directory_keeps_unknown_provenance(tmp_path: Path) -> None:
    """Legacy inputs stay usable without inventing pack versions or upstream authority."""
    from policynim.services.catalog import build_policy_catalog

    manifest = write_pack(tmp_path / "pack")
    manifest.unlink()
    entry = build_policy_catalog(manifest.parent).show("LOG-001")
    provenance = entry.policy.provenance
    assert provenance is not None
    assert provenance.pack_id is provenance.pack_version is None
    assert provenance.grounding_kind == "legacy_unknown"
    assert provenance.sources == []
    assert provenance.content_digest.startswith("sha256:")


def test_invalid_manifest_fails_before_real_ingest_provider_work(tmp_path: Path) -> None:
    """Manifest rejection traverses the actual ingestion service without paid embedding."""
    from test_index_identity import SpyEmbedder

    from policynim.errors import InvalidPolicyDocumentError
    from policynim.services.ingest import IngestService
    from policynim.settings import Settings
    from policynim.storage import create_index_store

    root = tmp_path / "pack"
    manifest = write_pack(root)
    manifest.write_text(
        manifest.read_text().replace('path = "policy.md"', 'path = "../outside.md"')
    )
    embedder = SpyEmbedder()
    output = tmp_path / "index.sqlite3"
    rules = tmp_path / "rules.json"
    service = IngestService(
        embedder=embedder,
        index_store=create_index_store(Settings(index_db_path=output), legacy=True),
        corpus_root=root,
        embedding_model="fixture/model",
        runtime_rules_artifact_path=rules,
    )
    with pytest.raises(InvalidPolicyDocumentError, match="policies.0.path"):
        service.run()
    assert embedder.calls == 0
    assert not output.exists()
    assert not rules.exists()


@pytest.mark.parametrize(
    "old,new,location",
    [
        ("schema_version = 1", "schema_version = 2", "schema_version"),
        ("schema_version = 1", "schema_version = true", "schema_version"),
        ('version = "1.0.0"', 'version = "latest"', "version"),
        ('version = "1.0.0"', 'version = "1.0.0"\nenabled = false', "enabled"),
        ('path = "policy.md"', 'path = "policy.md"\nenabled = false', "policies.0.enabled"),
        ('pack_id = "team.logging"', 'pack_id = "team.logging"\npack_id = "other"', "TOML"),
        ('path = "policy.md"', 'path = "missing.md"', "policies.0.path"),
        ('path = "policy.md"', 'path = "../outside.md"', "policies.0.path"),
        ('path = "policy.md"', 'path = "/tmp/outside.md"', "policies.0.path"),
        (
            'url = "https://example.com/logging"',
            'url = "file:///private/policy"',
            "policies.0.sources.0.url",
        ),
        (
            'url = "https://example.com/logging"',
            'url = "https://user:secret-sentinel@example.com"',
            "policies.0.sources.0.url",
        ),
        (
            "retrieved_on = 2026-09-01",
            'retrieved_on = "yesterday"',
            "policies.0.sources.0.retrieved_on",
        ),
        ('title = "Logging reference"', 'title = ""', "policies.0.sources.0.title"),
        ('title = "Logging reference"', 'title = "   "', "policies.0.sources.0.title"),
        ('path = "policy.md"', 'path = "policy\\u0000.md"', "policies.0.path"),
        ('policy_id = "LOG-001"', 'policy_id = "OTHER"', "doesn't match"),
    ],
)
def test_cli_validation_rejects_bad_manifest_with_locations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, old: str, new: str, location: str
) -> None:
    """Malformed inputs return nonzero structured failures without echoing secret values."""
    root = tmp_path / "pack"
    manifest = write_pack(root)
    manifest.write_text(manifest.read_text().replace(old, new))
    monkeypatch.setenv("POLICYNIM_CORPUS_DIR", str(root))
    result = CliRunner().invoke(app, ["policies", "validate", "--format", "json"])
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["status"] == "error"
    assert location in report["message"]
    assert "secret-sentinel" not in result.output
    assert not (root / "data").exists()


@pytest.mark.parametrize(
    "fault", ["duplicate_id", "duplicate_path", "symlink", "symlink_loop", "frontmatter"]
)
def test_manifest_rejects_ambiguous_or_escaping_membership(tmp_path: Path, fault: str) -> None:
    """One deliberate fault in a valid pack must block catalog construction."""
    from policynim.errors import InvalidPolicyDocumentError
    from policynim.services.catalog import build_policy_catalog

    root = tmp_path / "pack"
    manifest = write_pack(root)
    assert build_policy_catalog(root).effective_count == 1
    if fault == "duplicate_id":
        manifest.write_text(
            manifest.read_text() + '\n[[policies]]\npolicy_id = "LOG-001"\npath = "other.md"\n'
        )
    elif fault == "duplicate_path":
        manifest.write_text(
            manifest.read_text() + '\n[[policies]]\npolicy_id = "OTHER"\npath = "policy.md"\n'
        )
    elif fault == "symlink":
        outside = tmp_path / "outside.md"
        outside.write_text((root / "policy.md").read_text())
        (root / "policy.md").unlink()
        (root / "policy.md").symlink_to(outside)
    else:
        path = root / "policy.md"
        path.write_text(
            path.read_text().replace("title: Logging", "title: Logging\nenabled: false")
        )
    with pytest.raises(InvalidPolicyDocumentError):
        build_policy_catalog(root)


def test_json_catalog_and_show_match_declared_models(monkeypatch, tmp_path: Path) -> None:
    """Machine consumers can validate both command responses using their public schemas."""
    from jsonschema import validate

    from policynim.services.catalog import CatalogPolicyView, PolicyCatalog

    root = tmp_path / "pack"
    write_pack(root)
    monkeypatch.setenv("POLICYNIM_CORPUS_DIR", str(root))
    listed = CliRunner().invoke(app, ["policies", "list", "--format", "json"])
    shown = CliRunner().invoke(app, ["policies", "show", "LOG-001", "--format", "json"])
    assert listed.exit_code == shown.exit_code == 0
    validate(json.loads(listed.stdout), PolicyCatalog.model_json_schema())
    validate(json.loads(shown.stdout), CatalogPolicyView.model_json_schema())
    missing = CliRunner().invoke(app, ["policies", "show", "ABSENT", "--format", "json"])
    assert missing.exit_code == 1
    assert "policies list" in json.loads(missing.stdout)["message"]


@pytest.mark.parametrize("mutation", ["legacy", "corrupt"])
def test_index_provenance_compatibility_and_failure_gate(tmp_path: Path, mutation: str) -> None:
    """Legacy metadata stays unknown; corrupt metadata blocks embedding through real search."""
    import sqlite3

    from test_index_identity import SpyEmbedder

    from policynim.errors import IndexCompatibilityError
    from policynim.ingest.chunking import chunk_policy_documents
    from policynim.ingest.loader import load_policy_documents
    from policynim.services.search import SearchService
    from policynim.settings import Settings
    from policynim.storage import create_index_store
    from policynim.types import EmbeddedChunk, SearchRequest

    root = tmp_path / "pack"
    write_pack(root)
    chunks = chunk_policy_documents(load_policy_documents(root))
    path = tmp_path / "index.sqlite3"
    store = create_index_store(Settings(index_db_path=path), legacy=True)
    store.replace([EmbeddedChunk(**chunk.model_dump(), vector=[1.0, 0.0]) for chunk in chunks])
    assert store.inspect_readiness().state == "ready"
    with sqlite3.connect(path) as conn:
        if mutation == "legacy":
            conn.execute("DELETE FROM index_metadata WHERE key LIKE 'provenance:%'")
        else:
            conn.execute(
                "UPDATE index_metadata SET value='invalid-json' WHERE key LIKE 'provenance:%'"
            )
    before = path.read_bytes()
    embedder = SpyEmbedder()
    service = SearchService(embedder=embedder, index_store=store)
    if mutation == "legacy":
        result = service.search(SearchRequest(query="request identifiers", top_k=1))
        assert result.hits[0].policy.provenance is None
        assert store.list_chunks()[0].policy.provenance is None
        assert embedder.calls == 1
    else:
        with pytest.raises(IndexCompatibilityError):
            service.search(SearchRequest(query="request identifiers", top_k=1))
        assert embedder.calls == 0
        assert store.inspect_readiness().state == "incompatible"
    assert path.read_bytes() == before


def test_ingestion_returns_the_catalog_compiled_before_embedding(tmp_path: Path) -> None:
    """Actual ingestion must expose the same source catalog identity as offline inspection."""
    from test_index_identity import SpyEmbedder

    from policynim.services.catalog import build_policy_catalog
    from policynim.services.ingest import IngestService
    from policynim.settings import Settings
    from policynim.storage import create_index_store

    root = tmp_path / "pack"
    write_pack(root)
    service = IngestService(
        embedder=SpyEmbedder(),
        index_store=create_index_store(
            Settings(index_db_path=tmp_path / "index.sqlite3"), legacy=True
        ),
        corpus_root=root,
        embedding_model="fixture/model",
        runtime_rules_artifact_path=tmp_path / "rules.json",
    )
    result = service.run()
    assert result.catalog_digest == build_policy_catalog(root).digest


def test_catalog_rejection_blocks_embedding_and_publication(monkeypatch, tmp_path: Path) -> None:
    """A catalog compilation failure must precede all provider and artifact work."""
    from test_index_identity import SpyEmbedder

    from policynim.errors import InvalidPolicyDocumentError
    from policynim.services import ingest as ingest_module
    from policynim.settings import Settings
    from policynim.storage import create_index_store

    root = tmp_path / "pack"
    write_pack(root)
    output, rules = tmp_path / "index.sqlite3", tmp_path / "rules.json"
    embedder = SpyEmbedder()

    def reject_catalog(documents, *, root):
        """Inject one rejection at the catalog compilation boundary."""
        raise InvalidPolicyDocumentError("Catalog compilation rejected the source snapshot.")

    monkeypatch.setattr(ingest_module, "compile_policy_catalog", reject_catalog)
    service = ingest_module.IngestService(
        embedder=embedder,
        index_store=create_index_store(Settings(index_db_path=output), legacy=True),
        corpus_root=root,
        embedding_model="fixture/model",
        runtime_rules_artifact_path=rules,
    )
    with pytest.raises(InvalidPolicyDocumentError, match="Catalog compilation rejected"):
        service.run()
    assert embedder.calls == 0
    assert not output.exists() and not rules.exists()


def test_new_unlisted_policy_is_visible_without_implicit_activation(
    monkeypatch, tmp_path: Path
) -> None:
    """Adding a file must produce an explicit unselected-path diagnostic, not activation."""
    from policynim.services.catalog import build_policy_catalog

    root = tmp_path / "pack"
    write_pack(root)
    baseline = build_policy_catalog(root)
    (root / "new-policy.md").write_text(
        "---\npolicy_id: NEW-001\n---\n# New policy\nRequire review.\n"
    )
    monkeypatch.setenv("POLICYNIM_CORPUS_DIR", str(root))
    result = CliRunner().invoke(app, ["policies", "validate", "--format", "json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["effective_count"] == 1
    assert report["unlisted_markdown"] == ["new-policy.md"]
    assert report["catalog_digest"] == baseline.digest
    text = CliRunner().invoke(app, ["policies", "list"])
    assert "aren't selected" in text.stdout
    manifest = root / "pack.toml"
    manifest.write_text(
        manifest.read_text() + '\n[[policies]]\npolicy_id = "NEW-001"\npath = "new-policy.md"\n'
    )
    selected = build_policy_catalog(root)
    assert selected.effective_count == 2
    assert selected.unlisted_markdown == []
    assert selected.digest != baseline.digest
