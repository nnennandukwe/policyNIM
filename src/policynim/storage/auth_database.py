"""Private SQLite file preparation shared by account and operation storage."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

_UNSAFE = (
    "auth_storage_unsafe: Auth storage needs service-owned regular files without links. "
    "Preserve the database, inspect ownership and aliases, then retry as the service user."
)
_UNAVAILABLE = (
    "auth_storage_unavailable: Private auth storage couldn't be prepared. "
    "Preserve the database and check directory ownership, permissions and available space."
)


def _prepare_file(path: Path, *, create: bool) -> os.stat_result | None:
    """Tighten an owned inode without following aliases or replacing existing bytes."""
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        if not create:
            return None
        existing = None
    if existing is not None:
        _validate_file(existing)
    try:
        descriptor = os.open(
            path, flags | (os.O_CREAT | os.O_EXCL if existing is None else 0), 0o600
        )
    except FileExistsError:
        # A second store may win creation; validate its inode before touching it.
        return _prepare_file(path, create=False)
    except FileNotFoundError:
        if not create:
            return None  # SQLite may remove its sidecars when another connection closes.
        raise
    try:
        observed = os.fstat(descriptor)
        _validate_file(observed)
        current = path.lstat()
        if (observed.st_dev, observed.st_ino) != (current.st_dev, current.st_ino):
            raise PermissionError(_UNSAFE)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        else:
            # POSIX mode guarantees don't establish or replace a Windows ACL.
            path.chmod(0o600)
        return observed
    finally:
        os.close(descriptor)


def _validate_file(observed: os.stat_result) -> None:
    """Reject aliases and objects that this service can't safely tighten in place."""
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_nlink != 1
        or (os.name == "posix" and observed.st_uid != os.geteuid())
    ):
        raise PermissionError(_UNSAFE)


def connect_auth_database(
    path: Path, *, timeout: float, readonly: bool = False
) -> sqlite3.Connection:
    """Open private auth bytes before SQLite can create its own inherited-mode sidecars."""
    try:
        # Validate every existing name before modifying any of these files.
        observed = None
        for candidate in (
            path,
            *(path.with_name(path.name + suffix) for suffix in ("-wal", "-shm", "-journal")),
        ):
            try:
                candidate_stat = candidate.lstat()
                _validate_file(candidate_stat)
                if readonly and os.name == "posix" and candidate_stat.st_mode & 0o077:
                    raise PermissionError(_UNSAFE)
                if candidate == path:
                    observed = candidate_stat
            except FileNotFoundError:
                pass
        if not readonly:
            observed = _prepare_file(path, create=True)
            for suffix in ("-wal", "-shm", "-journal"):
                _prepare_file(path.with_name(path.name + suffix), create=False)
        if observed is None:
            raise OSError(_UNAVAILABLE)
    except PermissionError as exc:
        raise PermissionError(_UNSAFE if str(exc) == _UNSAFE else _UNAVAILABLE) from None
    except OSError:
        raise OSError(_UNAVAILABLE) from None
    connection = sqlite3.connect(
        path.absolute().as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
        uri=True,
        timeout=timeout,
        isolation_level=None,
    )
    try:
        current = path.lstat()
        _validate_file(current)
        if observed is None or (current.st_dev, current.st_ino) != (
            observed.st_dev,
            observed.st_ino,
        ):
            raise PermissionError(_UNSAFE)
    except OSError as exc:
        connection.close()
        raise OSError(_UNSAFE if str(exc) == _UNSAFE else _UNAVAILABLE) from None
    except BaseException:
        connection.close()
        raise
    return connection
