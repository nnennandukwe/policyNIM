"""Exercise checkout bytes used by cross-platform release evidence."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("autocrlf", ["true", "false", "input"])
def test_release_lock_checkout_preserves_source_bytes(tmp_path: Path, autocrlf: str) -> None:
    """Keep the receipt's lock hash identical across Git newline configurations."""
    repository = tmp_path / "checkout"
    repository.mkdir()

    def git(*arguments: str) -> bytes:
        """Run Git inside this owned fixture without inheriting repository attributes."""
        return subprocess.check_output(
            ["git", "-C", str(repository), *arguments], stderr=subprocess.PIPE
        )

    git("init", "--quiet")
    git("config", "core.autocrlf", autocrlf)
    git("config", "core.attributesFile", str(repository / "absent-global-attributes"))
    (repository / ".gitattributes").write_bytes((REPO_ROOT / ".gitattributes").read_bytes())
    source = (REPO_ROOT / "uv.lock").read_bytes()
    assert b"\r" not in source
    lock = repository / "uv.lock"
    lock.write_bytes(source)
    git("add", ".gitattributes", "uv.lock")
    staged = git("show", ":uv.lock")
    assert staged == source
    lock.unlink()
    git("checkout-index", "--force", "--", "uv.lock")
    assert lock.read_bytes() == staged
