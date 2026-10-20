"""Opt-in Docker build checks for the hosted image contract."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_RUN_DOCKER_TESTS = os.getenv("POLICYNIM_RUN_DOCKER_TESTS", "").strip() == "1"


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


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "Dockerfile.railway"])
def test_runtime_image_imports_generation_without_credentials(dockerfile: str) -> None:
    """Build both image paths and probe actual HTTP startup/discovery with networking disabled."""
    tag = f"policynim-offline-generation:{uuid.uuid4().hex[:12]}"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("POLICYNIM_") and key != "NVIDIA_API_KEY"
    }
    env["DOCKER_BUILDKIT"] = "1"
    try:
        build = subprocess.run(
            ["docker", "build", "-f", dockerfile, "--progress=plain", "-t", tag, "."],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
            timeout=900,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        probe = subprocess.run(
            ["docker", "run", "--rm", "--network=none", tag, "python", "-c", _PROBE],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=120,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
        report = json.loads(probe.stdout)
        assert report["health"]["generation_id"] == report["generation_id"]
        assert report["health"]["row_count"] > 0
        assert (
            report["modern"]
            == report["legacy"]
            == ["policy_compile", "policy_preflight", "policy_preflight_evidence", "policy_search"]
        )
        assert report["provider_credentials"] is False
    finally:
        subprocess.run(
            ["docker", "image", "rm", "-f", tag],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )


_PROBE = r"""
import asyncio,json,os,subprocess,time
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from policynim.settings import get_settings
from policynim.services.index_store import create_index_store
from policynim.services.index_store import pinned_generation
from policynim.services.health import create_runtime_health_service
settings=get_settings()
assert not settings.nvidia_api_key
store=create_index_store(settings)
try:
 snapshot=pinned_generation(store)
 assert snapshot is not None
 generation_id=snapshot.generation_id
 assert len(store.list_chunks())==snapshot.manifest.chunk_count
finally:
 store.close()
env=dict(os.environ)
env.update(POLICYNIM_MCP_HOST="127.0.0.1",POLICYNIM_MCP_PORT="8000",
           POLICYNIM_MCP_PUBLIC_BASE_URL="http://127.0.0.1:8000")
process=subprocess.Popen(["policynim","mcp","--transport","streamable-http"],env=env,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
async def discover(modern):
 "List tools through the requested MCP handshake without calling providers."
 async with httpx2.AsyncClient(timeout=10) as client:
  async with streamable_http_client("http://127.0.0.1:8000/mcp",http_client=client) as (read,write):
   async with ClientSession(read,write) as session:
    if modern: await session.discover()
    else: await session.initialize()
    return sorted(tool.name for tool in (await session.list_tools()).tools)
try:
 deadline=time.monotonic()+30
 while True:
  try:
   response=httpx2.get("http://127.0.0.1:8000/healthz",timeout=1)
   if response.status_code==200: break
  except httpx2.HTTPError: pass
  assert process.poll() is None and time.monotonic()<deadline
  time.sleep(0.1)
 print(json.dumps({"generation_id":generation_id,"health":response.json(),
   "modern":asyncio.run(discover(True)),"legacy":asyncio.run(discover(False)),
   "provider_credentials":bool(settings.nvidia_api_key)}))
finally:
 process.terminate()
 try: process.wait(timeout=10)
 except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
"""
