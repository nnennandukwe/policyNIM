"""Opt-in Docker build checks for the hosted image contract."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_RUN_DOCKER_TESTS = os.getenv("POLICYNIM_RUN_DOCKER_TESTS", "").strip() == "1"
_NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()


def _docker_ready() -> tuple[bool, str]:
    docker = shutil.which("docker")
    if docker is None:
        return False, "docker is not installed."

    try:
        probe = subprocess.run(
            [docker, "info"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "docker is installed but `docker info` timed out."

    if probe.returncode != 0:
        return False, "docker is installed but the daemon is unavailable."

    return True, ""


if _RUN_DOCKER_TESTS:
    _DOCKER_READY, _DOCKER_REASON = _docker_ready()
else:
    _DOCKER_READY, _DOCKER_REASON = False, ""

pytestmark = [
    pytest.mark.docker_live,
    pytest.mark.skipif(
        not _RUN_DOCKER_TESTS,
        reason="Set POLICYNIM_RUN_DOCKER_TESTS=1 to run Docker build tests.",
    ),
    pytest.mark.skipif(not _DOCKER_READY, reason=_DOCKER_REASON),
]


def test_docker_builder_stage_fails_without_nvidia_api_key() -> None:
    tag = f"policynim-hosted-missing-key:{uuid.uuid4().hex[:12]}"
    env = dict(os.environ)
    env["DOCKER_BUILDKIT"] = "1"

    try:
        result = subprocess.run(
            [
                "docker",
                "build",
                "--target",
                "builder",
                "--no-cache",
                "--progress=plain",
                "--build-arg",
                "NVIDIA_API_KEY=",
                "-t",
                tag,
                ".",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
            timeout=900,
        )
    finally:
        subprocess.run(
            ["docker", "image", "rm", "-f", tag],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

    combined_output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "NVIDIA_API_KEY is required for embeddings." in combined_output


def test_runtime_image_contains_non_empty_baked_index() -> None:
    if not _NVIDIA_API_KEY:
        pytest.skip("NVIDIA_API_KEY is not configured for positive Docker build validation.")

    tag = f"policynim-hosted-baked-index:{uuid.uuid4().hex[:12]}"
    env = dict(os.environ)
    env["DOCKER_BUILDKIT"] = "1"
    env["NVIDIA_API_KEY"] = _NVIDIA_API_KEY

    try:
        build_result = subprocess.run(
            [
                "docker",
                "build",
                "--progress=plain",
                "--build-arg",
                "NVIDIA_API_KEY",
                "-t",
                tag,
                ".",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
            timeout=900,
        )
        build_output = build_result.stdout + build_result.stderr
        assert build_result.returncode == 0, build_output

        dump_result = subprocess.run(
            ["docker", "run", "--rm", tag, "policynim", "dump-index", "--count-only"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=120,
        )
    finally:
        subprocess.run(
            ["docker", "image", "rm", "-f", tag],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

    dump_output = dump_result.stdout + dump_result.stderr
    assert dump_result.returncode == 0, dump_output

    match = re.search(r"Indexed chunks:\s*(\d+)", dump_result.stdout)
    assert match is not None, dump_output
    assert int(match.group(1)) > 0
