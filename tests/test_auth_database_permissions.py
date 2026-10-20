"""Real auth SQLite creation and retained-data permission regressions."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from policynim.storage import AuthStore
from policynim.storage.operation_store import OperationStore


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are the privacy contract.")
@pytest.mark.parametrize("kind", ["auth", "operation"])
def test_public_auth_stores_create_private_database_before_sqlite_open(
    tmp_path: Path, kind: str
) -> None:
    """Both public writers must create private bytes even with a permissive caller mask."""
    script = """
import json, os, sqlite3, stat, sys
from pathlib import Path
from policynim.storage import AuthStore
from policynim.storage.operation_store import OperationStore
os.umask(0)
path = Path(sys.argv[1])
original_connect = sqlite3.connect
observed_before_open = []
def observed_connect(*args, **kwargs):
    "Observe the real SQLite boundary before forwarding every actual open."
    observed_before_open.append(stat.S_IMODE(path.stat().st_mode))
    return original_connect(*args, **kwargs)
sqlite3.connect = observed_connect
store = AuthStore(path=path) if sys.argv[2] == 'auth' else OperationStore(path)
print(json.dumps({
    'mode': stat.S_IMODE(path.stat().st_mode), 'before': observed_before_open,
    'parent': stat.S_IMODE(path.parent.stat().st_mode)}))
store.close()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "nested/auth.sqlite3"), kind],
        capture_output=True,
        text=True,
        check=True,
    )
    observed = json.loads(completed.stdout)
    assert observed["mode"] == 0o600
    assert observed["before"] and set(observed["before"]) == {0o600}
    assert observed["parent"] == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are the privacy contract.")
def test_existing_auth_database_and_sqlite_sidecars_become_private_without_data_loss(
    tmp_path: Path,
) -> None:
    """An upgrade retains records and inode identity while fixing old public mode bits."""
    path = tmp_path / "auth.sqlite3"
    store = AuthStore(path=path)
    with sqlite3.connect(path) as keeper:
        keeper.execute("CREATE TABLE retained_permission_proof (value TEXT)")
        keeper.execute("INSERT INTO retained_permission_proof VALUES ('retained')")
        keeper.commit()
        files = [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]
        assert all(file.exists() for file in files)
        for file in files:
            file.chmod(0o644)
        identity = path.stat().st_ino
        AuthStore(path=path).list_accounts()
        assert path.stat().st_ino == identity
        assert keeper.execute("SELECT value FROM retained_permission_proof").fetchone() == (
            "retained",
        )
        assert [stat.S_IMODE(file.stat().st_mode) for file in files] == [0o600] * 3
    store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are the privacy contract.")
def test_sqlite_creates_private_wal_shm_and_rollback_journal(tmp_path: Path) -> None:
    """SQLite's actual VFS must inherit the private main-file mode for every sidecar."""
    script = """
import json, os, sqlite3, stat, sys
from pathlib import Path
from policynim.storage import AuthStore
os.umask(0)
path = Path(sys.argv[1])
store = AuthStore(path=path)
modes = {}
with sqlite3.connect(path) as connection:
    connection.execute('CREATE TABLE permission_probe (value INTEGER)')
    for suffix in ('-wal', '-shm'):
        modes[suffix] = stat.S_IMODE(path.with_name(path.name + suffix).stat().st_mode)
connection.close()
with sqlite3.connect(path) as connection:
    connection.execute('PRAGMA journal_mode=DELETE')
    connection.execute('BEGIN IMMEDIATE')
    connection.execute('INSERT INTO permission_probe VALUES (1)')
    modes['-journal'] = stat.S_IMODE(path.with_name(path.name + '-journal').stat().st_mode)
    connection.rollback()
print(json.dumps(modes))
store.close()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "auth.sqlite3")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout) == {"-wal": 0o600, "-shm": 0o600, "-journal": 0o600}


@pytest.mark.parametrize("kind", ["auth", "operation"])
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_auth_writers_reject_linked_database_or_sidecars_without_touching_target(
    tmp_path: Path, kind: str, suffix: str, link: str
) -> None:
    """Unsafe configured aliases must never change another inode's bytes or permissions."""
    path = tmp_path / "auth.sqlite3"
    target = tmp_path / "private-sentinel"
    target.write_bytes(b"private-sentinel-content")
    target.chmod(0o644)
    alias = path.with_name(path.name + suffix)
    try:
        alias.symlink_to(target) if link == "symbolic" else os.link(target, alias)
    except OSError:
        pytest.skip("This filesystem user lacks link creation permission.")
    with pytest.raises(OSError, match="auth_storage_unsafe") as caught:
        AuthStore(path=path) if kind == "auth" else OperationStore(path)
    assert str(tmp_path) not in str(caught.value)
    assert "private-sentinel" not in str(caught.value)
    assert target.read_bytes() == b"private-sentinel-content"
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.parametrize("kind", ["auth", "operation"])
def test_configured_relative_final_symlink_is_rejected_after_parent_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """The service factory must retain the configured final name while resolving its parent."""
    from policynim.errors import PolicyNIMError
    from policynim.services.beta_auth import create_beta_auth_service
    from policynim.services.operation_admission import OperationAdmissionService
    from policynim.settings import Settings

    directory = tmp_path / "state"
    directory.mkdir()
    target = directory / "retained.sqlite3"
    AuthStore(path=target).close()
    before = target.read_bytes()
    alias = directory / "auth.sqlite3"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("This filesystem user lacks link creation permission.")
    monkeypatch.chdir(tmp_path)
    settings = Settings.model_validate({"beta_auth_db_path": Path("state/auth.sqlite3")})
    with pytest.raises(PolicyNIMError) as caught:
        create_beta_auth_service(settings) if kind == "auth" else OperationAdmissionService(
            settings
        )
    assert str(tmp_path) not in str(caught.value)
    assert target.read_bytes() == before


@pytest.mark.parametrize("kind", ["auth", "operation"])
def test_configured_relative_database_keeps_cwd_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Installed-style factories keep relative auth paths below their invocation directory."""
    from policynim.services.beta_auth import create_beta_auth_service
    from policynim.services.operation_admission import OperationAdmissionService
    from policynim.settings import Settings

    monkeypatch.chdir(tmp_path)
    settings = Settings.model_validate({"beta_auth_db_path": Path("nested/auth.sqlite3")})
    service = (
        create_beta_auth_service(settings)
        if kind == "auth"
        else OperationAdmissionService(settings)
    )
    try:
        assert (tmp_path / "nested/auth.sqlite3").is_file()
        if os.name == "posix":
            assert stat.S_IMODE((tmp_path / "nested/auth.sqlite3").stat().st_mode) == 0o600
    finally:
        if isinstance(service, OperationAdmissionService):
            service.store.close()
        else:
            service.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode changes are the privacy boundary.")
def test_permission_failure_keeps_recovery_safe_without_extra_inode_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Permission failures must avoid extra inode handles and injected private diagnostics."""
    from policynim.storage import auth_database

    descriptors = []
    original_open, original_close = os.open, os.close

    def observe_open(*args, **kwargs):
        """Track actual descriptors owned by the preparation call."""
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def observe_close(descriptor):
        """Record release before closing the actual descriptor."""
        descriptors.remove(descriptor)
        original_close(descriptor)

    def refuse_chmod(*args):
        """Simulate a real permission error without publishing its private message."""
        raise PermissionError("private-permission-sentinel")

    monkeypatch.setattr(auth_database.os, "open", observe_open)
    monkeypatch.setattr(auth_database.os, "close", observe_close)
    path = tmp_path / "auth.sqlite3"
    path.touch(mode=0o644)
    path.chmod(0o644)
    monkeypatch.setattr(auth_database.os, "chmod", refuse_chmod)
    with pytest.raises(OSError, match="auth_storage_unavailable") as caught:
        AuthStore(path=tmp_path / "auth.sqlite3")
    assert descriptors == []
    assert "private-permission-sentinel" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are the privacy contract.")
def test_readonly_report_requires_private_storage_without_chmod_or_creation(tmp_path: Path) -> None:
    """Telemetry doesn't silently repair files or create a missing auth database."""
    path = tmp_path / "auth.sqlite3"
    with pytest.raises(OSError):
        OperationStore.report(path)
    assert not path.exists()
    store = OperationStore(path)
    store.close()
    path.chmod(0o644)
    before = path.read_bytes()
    with pytest.raises(OSError, match="auth_storage_unsafe"):
        OperationStore.report(path)
    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    path.chmod(0o600)
    assert OperationStore.report(path).retained_rows == 0


@pytest.mark.parametrize("kind", ["auth", "operation"])
def test_auth_writers_reject_nonregular_database_without_blocking(
    tmp_path: Path, kind: str
) -> None:
    """A configured directory must fail before SQLite or descriptor writes."""
    path = tmp_path / "auth.sqlite3"
    path.mkdir()
    with pytest.raises(OSError, match="auth_storage_unsafe"):
        AuthStore(path=path) if kind == "auth" else OperationStore(path)
    assert list(path.iterdir()) == []


def test_concurrent_auth_store_reentry_retains_all_records(tmp_path: Path) -> None:
    """Independent writers keep private files while retaining every accepted account."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime

    path = tmp_path / "auth.sqlite3"
    AuthStore(path=path).close()

    def record_account(index: int) -> None:
        """Exercise the public store and its real per-operation connections."""
        store = AuthStore(path=path)
        try:
            store.upsert_account_from_github(
                github_user_id=index,
                github_login=f"user-{index}",
                email=None,
                now=datetime(2026, 10, 20, tzinfo=UTC),
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record_account, range(1, 13)))
    store = AuthStore(path=path)
    try:
        assert len(store.list_accounts()) == 12
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        store.close()


@pytest.mark.skipif(os.name != "posix", reason="Open-inode unlink behavior is POSIX-specific.")
def test_last_sqlite_reader_sidecar_cleanup_doesnt_reject_another_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real final reader can remove its WAL while the next writer prepares old modes."""
    from policynim.storage import auth_database

    path = tmp_path / "auth.sqlite3"
    AuthStore(path=path).close()
    keeper = sqlite3.connect(path)
    keeper.execute("SELECT * FROM accounts").fetchall()
    wal = path.with_name(path.name + "-wal")
    wal.chmod(0o644)
    original_chmod = os.chmod
    cleaned = False

    def close_last_reader_before_sidecar_chmod(*args, **kwargs):
        """Remove the actual SQLite sidecar between observation and its permission change."""
        nonlocal cleaned
        if Path(args[0]) == wal and not cleaned:
            keeper.close()
            cleaned = True
        return original_chmod(*args, **kwargs)

    monkeypatch.setattr(auth_database.os, "chmod", close_last_reader_before_sidecar_chmod)
    try:
        store = AuthStore(path=path)
        assert store.list_accounts() == []
        assert cleaned
        store.close()
    finally:
        keeper.close()


def test_beta_factory_keeps_the_safe_unsafe_file_error_code(tmp_path: Path) -> None:
    """The service must retain the storage adapter's safe unsafe-file recovery category."""
    from policynim.errors import ConfigurationError
    from policynim.services.beta_auth import create_beta_auth_service
    from policynim.settings import Settings

    path = tmp_path / "auth.sqlite3"
    path.mkdir()
    with pytest.raises(ConfigurationError, match="^auth_storage_unsafe:"):
        create_beta_auth_service(Settings.model_validate({"beta_auth_db_path": path}))


def test_operator_report_rejects_configured_final_symlink_without_changing_target(
    tmp_path: Path,
) -> None:
    """Read-only report configuration mustn't erase the alias check before storage sees it."""
    from policynim.errors import PolicyNIMError
    from policynim.services.operational_report import inspect_operational_report
    from policynim.settings import Settings

    target = tmp_path / "retained.sqlite3"
    AuthStore(path=target).close()
    alias = tmp_path / "auth.sqlite3"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("This filesystem user lacks link creation permission.")
    before = target.read_bytes()
    with pytest.raises(PolicyNIMError, match="Operation state couldn't be read"):
        inspect_operational_report(Settings.model_validate({"beta_auth_db_path": alias}))
    assert target.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="SQLite fcntl lock ownership is POSIX-specific.")
def test_private_preparation_doesnt_release_an_existing_sqlite_writer_lock(tmp_path: Path) -> None:
    """Opening a public reader mustn't let another process steal this process's write lock."""
    path = tmp_path / "auth.sqlite3"
    store = AuthStore(path=path)
    keeper = sqlite3.connect(path)
    keeper.execute("BEGIN IMMEDIATE")
    try:
        assert store.list_accounts() == []
        script = """
import sqlite3,sys
connection=sqlite3.connect(sys.argv[1],timeout=0.05)
try:
    connection.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError as exc:
    print('locked' if 'locked' in str(exc).lower() else 'other_error')
else:
    print('writable')
    connection.rollback()
finally:
    connection.close()
"""
        child = subprocess.run(
            [sys.executable, "-c", script, str(path)], capture_output=True, text=True, check=True
        )
        assert child.stdout.strip() == "locked"
    finally:
        keeper.rollback()
        keeper.close()
        store.close()
