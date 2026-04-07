"""Non-live contract checks for Docker secret handling."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
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
