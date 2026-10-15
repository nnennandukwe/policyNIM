"""Process-scoped advisory locks for local writers on POSIX and Windows."""

from __future__ import annotations

import errno
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Keep a stable lock inode; closing its descriptor releases ownership after interruption."""
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("Writer lock must be a regular file.")
        if os.name == "nt":
            _lock_windows_descriptor(descriptor)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _lock_windows_descriptor(descriptor: int) -> None:
    """Retry only Windows lock contention; cancellation and fatal errors still propagate."""
    import msvcrt

    while True:
        try:
            getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_NBLCK"), 1)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            time.sleep(0.05)
