"""Kernel-backed process ownership for crash recovery across reused process IDs."""

from __future__ import annotations

import errno
import logging
import os
import re
import stat
import uuid
import weakref
from pathlib import Path
from typing import Literal


class OperationOwner:
    """Hold a unique owner lock until its service closes or its process exits."""

    def __init__(self, database: Path) -> None:
        """Create a private lock inode independent of container PID namespaces."""
        self.directory = database.with_name(database.name + ".owners")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.identity = uuid.uuid4().hex
        path = self.directory / (self.identity + ".lock")
        self._path = path
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            os.write(descriptor, b"0")
            _lock(descriptor)
        except BaseException:
            try:
                path.unlink()
            except OSError:
                logging.getLogger(__name__).warning(
                    "Operation owner cleanup failed: storage_unavailable."
                )
            finally:
                os.close(descriptor)
            raise
        self._descriptor = descriptor
        self._release = weakref.finalize(self, os.close, descriptor)

    def abandoned(self, identity: str, *, remove: bool = False) -> bool:
        """Prove a lease is unlocked and optionally remove its retired inode after verification."""
        if not re.fullmatch(r"[0-9a-f]{32}", identity):
            return False
        path = self.directory / (identity + ".lock")
        try:
            descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return False
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                return False
            try:
                _lock(descriptor)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    return False
                raise
        finally:
            os.close(descriptor)
        if remove:
            try:
                current = path.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (observed.st_dev, observed.st_ino):
                    path.unlink()
            except FileNotFoundError:
                pass  # Another local cleanup already removed this retired owner.
        return True

    def close(self) -> None:
        """Release this service's lease after its workers have drained."""
        if not self._release.alive:
            return
        try:
            observed = self._path.stat(follow_symlinks=False)
            owned = os.fstat(self._descriptor)
            if (observed.st_dev, observed.st_ino) == (owned.st_dev, owned.st_ino):
                self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logging.getLogger(__name__).warning(
                "Operation owner cleanup failed: storage_unavailable."
            )
        finally:
            self._release()


def _lock(descriptor: int) -> None:
    """Acquire a nonblocking process-released lock on POSIX or Windows."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_NBLCK"), 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def inspect_owner_lease(database: Path, identity: str) -> Literal["live", "abandoned", "unknown"]:
    """Observe an existing kernel lease without creating files or changing durable state."""
    if not re.fullmatch(r"[0-9a-f]{32}", identity):
        return "unknown"
    path = database.with_name(database.name + ".owners") / (identity + ".lock")
    mode = os.O_RDWR if os.name == "nt" else os.O_RDONLY
    try:
        descriptor = os.open(path, mode | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return "unknown"
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return "unknown"
        try:
            if os.name == "nt":
                _lock(descriptor)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            return "live" if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} else "unknown"
        return "abandoned"
    finally:
        os.close(descriptor)
