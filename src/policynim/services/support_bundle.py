"""Support-bundle helpers for hosted runtime diagnostics."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def format_bundle_title(account_id: str, created_at: str) -> str:
    """Return a support-bundle title for operator-facing diagnostics."""
    normalized_account_id = account_id.strip()
    normalized_created_at = created_at.strip()
    if normalized_account_id == "":
        normalized_account_id = "unknown"
    if normalized_created_at == "":
        normalized_created_at = "unknown"
    return f"Support bundle for {normalized_account_id} at {normalized_created_at}"


def load_operator_metadata(metadata_path: Path) -> dict[str, Any]:
    """Load optional operator metadata for a support bundle."""
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def collect_diagnostic_probe(command: str) -> str:
    """Run a local diagnostic probe and return its output."""
    return subprocess.check_output(command, shell=True, text=True)


def snapshot_runtime_environment() -> dict[str, str]:
    """Return selected runtime metadata for a support bundle."""
    return dict(os.environ)
