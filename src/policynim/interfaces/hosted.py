"""Start hosted MCP after validating or seeding its persistent generation store."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

from policynim.errors import GenerationError, PolicyNIMError
from policynim.services.generations import GenerationStore
from policynim.settings import Settings, get_settings
from policynim.types import EmbeddingIdentity

_SEED_DIR = Path("/app/artifacts/public-generation")


def prepare_hosted_generation(settings: Settings, *, seed_dir: Path = _SEED_DIR) -> str:
    """Keep the active volume selection, or conditionally seed a genuinely absent root."""
    store = GenerationStore(
        root=settings.generations_dir.absolute(),
        embedding=EmbeddingIdentity(
            model=settings.nvidia_embed_model, endpoint=settings.nvidia_base_url
        ),
    )
    try:
        store.root.lstat()
    except FileNotFoundError:
        checksum_path = seed_dir / "generation.sha256"
        if not stat.S_ISREG(checksum_path.lstat().st_mode):
            raise GenerationError("bootstrap", "The image seed checksum isn't a regular file.")
        with checksum_path.open("rb") as handle:
            checksum = handle.read(1025)
        if len(checksum) > 1024 or not re.fullmatch(rb"sha256:[0-9a-f]{64}\s*", checksum):
            raise GenerationError("bootstrap", "The image seed checksum isn't valid.")
        result = store.bootstrap_bundle(
            seed_dir / "generation.zip",
            expected_digest=checksum.decode("ascii").strip(),
            source_settings=settings,
            expected_seed_id=settings.generation_id,
        )
        for warning in result.warnings:
            print(json.dumps({"status": "warning", "message": warning}), file=sys.stderr)
    with store.pin(settings.generation_id, force_validation=True) as snapshot:
        return snapshot.generation_id


def main() -> int:
    """Validate durable state and replace this process so MCP receives signals directly."""
    phase = "generation_storage"
    try:
        prepare_hosted_generation(get_settings())
        phase = "process_exec"
        os.execvp("policynim", ["policynim", "mcp", "--transport", "streamable-http"])
    except (PolicyNIMError, OSError, ValueError) as error:
        failure = {
            "status": "error",
            "phase": phase,
            "message": (
                "Hosted startup couldn't validate or seed generation storage. "
                "Existing state is preserved. Check the volume, configuration, and "
                "image seed; run `policynim generation inspect`. For an interrupted "
                "first import, preserve the root and explicitly import a verified "
                "bundle before restarting."
            )
            if phase == "generation_storage"
            else (
                "Hosted MCP couldn't start after generation validation. "
                "The committed generation is preserved. Check the installed "
                "policynim command and executable permissions before restarting."
            ),
        }
        if isinstance(error, GenerationError):
            failure["generation_phase"] = error.phase
            failure["message"] = str(error)[:2048]
        print(json.dumps(failure), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
