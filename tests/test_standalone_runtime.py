"""Exercise native build capability rejection and owned SQLite cleanup."""

from __future__ import annotations

import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_standalone_runtime.py"


@pytest.fixture
def runtime_guard():
    """Load the build-only guard without changing application imports."""
    spec = importlib.util.spec_from_file_location("standalone_runtime", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Connection:
    """Record owned connection cleanup at the native-extension boundary."""

    def __init__(self, *, disable_error: bool = False, close_error: bool = False) -> None:
        """Select cleanup faults without changing the primary native load."""
        self.events: list[object] = []
        self.disable_error = disable_error
        self.close_error = close_error

    def enable_load_extension(self, enabled: bool) -> None:
        """Record the extension state and expose a real cleanup failure shape."""
        self.events.append(("enable", enabled))
        if not enabled and self.disable_error:
            raise RuntimeError("private-cleanup-detail")

    def execute(self, statement: str):
        """Return a version only after the actual load boundary was reached."""
        self.events.append(statement)
        return SimpleNamespace(fetchone=lambda: ("v0.1.9",))

    def close(self) -> None:
        """Record closure even when disabling extensions failed."""
        self.events.append("close")
        if self.close_error:
            raise OSError("private-close-detail")


def install_sqlite_boundary(monkeypatch: pytest.MonkeyPatch, connection: object, load) -> None:
    """Supply the existing connection/load seam while retaining guard control flow."""
    monkeypatch.setitem(
        sys.modules,
        "sqlite3",
        SimpleNamespace(connect=lambda _: connection, sqlite_version="3.47.1"),
    )
    monkeypatch.setitem(sys.modules, "sqlite_vec", SimpleNamespace(load=load))


def test_missing_extension_api_closes_connection(runtime_guard, monkeypatch) -> None:
    """The Python.org failure must reject before freezing and still close its handle."""
    events: list[str] = []
    connection = SimpleNamespace(close=lambda: events.append("close"))
    install_sqlite_boundary(monkeypatch, connection, lambda _: pytest.fail("native load ran"))
    result = runtime_guard.check_runtime(platform.python_version())
    assert result["status"] == "error"
    assert result["checks"]["sqlite"]["errors"][0]["error_type"] == "AttributeError"
    assert events == ["close"]


def test_native_load_failure_disables_and_closes(runtime_guard, monkeypatch) -> None:
    """A missing or incompatible vec library must fail with bounded diagnostics."""
    connection = Connection()

    def fail_load(connection) -> None:
        """Represent the native loader failure without exposing its payload."""
        connection.events.append("load")
        raise OSError("private-native-path secret-marker")

    install_sqlite_boundary(monkeypatch, connection, fail_load)
    result = runtime_guard.check_runtime(platform.python_version())
    assert result["status"] == "error"
    assert connection.events == [("enable", True), "load", ("enable", False), "close"]
    assert "private-native-path" not in json.dumps(result)
    assert "secret-marker" not in json.dumps(result)


def test_disable_failure_cant_skip_close(runtime_guard, monkeypatch) -> None:
    """Failure to restore extension state must reject and still close the handle."""
    connection = Connection(disable_error=True)
    install_sqlite_boundary(monkeypatch, connection, lambda _: None)
    result = runtime_guard.check_runtime(platform.python_version())
    assert result["status"] == "error"
    assert connection.events[-2:] == [("enable", False), "close"]
    assert result["checks"]["sqlite"]["errors"][-1]["phase"] == "sqlite_disable"


def test_close_failure_isnt_reported_as_success(runtime_guard, monkeypatch) -> None:
    """A successful probe with incomplete owned cleanup must remain a failed gate."""
    connection = Connection(close_error=True)
    install_sqlite_boundary(monkeypatch, connection, lambda _: None)
    result = runtime_guard.check_runtime(platform.python_version())
    assert result["status"] == "error"
    assert result["checks"]["sqlite"]["errors"][-1]["phase"] == "sqlite_close"


def test_wrong_python_rejects_before_native_import(runtime_guard, monkeypatch) -> None:
    """The displayed setup version can't authorize a different selected interpreter."""
    monkeypatch.setattr(runtime_guard, "_check_sqlite", lambda: pytest.fail("SQLite ran"))
    result = runtime_guard.check_runtime("0.0.0")
    assert result["status"] == "error"
    assert result["checks"]["python"]["status"] == "error"
    assert "sqlite" not in result["checks"]


def test_crypto_binding_failure_is_bounded(runtime_guard, monkeypatch) -> None:
    """An unresolved native SSL symbol must block packaging without a raw traceback."""
    monkeypatch.setattr(runtime_guard, "_check_sqlite", lambda: {"status": "ok"})

    def failed_binding():
        """Represent an import-time native symbol mismatch."""
        raise ImportError("private native symbol path secret-marker")

    monkeypatch.setattr(runtime_guard, "_check_crypto", failed_binding)
    result = runtime_guard.check_runtime(platform.python_version())
    assert result["status"] == "error"
    assert result["checks"]["crypto"]["errors"][0]["error_type"] == "ImportError"
    assert "secret-marker" not in json.dumps(result)


def test_real_runtime_loads_vec_and_crypto() -> None:
    """The actual selected interpreter must execute native SQLite and crypto bindings."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--expected-python", platform.python_version()],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert report["checks"]["sqlite"]["vec_version"]
    assert report["checks"]["crypto"]["stdlib_openssl"]
    assert report["checks"]["crypto"]["cryptography_openssl"]


def test_cli_version_failure_is_structured() -> None:
    """A wrong build interpreter returns nonzero JSON before freezing."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--expected-python", "0.0.0"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "error"
    assert "Traceback" not in result.stderr


def test_primary_and_cleanup_failures_are_all_retained(runtime_guard, monkeypatch) -> None:
    """A cleanup error can't erase the native load failure that triggered it."""
    connection = Connection(disable_error=True, close_error=True)

    def failed_load(connection) -> None:
        """Fail at the real native-loader call boundary."""
        raise ImportError("private-loader-detail")

    install_sqlite_boundary(monkeypatch, connection, failed_load)
    report = runtime_guard.check_runtime(platform.python_version())
    assert report["status"] == "error"
    assert [error["phase"] for error in report["checks"]["sqlite"]["errors"]] == [
        "sqlite_extension",
        "sqlite_disable",
        "sqlite_close",
    ]
    assert connection.events[-2:] == [("enable", False), "close"]


def test_failed_connection_creation_is_structured(runtime_guard, monkeypatch) -> None:
    """An unusable SQLite runtime must reject even before a connection can be owned."""

    def failed_connect(database: str) -> None:
        """Reject opening memory storage without exposing an external message."""
        raise OSError("private-connect-detail")

    monkeypatch.setitem(sys.modules, "sqlite3", SimpleNamespace(connect=failed_connect))
    monkeypatch.setitem(sys.modules, "sqlite_vec", SimpleNamespace(load=lambda _: None))
    report = runtime_guard.check_runtime(platform.python_version())
    assert report["status"] == "error"
    assert report["checks"]["sqlite"]["errors"][0]["error_type"] == "OSError"
    assert "private-connect-detail" not in json.dumps(report)


def test_empty_vector_identity_rejects_after_cleanup(runtime_guard, monkeypatch) -> None:
    """Loading a library alone can't pass without the actual vector version query."""
    connection = Connection()
    monkeypatch.setattr(connection, "execute", lambda _: SimpleNamespace(fetchone=lambda: ("",)))
    install_sqlite_boundary(monkeypatch, connection, lambda _: None)
    report = runtime_guard.check_runtime(platform.python_version())
    assert report["status"] == "error"
    assert connection.events[-2:] == [("enable", False), "close"]
