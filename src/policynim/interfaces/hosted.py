"""Start hosted MCP after validating or seeding its persistent generation store."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

from pydantic import ValidationError

from policynim.errors import GenerationError, PolicyNIMError
from policynim.services.generations import GenerationStore
from policynim.settings import Settings, get_settings
from policynim.types import EmbeddingIdentity

_SEED_DIR = Path("/app/artifacts/public-generation")


def _require_state_mount(settings: Settings) -> None:
    """Reject a missing declared volume or mutable paths outside it before any writes."""
    mount = settings.hosted_state_mount
    if mount is None:
        return
    try:
        if (
            not mount.is_absolute()
            or ".." in mount.parts
            or mount.parent == mount
            or mount.is_symlink()
            or not mount.is_mount()
        ):
            raise GenerationError(
                "storage_mount",
                "The configured hosted state volume isn't mounted. Mount it before restarting.",
            )
        root = mount.resolve(strict=True)
        paths = (
            settings.generations_dir,
            settings.beta_auth_db_path,
            settings.eval_workspace_dir,
            settings.runtime_evidence_db_path,
        )
        if any(not path.resolve().is_relative_to(root) for path in paths):
            raise GenerationError(
                "storage_mount",
                "A mutable state path is outside the configured hosted state volume. "
                "Put generation, auth, evaluation, and runtime evidence paths on that volume.",
            )
    except (OSError, RuntimeError) as error:
        raise GenerationError(
            "storage_mount",
            "Hosted state storage couldn't be inspected. Check the mount and path permissions.",
        ) from error


def prepare_hosted_generation(settings: Settings, *, seed_dir: Path = _SEED_DIR) -> str:
    """Keep the active volume selection, or conditionally seed a genuinely absent root."""
    _require_state_mount(settings)
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
    phase = "settings"
    try:
        settings = get_settings()
        phase = "generation_storage"
        prepare_hosted_generation(settings)
        phase = "process_exec"
        os.execvp("policynim", ["policynim", "mcp", "--transport", "streamable-http"])
    except (PolicyNIMError, OSError, ValueError) as error:
        messages = {
            "settings": (
                "Hosted settings couldn't be loaded. Check the configuration fields and "
                "environment permissions before restarting."
            ),
            "generation_storage": (
                "Hosted startup couldn't validate or seed generation storage. "
                "Existing state is preserved. Check the volume, configuration, and "
                "image seed; run `policynim generation inspect`. For an interrupted "
                "first import, preserve the root and explicitly import a verified "
                "bundle before restarting."
            ),
            "process_exec": (
                "Hosted MCP couldn't start after generation validation. "
                "The committed generation is preserved. Check the installed "
                "policynim command and executable permissions before restarting."
            ),
        }
        failure: dict[str, object] = {
            "status": "error",
            "phase": phase,
            "message": messages[phase],
        }
        if isinstance(error, ValidationError):
            fields = set()
            for detail in error.errors(include_input=False, include_context=False):
                location = detail["loc"]
                if location and isinstance(location[0], str):
                    field = location[0].lower().removeprefix("policynim_")
                    if field in Settings.model_fields:
                        fields.add(field)
            failure["invalid_settings"] = sorted(fields)[:32]
        if isinstance(error, GenerationError):
            failure["generation_phase"] = error.phase
            failure["message"] = str(error)[:2048]
        print(json.dumps(failure), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
