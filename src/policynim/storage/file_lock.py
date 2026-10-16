"""Process-scoped advisory locks for local writers on POSIX and Windows."""

from __future__ import annotations

import errno
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_file_lock(path: Path, *, on_wait: Callable[[], None] | None = None) -> Iterator[None]:
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
            _lock_windows_descriptor(descriptor, on_wait=on_wait)
        else:
            import fcntl

            if on_wait is None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            else:
                while True:
                    on_wait()
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        if exc.errno not in {errno.EAGAIN, errno.EWOULDBLOCK}:
                            raise
                        time.sleep(0.05)
                    else:
                        break
        yield
    finally:
        os.close(descriptor)


def _lock_windows_descriptor(descriptor: int, *, on_wait: Callable[[], None] | None = None) -> None:
    """Retry only Windows lock contention; cancellation and fatal errors still propagate."""
    import msvcrt

    while True:
        if on_wait is not None:
            on_wait()
        try:
            getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_NBLCK"), 1)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            time.sleep(0.05)


@contextmanager
def try_exclusive_file_lock(path: Path) -> Iterator[bool]:
    """Probe an existing reader lease without waiting or creating a replacement inode."""
    descriptor = os.open(
        path, os.O_RDWR | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("Reader lease must be a regular file.")
        try:
            if os.name == "nt":
                import msvcrt

                getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_NBLCK"), 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            acquired = False
        else:
            acquired = True
        yield acquired
    finally:
        os.close(descriptor)
