"""Public store shutdown under sharing restrictions and real SQLite contention."""

from __future__ import annotations

import errno
import os
import sqlite3
from pathlib import Path

import pytest

from policynim.services.operation_admission import admit_operation
from policynim.storage.operation_lease import inspect_owner_lease
from policynim.storage.operation_store import OperationStore


def test_clean_close_removes_every_owner_alias(tmp_path: Path) -> None:
    """Run unchanged on native Windows as well as POSIX to verify handle cleanup."""
    path = tmp_path / "auth.sqlite3"
    store = OperationStore(path)
    store.close()
    store.close()
    assert list(path.with_name(path.name + ".owners").iterdir()) == []


def test_clean_close_retries_deferred_aliases_after_releasing_the_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Model Windows sharing denial at the filesystem boundary through the public store API."""
    original_open = os.open
    original_unlink = Path.unlink
    descriptors: list[int] = []

    def track_owner_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        """Track the real pending owner handle for sharing checks."""
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith(".pending"):
            descriptors.append(descriptor)
        return descriptor

    def reject_open_inode(path: Path, missing_ok: bool = False) -> None:
        """Reject unlink only while this process holds the same inode open."""
        target = path.stat(follow_symlinks=False)
        for descriptor in descriptors:
            try:
                observed = os.fstat(descriptor)
            except OSError:
                continue
            if (target.st_dev, target.st_ino) == (observed.st_dev, observed.st_ino):
                raise PermissionError(errno.EACCES, "private sharing-denial payload")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(os, "open", track_owner_open)
    monkeypatch.setattr(Path, "unlink", reject_open_inode)
    path = tmp_path / "auth.sqlite3"
    store = OperationStore(path)
    directory = path.with_name(path.name + ".owners")
    assert len(list(directory.iterdir())) == 2  # Pending alias couldn't be removed while open.
    store.close()
    store.close()
    assert list(directory.iterdir()) == []
    assert "private sharing-denial payload" not in caplog.text


@pytest.mark.parametrize("completed", [False, True])
def test_close_defers_without_raising_or_releasing_uncertain_ownership(
    tmp_path: Path, completed: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """An exclusive SQLite writer can't break shutdown or release an unverified active owner."""
    path = tmp_path / "auth.sqlite3"
    store = OperationStore(path)
    accepted = admit_operation(
        store,
        principal_id="caller",
        operation="policy_search",
        daily_limit=5,
        principal_limit=1,
        global_limit=1,
    )
    assert accepted.operation_id is not None
    if completed:
        store.finish(accepted.operation_id, "completed")
    directory = path.with_name(path.name + ".owners")
    owner = next(directory.glob("*.lock")).stem
    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        store.close()
        assert inspect_owner_lease(path, owner) == "live"
        writer.commit()
    assert store.usage("caller").active == (0 if completed else 1)
    if not completed:
        store.finish(accepted.operation_id, "completed")
    store.close()
    assert list(directory.iterdir()) == []
    assert str(tmp_path) not in caplog.text
