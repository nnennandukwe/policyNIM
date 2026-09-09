"""Non-live contract checks for Docker secret handling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from policynim.settings import Settings

NO_ENV: dict[str, Any] = {"_env_file": None}

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
RAILWAY_DOCKERFILE = REPO_ROOT / "Dockerfile.railway"
RAILWAY_CONFIG = REPO_ROOT / "railway.toml"
HOSTED_OPERATIONS = REPO_ROOT / "docs" / "hosted-beta-operations.md"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_dockerfile_uses_buildkit_secret_for_nvidia_api_key() -> None:
    text = _read_text(DOCKERFILE)

    assert "ARG NVIDIA_API_KEY" not in text
    assert "ENV NVIDIA_API_KEY" not in text
    assert "--mount=type=secret,id=nvidia_api_key" in text
    assert "/run/secrets/nvidia_api_key" in text


def test_hosted_operations_doc_uses_secret_build_invocation() -> None:
    text = " ".join(_read_text(HOSTED_OPERATIONS).split())

    assert "DOCKER_BUILDKIT=1 docker build" in text
    assert "--secret id=nvidia_api_key,env=NVIDIA_API_KEY" in text
    assert "-t policynim-hosted ." in text
    assert "--build-arg NVIDIA_API_KEY" not in text


def test_railway_uses_a_dedicated_compatible_dockerfile() -> None:
    railway_text = _read_text(RAILWAY_CONFIG)
    dockerfile_text = _read_text(RAILWAY_DOCKERFILE)

    assert 'dockerfilePath = "Dockerfile.railway"' in railway_text
    assert "ARG NVIDIA_API_KEY" in dockerfile_text
    assert "--mount=type=secret" not in dockerfile_text


def test_container_builds_include_project_metadata_files() -> None:
    """Keep minimal Docker build contexts compatible with package metadata."""
    for path in (DOCKERFILE, RAILWAY_DOCKERFILE):
        text = _read_text(path)

        assert "COPY pyproject.toml uv.lock README.md LICENSE ./" in text


def test_hosted_container_builds_sync_without_legacy_index_extra() -> None:
    """Keep hosted deploys on the default SQLite-backed package install."""
    for path in (DOCKERFILE, RAILWAY_DOCKERFILE):
        text = _read_text(path)

        assert "uv sync --frozen --extra hosted-legacy-index" not in text
        assert "uv sync --frozen" in text
        assert "POLICYNIM_INDEX_DB_PATH=/app/data/index.sqlite3" in text


def test_hosted_operations_doc_explains_railway_dockerfile_split() -> None:
    text = " ".join(_read_text(HOSTED_OPERATIONS).split())

    assert "Railway only supports `--mount=type=cache`" in text
    assert "`Dockerfile.railway`" in text
    assert "`Dockerfile`" in text


def test_ingestion_build_arguments_match_runtime_defaults_and_publish_rules() -> None:
    """Verify ingestion build arguments match runtime defaults and publish rules."""
    settings = Settings(**NO_ENV)
    expected = {
        "POLICYNIM_NVIDIA_EMBED_MODEL": settings.nvidia_embed_model,
        "POLICYNIM_NVIDIA_BASE_URL": settings.nvidia_base_url,
        "POLICYNIM_EMBED_BATCH_SIZE": str(settings.embed_batch_size),
        "POLICYNIM_NVIDIA_TIMEOUT_SECONDS": str(settings.nvidia_timeout_seconds),
        "POLICYNIM_NVIDIA_MAX_RETRIES": str(settings.nvidia_max_retries),
    }
    for path in (DOCKERFILE, RAILWAY_DOCKERFILE):
        text = _read_text(path)
        global_args, builder, runtime = text.split("FROM ${PYTHON_BASE_IMAGE}")
        for name, value in expected.items():
            assert f"ARG {name}={value}\n" in global_args
            for stage in (builder, runtime):
                assert f"ARG {name}\n" in stage
                assert f"{name}=${{{name}}}" in stage
        assert "uv run --no-sync policynim ingest" in builder
        assert "COPY --from=builder /app/data/runtime/runtime_rules.json " in runtime
        assert (
            "POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH=/app/data/runtime/runtime_rules.json" in runtime
        )
