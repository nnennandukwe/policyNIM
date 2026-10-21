"""Real auth SQLite creation and retained-data permission regressions."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
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
    with pytest.raises(PolicyNIMError, match="auth_storage_unsafe"):
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are the privacy contract.")
def test_operator_report_names_private_permission_recovery_without_changing_legacy_data(
    tmp_path: Path,
) -> None:
    """A read-only legacy mode rejection directs the operator to repair permissions."""
    from policynim.errors import PolicyNIMError
    from policynim.services.operational_report import inspect_operational_report
    from policynim.settings import Settings

    path = tmp_path / "private-report-sentinel.sqlite3"
    OperationStore(path).close()
    path.chmod(0o644)
    before = path.read_bytes()
    with pytest.raises(PolicyNIMError) as caught:
        inspect_operational_report(Settings.model_validate({"beta_auth_db_path": path}))
    message = str(caught.value)
    assert "auth_storage_unsafe" in message
    assert "permissions" in message
    assert path.name not in message
    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.parametrize("break_glass", [False, True])
def test_mcp_startup_keeps_safe_auth_alias_recovery_category(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, break_glass: bool
) -> None:
    """Strict startup and degraded startup keep owned categories without exposing paths."""
    from policynim.errors import ConfigurationError
    from policynim.interfaces import mcp as mcp_module
    from policynim.settings import Settings

    path = tmp_path / "private-startup-sentinel.sqlite3"
    path.mkdir()
    settings = Settings.model_validate(
        {
            "beta_auth_db_path": path,
            "mcp_require_auth": True,
            "mcp_public_base_url": "https://example.test",
            "mcp_bearer_tokens": ("private-bearer-sentinel",),
            "beta_signup_enabled": not break_glass,
            "beta_session_secret": "private-session-sentinel",
            "beta_github_client_id": "synthetic-client",
            "beta_github_client_secret": "private-oauth-sentinel",
        }
    )
    if break_glass:
        mcp_module._build_streamable_http_app(settings)
        message = caplog.text
    else:
        with pytest.raises(ConfigurationError) as caught:
            mcp_module._build_streamable_http_app(settings)
        message = str(caught.value)
    assert "auth_storage_unsafe" in message
    assert "aliases" in message
    assert path.name not in message
    assert "private-bearer-sentinel" not in message


@pytest.mark.skipif(os.name != "posix", reason="Unlinked inode observations are POSIX-specific.")
@pytest.mark.parametrize(
    "phase", ["readonly", "writer_inspection", "writer_prepare", "writer_verify"]
)
def test_retired_zero_link_sidecar_observation_doesnt_reject_live_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """A real last-reader unlink can retire a sidecar while lstat returns its old inode."""
    from policynim.storage import auth_database

    path = tmp_path / "auth.sqlite3"
    AuthStore(path=path).close()
    keeper = sqlite3.connect(path)
    keeper.execute("PRAGMA journal_mode=WAL")
    keeper.execute("SELECT * FROM accounts").fetchall()
    sidecar = path.with_name(path.name + "-wal")
    descriptor = os.open(sidecar, os.O_RDONLY)
    original = Path.lstat
    observations = 0
    injected = False
    selected = {"readonly": 1, "writer_inspection": 1, "writer_prepare": 2, "writer_verify": 3}[
        phase
    ]

    def observe_retired_inode(candidate, *args, **kwargs):
        """Return the actual unlinked stat at the kernel race's selected observation point."""
        nonlocal observations, injected
        if candidate == sidecar:
            observations += 1
            if observations == selected:
                keeper.close()
                retired = os.fstat(descriptor)
                assert retired.st_nlink == 0
                injected = True
                return retired
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(auth_database.Path, "lstat", observe_retired_inode)
    try:
        if phase == "readonly":
            with closing(
                auth_database.connect_auth_database(path, timeout=0.1, readonly=True)
            ) as reader:
                assert reader.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
        else:
            store = AuthStore(path=path)
            assert store.list_accounts() == []
            store.close()
        assert injected
    finally:
        keeper.close()
        os.close(descriptor)


def test_missing_auth_report_names_absence_without_preparation_advice(tmp_path: Path) -> None:
    """A read-only missing path gives initialization guidance without blaming disk space."""
    from policynim.errors import PolicyNIMError
    from policynim.services.operational_report import inspect_operational_report
    from policynim.settings import Settings

    path = tmp_path / "missing-private-sentinel" / "auth.sqlite3"
    with pytest.raises(PolicyNIMError) as caught:
        inspect_operational_report(Settings.model_validate({"beta_auth_db_path": path}))
    message = str(caught.value)
    assert message.startswith("auth_storage_missing:")
    assert "database wasn't found" in message
    assert "POLICYNIM_BETA_AUTH_DB_PATH" in message
    assert "available space" not in message and "couldn't be prepared" not in message
    assert "missing-private-sentinel" not in message
    assert not path.parent.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX sidecar modes are the privacy contract.")
def test_readonly_report_keeps_created_coordination_files_private(tmp_path: Path) -> None:
    """A real read may create WAL/SHM metadata while retaining the canonical database bytes."""
    script = """
import hashlib, json, os, stat, sys
from pathlib import Path
from policynim.storage import AuthStore
from policynim.storage.operation_store import OperationStore
os.umask(0)
path = Path(sys.argv[1])
AuthStore(path=path).close()
before = hashlib.sha256(path.read_bytes()).hexdigest()
assert not path.with_name(path.name + '-wal').exists()
assert not path.with_name(path.name + '-shm').exists()
report = OperationStore.report(path)
print(json.dumps({
    'unchanged': before == hashlib.sha256(path.read_bytes()).hexdigest(),
    'retained_rows': report.retained_rows,
    'modes': {suffix: stat.S_IMODE(path.with_name(path.name + suffix).stat().st_mode)
              for suffix in ('-wal', '-shm')}
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "auth.sqlite3")],
        capture_output=True,
        text=True,
        check=True,
    )
    observed = json.loads(completed.stdout)
    assert observed == {
        "unchanged": True,
        "retained_rows": 0,
        "modes": {"-wal": 0o600, "-shm": 0o600},
    }


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_operation_admission_preserves_unsafe_file_recovery(tmp_path: Path, suffix: str) -> None:
    """Actual admission initialization must name unsafe aliases without exposing their target."""
    from policynim.services.operation_admission import (
        OperationAdmissionError,
        OperationAdmissionService,
    )
    from policynim.settings import Settings

    path = tmp_path / "auth.sqlite3"
    target = tmp_path / "private-admission-sentinel"
    target.write_bytes(b"retained-private-records")
    alias = path.with_name(path.name + suffix)
    try:
        alias.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("This platform doesn't permit symbolic links.")
    with pytest.raises(OperationAdmissionError) as raised:
        OperationAdmissionService(Settings(beta_auth_db_path=path))
    message = str(raised.value)
    assert message.startswith("admission_unavailable:")
    assert "auth_storage_unsafe:" in message
    assert "ownership, permissions and aliases" in message
    assert "Ask the operator" in message and "retry as the service user" not in message
    assert "database locks" not in message
    assert "private-admission-sentinel" not in message and str(tmp_path) not in message
    assert target.read_bytes() == b"retained-private-records"


def test_admin_cli_sqlite_open_race_has_safe_recovery(tmp_path, monkeypatch) -> None:
    """A real failed SQLite open after preparation can't escape the public admin command."""
    from typer.testing import CliRunner

    from policynim.interfaces import cli
    from policynim.settings import Settings
    from policynim.storage import auth_database

    path, retained = tmp_path / "auth.sqlite3", tmp_path / "retained-auth.sqlite3"
    AuthStore(path=path).close()
    before = path.read_bytes()
    original = sqlite3.connect
    moved: list[bool] = []

    def move_before_actual_open(*args, **kwargs):
        """Move the owned database after validation and let the real rw open fail."""
        if not moved:
            path.rename(retained)
            moved.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(auth_database.sqlite3, "connect", move_before_actual_open)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(beta_auth_db_path=path))
    result = CliRunner().invoke(cli.app, ["beta-admin", "list-accounts"])
    assert moved and result.exit_code == 1
    assert "auth_storage_unavailable:" in result.output
    assert "Preserve the database" in result.output
    assert not isinstance(result.exception, sqlite3.Error)
    assert "Traceback" not in result.output and str(tmp_path) not in result.output
    assert retained.read_bytes() == before and not path.exists()


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED])
def test_admission_preserves_sqlite_open_contention(tmp_path, monkeypatch, code) -> None:
    """Busy DB-API open failures must keep their existing bounded retry classification."""
    from policynim.services.operation_admission import (
        OperationAdmissionError,
        OperationAdmissionService,
    )
    from policynim.settings import Settings
    from policynim.storage import auth_database

    error = sqlite3.OperationalError("private-open-contention-sentinel")
    error.sqlite_errorcode = code

    def busy_open(*args, **kwargs):
        """Preserve the DB-API failure and code while rejecting before SQL work."""
        raise error

    monkeypatch.setattr(auth_database.sqlite3, "connect", busy_open)
    with pytest.raises(OperationAdmissionError, match="admission_busy:") as raised:
        OperationAdmissionService(Settings(beta_auth_db_path=tmp_path / "auth.sqlite3"))
    assert "private-open-contention-sentinel" not in str(raised.value)
