"""Support-bundle helpers for hosted runtime diagnostics."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Sequence


_DEFAULT_DIAGNOSTIC_PROBE_TIMEOUT_SECONDS = 30.0

# Intentionally small allowlist: support bundles must not exfiltrate secrets.
_SAFE_ENV_KEYS = {
    "POLICYNIM_ENV",
    "PYTHON_VERSION",
    "VIRTUAL_ENV",
    "CONDA_DEFAULT_ENV",
}


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


def collect_diagnostic_probe(
    command: Sequence[str],
    *,
    timeout_seconds: float = _DEFAULT_DIAGNOSTIC_PROBE_TIMEOUT_SECONDS,
) -> str:
    """Run a local diagnostic probe and return its output.

    The probe command is executed without a shell to avoid command injection.
    """
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        return f"exit_code={completed.returncode}\n{output}".rstrip()
    return output.rstrip()


def snapshot_runtime_environment() -> dict[str, str]:
    """Return selected runtime metadata for a support bundle.

    This intentionally avoids returning the full process environment because it may
    contain secrets (API keys, tokens, client secrets) that must not appear in
    support-bundle output.
    """
    snapshot: dict[str, str] = {}
    for key in sorted(_SAFE_ENV_KEYS):
        value = os.environ.get(key)
        if value is not None:
            snapshot[key] = value
    return snapshot
