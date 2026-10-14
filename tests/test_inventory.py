"""CLI and persistence regressions for offline policy inventory and recovery."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlite_vec
from typer.testing import CliRunner

from policynim.interfaces.cli import app
from policynim.settings import Settings, get_settings
from policynim.storage import create_index_store
from policynim.types import EmbeddedChunk, PolicyMetadata


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Run inspection without user configuration or provider credentials."""
    for key in list(os.environ):
        if key.startswith("POLICYNIM_") or key == "NVIDIA_API_KEY":
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    from test_cli import configure_standalone_cli_environment

    configure_standalone_cli_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("POLICYNIM_INDEX_DB_PATH", str(tmp_path / "index.sqlite3"))
    monkeypatch.setattr("policynim.providers.nvidia.OpenAI", forbid_provider)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_inventory_separates_sources_from_unknown_index(tmp_path: Path) -> None:
    """Inspect the bundled corpus without setup, keys, or creating an index."""
    result = CliRunner().invoke(app, ["inventory", "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["scope"] == "local"
    assert report["source"]["document_count"] == 9
    assert report["source"]["policy_count"] == 9
    assert report["source"]["chunk_count"] == 45
    assert report["index"]["state"] == "missing"
    assert report["index"]["policy_count"] is None
    assert report["index"]["chunk_count"] is None
    assert not (tmp_path / "data").exists()


def forbid_provider(*args: object, **kwargs: object) -> None:
    """Fail if any inventory or recovery path constructs a model client."""
    raise AssertionError("Inspection mustn't create a provider client")


def write_index(path: Path) -> None:
    """Build two policies with three actual chunks for independent count assertions."""
    chunks = [
        EmbeddedChunk(
            chunk_id=f"{policy_id}:{section}",
            path=f"policies/{policy_id}.md",
            section=section,
            lines="1-3",
            text="Include request identifiers.",
            vector=[1.0, 0.0],
            policy=PolicyMetadata(
                policy_id=policy_id, title=policy_id, doc_type="guidance", domain="backend"
            ),
        )
        for policy_id, section in [("LOG", "Rules"), ("LOG", "Examples"), ("AUTH", "Rules")]
    ]
    create_index_store(Settings(index_db_path=path)).replace(chunks)


@pytest.mark.parametrize(
    "mutation,state",
    [
        (None, "ready"),
        ("model", "incompatible"),
        ("schema", "incompatible"),
        ("incomplete", "incomplete"),
        ("empty", "empty"),
    ],
)
def test_inventory_reports_physical_counts_and_compatibility(
    tmp_path: Path, mutation: str | None, state: str
) -> None:
    """Inspect actual SQLite contents without merging source and indexed inventories."""
    path = tmp_path / "index.sqlite3"
    write_index(path)
    if mutation:
        with sqlite3.connect(path) as conn:
            if mutation == "empty":
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
                conn.execute("DELETE FROM policy_chunks")
                conn.execute("DELETE FROM policy_vectors")
            else:
                key, value = {
                    "model": ("embedding_model", "other/model"),
                    "schema": ("schema_version", "1"),
                    "incomplete": ("ingest_complete", "false"),
                }[mutation]
                conn.execute("UPDATE index_metadata SET value=? WHERE key=?", (value, key))
    before = path.read_bytes()
    result = CliRunner().invoke(app, ["inventory", "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["source"]["document_count"] == 9
    assert report["index"]["state"] == state
    assert report["index"]["policy_count"] == (0 if mutation == "empty" else 2)
    assert report["index"]["chunk_count"] == (0 if mutation == "empty" else 3)
    assert report["index"]["compatible"] is (state == "ready")
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["directory", "invalid", "unreadable"])
def test_inventory_preserves_unusable_indexes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """Keep unknown counts null and leave existing data unchanged on failure."""
    path = tmp_path / "index.sqlite3"
    if kind == "directory":
        path.mkdir()
        preserved = path / "legacy-only-policy.lance"
    else:
        preserved = path
    preserved.write_bytes(b"private legacy contents")
    if kind == "unreadable":
        from policynim.storage import sqlite_vec

        original_connect = sqlite_vec._connect

        def deny_index_read(target: Path, **kwargs):
            """Inject an OS access denial only at the database connection boundary."""
            if target == path:
                raise PermissionError(13, "Bearer secret-sentinel", str(path))
            return original_connect(target, **kwargs)

        monkeypatch.setattr(sqlite_vec, "_connect", deny_index_read)
    before = preserved.read_bytes()
    result = CliRunner().invoke(app, ["inventory", "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["index"]["state"] == kind
    assert report["index"]["policy_count"] is None
    assert report["index"]["chunk_count"] is None
    assert "secret-sentinel" not in result.output
    assert preserved.read_bytes() == before


def test_invalid_sources_are_unknown_not_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Don't turn missing or corrupt policy inputs into an observed zero count."""
    monkeypatch.setenv("POLICYNIM_CORPUS_DIR", str(tmp_path / "missing-policies"))
    result = CliRunner().invoke(app, ["inventory", "--format", "json"])
    assert result.exit_code == 0, result.output
    source = json.loads(result.stdout)["source"]
    assert source["status"] == "unavailable"
    assert source["document_count"] is None
    assert source["policy_count"] is None
    assert "POLICYNIM_CORPUS_DIR" in source["reason"]
    assert "policynim policies validate" in source["reason"]


def test_legacy_alias_file_and_canonical_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A valid SQLite legacy alias works; a canonical path wins over an old directory."""
    path = tmp_path / "index.sqlite3"
    write_index(path)
    monkeypatch.delenv("POLICYNIM_INDEX_DB_PATH")
    monkeypatch.setenv("POLICYNIM_LANCEDB_URI", str(path))
    result = CliRunner().invoke(app, ["inventory", "--format", "json"])
    assert json.loads(result.stdout)["index"]["state"] == "ready"
    monkeypatch.setenv("POLICYNIM_LANCEDB_URI", str(tmp_path))
    monkeypatch.setenv("POLICYNIM_INDEX_DB_PATH", str(path))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["inventory", "--format", "json"])
    assert json.loads(result.stdout)["index"]["state"] == "ready"
    direct = create_index_store(Settings(index_db_path=path)).inspect_readiness()
    assert direct.state == "ready"


def test_inventory_text_labels_unknown_counts() -> None:
    """Human-readable inspection keeps absent counts visibly unknown."""
    result = CliRunner().invoke(app, ["inventory"])
    assert result.exit_code == 0
    assert "Source documents: 9" in result.stdout
    assert "Indexed policy IDs: unknown" in result.stdout
    assert "remote deployment isn't inspected" in result.stdout


@pytest.mark.parametrize("state", ["ready", "invalid", "directory", "incomplete", "incompatible"])
def test_real_search_guard_rejects_unusable_index_before_embedding(
    tmp_path: Path, state: str
) -> None:
    """Use a valid control and single faults to verify provider admission through search."""
    from test_index_identity import SpyEmbedder

    from policynim.errors import MissingIndexError
    from policynim.services.search import SearchService
    from policynim.types import SearchRequest

    path = tmp_path / "index.sqlite3"
    if state == "directory":
        path.mkdir()
    elif state == "invalid":
        path.write_bytes(b"not a database")
    else:
        write_index(path)
        if state != "ready":
            key, value = (
                ("ingest_complete", "false")
                if state == "incomplete"
                else ("embedding_model", "other/model")
            )
            with sqlite3.connect(path) as conn:
                conn.execute("UPDATE index_metadata SET value=? WHERE key=?", (value, key))
    embedder = SpyEmbedder()
    service = SearchService(embedder=embedder, index_store=create_index_store(Settings()))
    if state == "ready":
        result = service.search(SearchRequest(query="request identifiers", top_k=2))
        assert not result.insufficient_context
        assert embedder.calls == 1
    else:
        with pytest.raises(MissingIndexError, match="Preserve existing"):
            service.search(SearchRequest(query="request identifiers", top_k=2))
        assert embedder.calls == 0


def test_doctor_and_inventory_share_same_observed_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep diagnosis and standalone inspection on the same typed readiness contract."""
    config = tmp_path / "local.env"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("POLICYNIM_CONFIG_FILE", str(config))
    path = tmp_path / "index.sqlite3"
    path.write_bytes(b"not a database")
    inventory = CliRunner().invoke(app, ["inventory", "--format", "json"])
    doctor = CliRunner().invoke(app, ["doctor", "--format", "json"])
    assert inventory.exit_code == doctor.exit_code == 0
    assert json.loads(inventory.stdout)["index"] == json.loads(doctor.stdout)["index"]
    assert json.loads(doctor.stdout)["index"]["state"] == "invalid"


def test_empty_index_requires_valid_shape_before_empty_diagnosis(tmp_path: Path) -> None:
    """A valid empty control and one damaged identity must yield different recovery states."""
    path = tmp_path / "index.sqlite3"
    write_index(path)
    with sqlite3.connect(path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("DELETE FROM policy_chunks")
        conn.execute("DELETE FROM policy_vectors")
    store = create_index_store(Settings())
    assert store.inspect_readiness().state == "empty"
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE index_metadata SET value='999' WHERE key='embedding_dimension'")
    before = path.read_bytes()
    assert store.inspect_readiness().state == "invalid"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "code",
    [
        sqlite3.SQLITE_IOERR_READ,
        sqlite3.SQLITE_BUSY_SNAPSHOT,
        sqlite3.SQLITE_LOCKED_SHAREDCACHE,
        sqlite3.SQLITE_CANTOPEN_ISDIR,
    ],
)
@pytest.mark.parametrize("failure_point", ["connection", "metadata"])
def test_extended_sqlite_access_errors_never_recommend_rebuild(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int, failure_point: str
) -> None:
    """Keep actual extended access/contention codes in the unreadable recovery path."""
    from policynim.storage import sqlite_vec as adapter

    write_index(tmp_path / "index.sqlite3")
    error = sqlite3.OperationalError("private access detail")
    error.sqlite_errorcode = code

    def fail_connection(*args, **kwargs):
        """Inject one SQLite extended error at the real connection boundary."""
        raise error

    if failure_point == "connection":
        monkeypatch.setattr(adapter, "_connect", fail_connection)
    else:
        original_metadata = adapter._metadata_value

        def fail_identity_metadata(conn, key):
            """Fail inside identity parsing after schema inspection succeeds."""
            if key == "embedding_provider":
                raise error
            return original_metadata(conn, key)

        monkeypatch.setattr(adapter, "_metadata_value", fail_identity_metadata)
    result = CliRunner().invoke(app, ["inventory", "--format", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["index"]["state"] == "unreadable"
    assert "private access detail" not in result.stdout
