"""Bounded local inputs and single-file report replacement for release tools."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

MAX_DOCUMENT_BYTES = 1024 * 1024


def read_bytes(path: Path, *, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    """Reject links/special files and bound reads without trusting a pre-open size alone."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if path.is_symlink():
        raise ValueError("Symlink inputs aren't accepted.")
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        observed = os.fstat(stream.fileno())
        if not stat.S_ISREG(observed.st_mode) or observed.st_size > limit:
            raise ValueError("Input must be a bounded regular file.")
        payload = stream.read(limit + 1)
        if len(payload) > limit:
            raise ValueError("Input exceeds its size limit.")
        return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON members instead of silently choosing one claim."""
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON member.")
        result[name] = value
    return result


def _reject_constant(value: str) -> None:
    """Reject JavaScript nonfinite constants that aren't valid JSON values."""
    raise ValueError("Nonfinite JSON constant.")


def decode_json(payload: bytes) -> Any:
    """Parse the exact bytes used for a receipt digest, rejecting duplicate members."""
    return json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def read_json(path: Path) -> Any:
    """Decode one bounded document while retaining duplicate-member rejection."""
    return decode_json(read_bytes(path))


def write_document(path: Path, payload: bytes) -> None:
    """Replace one explicit report after closing/syncing its owned staged sibling.

    The parent is a trusted local directory with one coordinated writer. This doesn't
    provide power-loss durability or serialize unrelated concurrent writers.
    """
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise ValueError("Report exceeds its size limit.")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Output must be a regular file, never a symlink.")
    staged: Path | None = None
    staged_identity: tuple[int, int] | None = None
    primary: BaseException | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".policynim-evidence-", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            staged = Path(stream.name)
            identity = os.fstat(stream.fileno())
            staged_identity = (identity.st_dev, identity.st_ino)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Output changed to an unsupported file type.")
        observed = staged.lstat()
        if (observed.st_dev, observed.st_ino) != staged_identity or not stat.S_ISREG(
            observed.st_mode
        ):
            raise ValueError("Staged report ownership changed.")
        os.replace(staged, path)
        staged = None
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if staged is not None:
            try:
                try:
                    observed = staged.lstat()
                except FileNotFoundError:
                    observed = None
                if observed is not None:
                    if (observed.st_dev, observed.st_ino) != staged_identity:
                        raise ValueError("Staged report ownership changed; replacement preserved.")
                    staged.unlink()
            except (OSError, ValueError) as exc:
                if primary is None:
                    raise
                primary.add_note(
                    "Owned staged report cleanup failed ("
                    + type(exc).__name__[:64]
                    + "); inspect .policynim-evidence-*.tmp in the selected output directory."
                )
