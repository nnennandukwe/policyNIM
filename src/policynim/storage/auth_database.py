"""Private SQLite file preparation shared by account and operation storage."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from threading import RLock
from typing import Literal

_UNSAFE = (
    "auth_storage_unsafe: Auth storage needs service-owned regular files without links. "
    "Preserve the database, inspect ownership, permissions and aliases, "
    "then retry as the service user."
)
_UNAVAILABLE = (
    "auth_storage_unavailable: Private auth storage couldn't be prepared. "
    "Preserve the database and check directory ownership, permissions and available space."
)
_PREPARATION_LOCK = RLock()


class AuthDatabaseError(OSError):
    """Expose only an owned safe file-category message to application services."""

    def __init__(self, code: Literal["auth_storage_unsafe", "auth_storage_unavailable"]) -> None:
        """Keep unsafe aliases separate from an unavailable private filesystem."""
        self.code = code
        super().__init__(_UNSAFE if code == "auth_storage_unsafe" else _UNAVAILABLE)


def _validate_file(observed: os.stat_result) -> None:
    """Reject aliases and objects that this service can't safely tighten in place."""
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_nlink != 1
        or (os.name == "posix" and observed.st_uid != os.geteuid())
    ):
        raise AuthDatabaseError("auth_storage_unsafe")


def _observe_file(path: Path, *, optional_sidecar: bool = False) -> os.stat_result | None:
    """Recheck retired optional inodes without opening descriptors or accepting live aliases."""
    for _ in range(3):
        try:
            observed = path.lstat()
        except FileNotFoundError:
            return None
        if optional_sidecar and observed.st_nlink == 0:
            # SQLite can unlink its last-reader sidecar after lstat acquires the inode.
            continue
        return observed
    raise AuthDatabaseError("auth_storage_unavailable")


def _prepare_file(path: Path, *, create: bool) -> os.stat_result | None:
    """Tighten an owned path without opening existing SQLite inodes or dropping their locks."""
    observed = _observe_file(path, optional_sidecar=not create)
    if observed is None:
        if not create:
            return None
        try:
            descriptor = os.open(
                path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
        except FileExistsError:
            pass
        else:
            # The preparation lock keeps another local helper from opening SQLite until
            # this fresh descriptor closes. Extra closes on existing inodes drop fcntl locks.
            os.close(descriptor)
        observed = path.lstat()
    _validate_file(observed)
    try:
        if stat.S_IMODE(observed.st_mode) != 0o600:
            if os.chmod in os.supports_follow_symlinks:
                os.chmod(path, 0o600, follow_symlinks=False)
            else:
                # Trusted service-controlled parents and the same-UID boundary are required.
                os.chmod(path, 0o600)
        current = _observe_file(path, optional_sidecar=not create)
        if current is None:
            if not create:
                return None
            raise FileNotFoundError
    except FileNotFoundError:
        if not create:
            return None  # A last SQLite reader may remove its WAL/SHM sidecars.
        raise
    _validate_file(current)
    if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
        if not create:
            return None  # SQLite replaced a retired sidecar with another valid inode.
        raise AuthDatabaseError("auth_storage_unsafe")
    return current


def connect_auth_database(
    path: Path, *, timeout: float, readonly: bool = False
) -> sqlite3.Connection:
    """Keep auth bytes private without disturbing locks owned by live SQLite connections."""
    with _PREPARATION_LOCK:
        try:
            observed = None
            # Inspect every existing name before changing permissions on any of them.
            for candidate in (
                path,
                *(path.with_name(path.name + suffix) for suffix in ("-wal", "-shm", "-journal")),
            ):
                candidate_stat = _observe_file(candidate, optional_sidecar=candidate != path)
                if candidate_stat is None:
                    continue
                _validate_file(candidate_stat)
                if readonly and os.name == "posix" and candidate_stat.st_mode & 0o077:
                    raise AuthDatabaseError("auth_storage_unsafe")
                if candidate == path:
                    observed = candidate_stat
            if not readonly:
                observed = _prepare_file(path, create=True)
                for suffix in ("-wal", "-shm", "-journal"):
                    _prepare_file(path.with_name(path.name + suffix), create=False)
            if observed is None:
                raise AuthDatabaseError("auth_storage_unavailable")
        except AuthDatabaseError:
            raise
        except OSError:
            raise AuthDatabaseError("auth_storage_unavailable") from None
        connection = sqlite3.connect(
            path.absolute().as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
            uri=True,
            timeout=timeout,
            isolation_level=None,
        )
        try:
            current = path.lstat()
            _validate_file(current)
            if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
                raise AuthDatabaseError("auth_storage_unsafe")
        except AuthDatabaseError:
            connection.close()
            raise
        except OSError:
            connection.close()
            raise AuthDatabaseError("auth_storage_unavailable") from None
        except BaseException:
            connection.close()
            raise
        return connection
