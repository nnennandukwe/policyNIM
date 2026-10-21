"""Reject an unsuitable native build interpreter before PyInstaller runs."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence


def _failure(phase: str, error: Exception) -> dict[str, str]:
    """Retain the failing phase and bounded class without native paths or payloads."""
    return {
        "phase": phase,
        "error_type": type(error).__name__.encode("ascii", errors="replace").decode()[:64],
        "message": "Native capability check failed; inspect the selected build interpreter.",
    }


def _check_sqlite() -> dict[str, object]:
    """Load the real vector extension and always disable loading and close its connection."""
    import sqlite3

    import sqlite_vec

    connection = sqlite3.connect(":memory:")
    errors: list[dict[str, str]] = []
    version: str | None = None
    try:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        row = connection.execute("SELECT vec_version()").fetchone()
        if (
            row is None
            or not isinstance(row[0], str)
            or not row[0]
            or not row[0].isascii()
            or len(row[0]) > 128
        ):
            raise ValueError("The vector extension didn't return a bounded version.")
        version = row[0]
    except Exception as error:
        errors.append(_failure("sqlite_extension", error))
    finally:
        try:
            connection.enable_load_extension(False)
        except Exception as error:
            errors.append(_failure("sqlite_disable", error))
        finally:
            try:
                connection.close()
            except Exception as error:
                errors.append(_failure("sqlite_close", error))
    return {
        "status": "error" if errors else "ok",
        "sqlite_version": sqlite3.sqlite_version,
        "vec_version": version,
        "errors": errors,
    }


def _check_crypto() -> dict[str, object]:
    """Import native crypto bindings and identify both loaded OpenSSL implementations."""
    import ssl

    import cryptography
    from cryptography.hazmat.bindings.openssl.binding import Binding

    binding = Binding()
    native_version = binding.ffi.string(binding.lib.OpenSSL_version(0)).decode("ascii")
    if len(native_version) > 256 or len(ssl.OPENSSL_VERSION) > 256:
        raise ValueError("A native OpenSSL identity exceeds its diagnostic bound.")
    return {
        "status": "ok",
        "stdlib_openssl": ssl.OPENSSL_VERSION,
        "cryptography_version": cryptography.__version__,
        "cryptography_openssl": native_version,
    }


def check_runtime(expected_python: str) -> dict[str, object]:
    """Check the executing interpreter and native dependencies without contacting providers."""
    actual_python = platform.python_version()
    checks: dict[str, dict[str, object]] = {
        "python": {"status": "ok" if actual_python == expected_python else "error"}
    }
    report: dict[str, object] = {
        "status": "error",
        "expected_python": expected_python,
        "actual_python": actual_python,
        "executable": sys.executable,
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "checks": checks,
    }
    if actual_python != expected_python:
        checks["python"]["message"] = "The selected interpreter doesn't match the native build pin."
        return report
    for phase, probe in (("sqlite", _check_sqlite), ("crypto", _check_crypto)):
        try:
            checks[phase] = probe()
        except Exception as error:
            checks[phase] = {"status": "error", "errors": [_failure(phase, error)]}
        if checks[phase]["status"] != "ok":
            return report
    report["status"] = "ok"
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Print one structured build diagnostic and refuse packaging on any failed check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-python", required=True, help="Exact managed native build version."
    )
    arguments = parser.parse_args(argv)
    report = check_runtime(arguments.expected_python)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
