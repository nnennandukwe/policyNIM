"""Failure-first evidence for isolated ingestion candidates and owned cleanup."""

from pathlib import Path

import pytest
from test_index_identity import chunk, make_ingest, settings_for

from policynim.errors import MissingIndexError
from policynim.services import ingest as ingest_module
from policynim.services.health import create_runtime_health_service
from policynim.storage import create_index_store
from policynim.storage import sqlite_vec as storage_module
from policynim.types import EmbeddingIdentity


@pytest.mark.parametrize(
    "fault",
    [
        "inspection",
        "temporary_creation",
        "schema",
        "insert",
        "validation",
        "publication",
        "rules_stage",
        "rules_finalize",
        "completion",
        "interrupt_insert",
        "interrupt_finalize",
    ],
)
def test_failed_migration_preserves_original_installation(tmp_path, monkeypatch, fault):
    original_settings, original, _ = make_ingest(tmp_path, filename="original.sqlite3")
    original.run()
    preserved = [
        original_settings.index_db_path,
        tmp_path / "original.sqlite3.rules.json",
        tmp_path / "policies" / "logging.md",
    ]
    before = {path: path.read_bytes() for path in preserved}
    candidate_settings, candidate, embedder = make_ingest(
        tmp_path, filename="candidate.sqlite3", model="test/model-b"
    )

    def fail(*args, **kwargs):
        if fault.startswith("interrupt"):
            raise KeyboardInterrupt("injected interruption")
        raise OSError("injected failure")

    target, name = {
        "inspection": (storage_module.SQLiteVecIndexStore, "validate_replacement"),
        "temporary_creation": (storage_module, "_new_temp_database_path"),
        "schema": (storage_module, "_initialize_schema"),
        "insert": (storage_module, "_insert_chunks"),
        "validation": (storage_module, "_read_identity"),
        "publication": (storage_module.os, "link"),
        "rules_stage": (ingest_module, "_stage_runtime_rules_artifact"),
        "rules_finalize": (ingest_module, "_finalize_runtime_rules_artifact"),
        "completion": (storage_module.SQLiteVecIndexStore, "complete_ingest"),
        "interrupt_insert": (storage_module, "_insert_chunks"),
        "interrupt_finalize": (ingest_module, "_finalize_runtime_rules_artifact"),
    }[fault]
    with monkeypatch.context() as patch:
        patch.setattr(target, name, fail)
        with pytest.raises((OSError, KeyboardInterrupt)), candidate:
            candidate.run()
    assert embedder.closed
    assert {path: path.read_bytes() for path in preserved} == before
    assert create_runtime_health_service(original_settings).check().ready
    assert not create_runtime_health_service(candidate_settings).check().ready
    assert not list(tmp_path.rglob(".*.tmp"))


def test_complete_migration_retains_old_index_and_records_new_space(tmp_path):
    old_settings, old, _ = make_ingest(tmp_path, filename="old.sqlite3")
    old.run()
    old_bytes = old_settings.index_db_path.read_bytes()
    settings, candidate, _ = make_ingest(tmp_path, filename="new.sqlite3", model="test/model-b")
    result = candidate.run()
    assert result.document_count == 1
    assert result.chunk_count == create_index_store(settings).count() > 0
    assert create_index_store(settings).inspect_identity().embedding.model == "test/model-b"
    assert create_runtime_health_service(settings).check().ready
    assert old_settings.index_db_path.read_bytes() == old_bytes
    assert create_runtime_health_service(old_settings).check().ready


def test_new_index_publication_does_not_clobber_concurrent_creator(tmp_path, monkeypatch):
    path = tmp_path / "index.sqlite3"
    store = create_index_store(settings_for(path))
    real_link = storage_module.os.link

    def competing_link(source, destination):
        Path(destination).write_bytes(b"concurrent owner")
        real_link(source, destination)

    monkeypatch.setattr(storage_module.os, "link", competing_link)
    with pytest.raises(FileExistsError):
        store.replace([chunk()])
    assert path.read_bytes() == b"concurrent owner"
    assert not list(tmp_path.glob(".*.tmp"))


def test_destination_replaced_during_embedding_is_not_overwritten(tmp_path):
    settings, service, embedder = make_ingest(tmp_path)
    replacement = tmp_path / "other.sqlite3"
    create_index_store(settings_for(replacement, "test/other")).replace([chunk()])
    expected = replacement.read_bytes()
    original_embed = embedder.embed_documents

    def replace_destination(texts):
        replacement.replace(settings.index_db_path)
        return original_embed(texts)

    embedder.embed_documents = replace_destination
    with pytest.raises(MissingIndexError):
        service.run()
    assert settings.index_db_path.read_bytes() == expected


def test_destination_changes_during_database_write_are_preserved(tmp_path, monkeypatch):
    settings = settings_for(tmp_path / "index.sqlite3")
    store = create_index_store(settings)
    store.replace([chunk()])
    insert = storage_module._insert_chunks

    def replace_destination(conn, chunks):
        insert(conn, chunks)
        settings.index_db_path.unlink()
        settings.index_db_path.write_bytes(b"concurrent destination")

    monkeypatch.setattr(storage_module, "_insert_chunks", replace_destination)
    with pytest.raises(MissingIndexError, match="changed"):
        store.replace([chunk()])
    assert settings.index_db_path.read_bytes() == b"concurrent destination"


def test_concurrent_rules_destination_is_preserved_and_candidate_unready(tmp_path, monkeypatch):
    settings, service, _ = make_ingest(tmp_path)
    rules = tmp_path / "index.sqlite3.rules.json"
    finalize = ingest_module._finalize_runtime_rules_artifact

    def competing_rules(*args):
        rules.write_text("concurrent rules")
        finalize(*args)

    monkeypatch.setattr(ingest_module, "_finalize_runtime_rules_artifact", competing_rules)
    with pytest.raises(MissingIndexError, match="changed"):
        service.run()
    assert rules.read_text() == "concurrent rules"
    assert not create_runtime_health_service(settings).check().ready


def test_completion_receipt_cannot_mark_another_build_ready(tmp_path):
    store = create_index_store(settings_for(tmp_path / "index.sqlite3"))
    store.replace([chunk()], complete=False)
    with pytest.raises(MissingIndexError, match="changed"):
        store.complete_ingest("another-build")
    assert not store.inspect_identity().complete


@pytest.mark.parametrize("published", [False, True])
def test_cleanup_error_never_misreports_publication(tmp_path, monkeypatch, caplog, published):
    settings = settings_for(tmp_path / "index.sqlite3")
    store = create_index_store(settings)

    def cleanup_failure(*args):
        raise PermissionError("secret-cleanup-sentinel")

    def write_failure(*args):
        raise ValueError("original write failure")

    monkeypatch.setattr(storage_module, "_cleanup_database_files", cleanup_failure)
    if published:
        assert store.replace([chunk()])
        assert create_runtime_health_service(settings).check().ready
    else:
        monkeypatch.setattr(storage_module, "_insert_chunks", write_failure)
        with pytest.raises(ValueError, match="original write failure"):
            store.replace([chunk()])
        assert not settings.index_db_path.exists()
    assert "secret-cleanup-sentinel" not in caplog.text
    assert ("(published)" if published else "(not published)") in caplog.text


def test_output_cannot_replace_preserved_policy_source(tmp_path):
    settings, service, embedder = make_ingest(tmp_path)
    service.run()
    source = tmp_path / "policies" / "logging.md"
    before = source.read_bytes()
    invalid = ingest_module.IngestService(
        embedder=embedder,
        index_store=create_index_store(settings),
        corpus_root=source.parent,
        embedding_model=settings.nvidia_embed_model,
        runtime_rules_artifact_path=source,
    )
    calls = embedder.calls
    with pytest.raises(MissingIndexError, match="source"):
        invalid.run()
    assert source.read_bytes() == before
    assert embedder.calls == calls


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:secret@example.invalid/v1",
        "https://example.invalid/v1?key=secret",
        "https://example.invalid/v1#secret",
        "not-a-url",
    ],
)
def test_identity_rejects_credential_bearing_or_invalid_endpoints(endpoint):
    with pytest.raises(ValueError):
        EmbeddingIdentity(model="test/model", endpoint=endpoint)


def test_identity_normalizes_only_equivalent_endpoint_spelling():
    assert EmbeddingIdentity(model="m", endpoint="https://EXAMPLE.invalid:443/v1/") == (
        EmbeddingIdentity(model="m", endpoint="https://example.invalid/v1")
    )


@pytest.mark.parametrize("fault", ["begin", "commit", "checkpoint", "close"])
def test_database_lifecycle_failure_preserves_published_index(tmp_path, monkeypatch, fault):
    settings = settings_for(tmp_path / "index.sqlite3")
    store = create_index_store(settings)
    store.replace([chunk()])
    before = settings.index_db_path.read_bytes()
    connect = storage_module._connect
    closed = []

    class FailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql, *args):
            if (
                sql
                == {
                    "begin": "BEGIN IMMEDIATE",
                    "commit": "COMMIT",
                    "checkpoint": "PRAGMA wal_checkpoint(TRUNCATE)",
                    "close": "never",
                }[fault]
            ):
                raise OSError("injected database lifecycle failure")
            return self.connection.execute(sql, *args)

        def close(self):
            self.connection.close()
            closed.append(True)
            if fault == "close":
                raise OSError("injected close failure")

    def fault_connection(path, **kwargs):
        connection = connect(path, **kwargs)
        return FailingConnection(connection) if path.suffix == ".tmp" else connection

    monkeypatch.setattr(storage_module, "_connect", fault_connection)
    with pytest.raises(OSError, match="injected"):
        store.replace([chunk()])
    assert closed
    assert settings.index_db_path.read_bytes() == before
    assert store.search([1.0, 0.0], top_k=1)
    assert not list(tmp_path.glob(".*.tmp"))


def test_rules_staging_write_failure_cleans_owned_file(tmp_path, monkeypatch):
    settings, service, embedder = make_ingest(tmp_path)
    temporary_file = ingest_module.NamedTemporaryFile

    class FailingWriter:
        def __init__(self, *args, **kwargs):
            self.handle = temporary_file(*args, **kwargs)
            self.name = self.handle.name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def write(self, text):
            self.handle.write(text[:10])
            raise OSError("injected partial rules write")

    monkeypatch.setattr(ingest_module, "NamedTemporaryFile", FailingWriter)
    with pytest.raises(OSError, match="partial rules write"), service:
        service.run()
    assert embedder.closed
    assert not settings.index_db_path.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_rules_cleanup_failure_after_publication_keeps_complete_result(
    tmp_path, monkeypatch, caplog
):
    settings, service, _ = make_ingest(tmp_path)
    unlink = Path.unlink

    def cleanup_failure(path, *args, **kwargs):
        if ".rules.json." in path.name:
            raise OSError("secret-unlink-sentinel")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cleanup_failure)
    assert service.run().chunk_count > 0
    assert create_runtime_health_service(settings).check().ready
    assert "owned runtime-rules staging file" in caplog.text
    assert "secret-unlink-sentinel" not in caplog.text


def test_ingestion_does_not_replace_same_model_destination_created_during_embedding(tmp_path):
    settings, service, embedder = make_ingest(tmp_path)
    replacement = tmp_path / "other.sqlite3"
    create_index_store(settings_for(replacement)).replace([chunk()])
    expected = replacement.read_bytes()
    original_embed = embedder.embed_documents

    def concurrent_build(texts):
        replacement.replace(settings.index_db_path)
        return original_embed(texts)

    embedder.embed_documents = concurrent_build
    with pytest.raises(MissingIndexError, match="changed"):
        service.run()
    assert settings.index_db_path.read_bytes() == expected
