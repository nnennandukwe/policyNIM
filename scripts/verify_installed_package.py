"""Verify an installed wheel, its generation bundle, and real MCP transports offline.

Run with a fresh environment's Python from a directory outside the source checkout.
This program never calls search/preflight or contacts a model provider.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sysconfig
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from packaging.requirements import Requirement

import policynim
from policynim.config_discovery import find_source_checkout_root


def require(condition: bool, message: str) -> None:
    """Keep verification active even if Python optimization disables assertions."""
    if not condition:
        raise RuntimeError(message)


def check_wheel(wheel: Path) -> dict[str, object]:
    """Compare installed package bytes with the supplied built artifact, not its version alone."""
    require(find_source_checkout_root() is None, "Run from outside a source checkout.")
    package = Path(policynim.__file__).resolve().parent
    checked = 0
    distribution = importlib.metadata.distribution("policynim")
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.endswith(".dist-info/entry_points.txt") or name.endswith(".dist-info/METADATA"):
                require(
                    Path(distribution.locate_file(name)).read_bytes() == archive.read(name),
                    "Installed distribution metadata differs from the wheel.",
                )
        for name in archive.namelist():
            if name.startswith("policynim/") and not name.endswith("/"):
                relative = Path(name).relative_to("policynim")
                require(".." not in relative.parts, "Unsafe wheel member.")
                require(
                    (package / relative).read_bytes() == archive.read(name),
                    f"Installed package differs from the wheel at {relative.as_posix()}.",
                )
                checked += 1
    require(checked > 0, "The wheel has no package files.")
    return {
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "verified_package_files": checked,
        "package_version": importlib.metadata.version("policynim"),
    }


def check_dependencies(
    lockfile: Path, expected_digest: str, requirements: Path
) -> dict[str, object]:
    """Bind installed dependency versions to the exact reviewed lockfile bytes."""
    require(lockfile.is_file(), "Dependency lockfile is missing.")
    data = lockfile.read_bytes()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    require(
        digest == expected_digest, "Dependency lockfile digest differs from the expected revision."
    )
    packages = tomllib.loads(data.decode("utf-8"))["package"]
    locked: dict[str, set[str]] = {}
    for package in packages:
        name = re.sub(r"[-_.]+", "-", package["name"]).lower()
        locked.setdefault(name, set()).add(package["version"])
    selected: set[str] = {"policynim"}
    require(requirements.is_file(), "Exported runtime requirements are missing.")
    requirement_bytes = requirements.read_bytes()
    for line in requirement_bytes.decode("utf-8").splitlines():
        if not line or line[0].isspace() or line.startswith("#"):
            continue
        requirement = Requirement(line.removesuffix("\\").strip())
        if requirement.marker is None or requirement.marker.evaluate():
            selected.add(re.sub(r"[-_.]+", "-", requirement.name).lower())
    versions: dict[str, str] = {}
    bootstrap: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        version = distribution.version
        if name in {"pip", "setuptools", "wheel"} and name not in selected:
            bootstrap[name] = version
            continue
        require(
            version in locked.get(name, set()),
            f"Installed dependency isn't locked: {name}=={version}.",
        )
        versions[name] = version
    require(selected <= versions.keys(), "A selected runtime dependency isn't installed.")
    return {
        "sha256": digest,
        "requirements_sha256": "sha256:" + hashlib.sha256(requirement_bytes).hexdigest(),
        "installed_versions": versions,
        "bootstrap_versions": bootstrap,
    }


def installed_command() -> str:
    """Locate the console command installed by this interpreter's wheel installer."""
    suffix = ".exe" if os.name == "nt" else ""
    command = Path(sysconfig.get_path("scripts")) / ("policynim" + suffix)
    require(command.is_file(), "Installed policynim console command is missing.")
    return str(command)


def safe_diagnostic(text: str, env: dict[str, str]) -> str:
    """Bound diagnostics and remove credentials before writing release evidence."""
    for key, value in env.items():
        if value and any(
            marker in key.upper() for marker in ("TOKEN", "SECRET", "KEY", "PASSWORD")
        ):
            text = text.replace(value, "<redacted>")
    return text[-4000:]


def run_command(
    arguments: list[str], *, env: dict[str, str], cwd: Path, command: str | None = None
) -> str:
    """Exercise the user's installed command and retain sanitized failure details."""
    completed = subprocess.run(
        [command or installed_command(), *arguments],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )
    require(
        completed.returncode == 0,
        f"Installed CLI check failed: {arguments[0]} (exit {completed.returncode}). "
        + safe_diagnostic((completed.stderr or "") + "\n" + (completed.stdout or ""), env),
    )
    return completed.stdout


def run_cli(
    arguments: list[str], *, env: dict[str, str], cwd: Path, command: str | None = None
) -> dict:
    """Parse the actual installed console command's structured result."""
    return json.loads(run_command(arguments, env=env, cwd=cwd, command=command))


async def check_client(
    client: Client, mode: str, *, authenticated: bool = False
) -> dict[str, object]:
    """Discover real installed tools and check the negotiated protocol."""
    expected = "2025-11-25" if mode == "legacy" else "2026-07-28"
    require(client.protocol_version == expected, "Unexpected negotiated MCP protocol.")
    listing = await client.list_tools()
    names = sorted(tool.name for tool in listing.tools)
    expected_tools = [
        "policy_compile",
        "policy_preflight",
        "policy_preflight_evidence",
        "policy_search",
    ]
    if authenticated:
        expected_tools += ["eval_submit", "eval_inspect", "eval_cancel", "eval_result"]
    require(names == sorted(expected_tools), "Installed MCP tools differ.")
    require(
        all(tool.input_schema and tool.output_schema for tool in listing.tools),
        "Missing advertised tool schemas.",
    )
    rejected = await client.call_tool("policy_search", {"query": "release smoke", "top_k": 0})
    require(rejected.is_error is True, "Installed MCP accepted an invalid tool argument.")
    return {"protocol": expected, "tools": names, "invalid_argument_rejected": True}


async def check_stdio(
    env: dict[str, str], cwd: Path, *, command: str | None = None
) -> list[dict[str, object]]:
    """Launch installed stdio children for both protocol generations."""
    results = []
    for mode in ("auto", "legacy"):
        parameters = StdioServerParameters(
            command=command or installed_command(),
            args=["mcp", "--transport", "stdio"],
            cwd=cwd,
            env=env,
        )
        async with asyncio.timeout(45):
            async with Client(parameters, mode=mode, read_timeout_seconds=20) as client:
                results.append(await check_client(client, mode))
    return results


async def check_http(origin: str, token: str) -> list[dict[str, object]]:
    """Negotiate both MCP protocols through the installed HTTP/auth boundary."""
    results = []
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}, timeout=20, trust_env=False
    ) as http_client:
        for mode in ("auto", "legacy"):
            async with asyncio.timeout(45):
                transport = streamable_http_client(origin + "/mcp", http_client=http_client)
                async with Client(transport, mode=mode, read_timeout_seconds=20) as client:
                    results.append(await check_client(client, mode, authenticated=True))
    return results


def verify(args: argparse.Namespace) -> dict[str, object]:
    """Keep all runtime state private to a disposable directory and leave credentials unused."""
    report = {
        "status": "working",
        "source_revision": args.revision,
        "python": platform.python_version(),
        "platform": platform.system(),
        "wheel": check_wheel(args.wheel),
        "dependency_lock": check_dependencies(args.lockfile, args.lock_digest, args.requirements),
        "sdk_version": importlib.metadata.version("mcp"),
        "provider_calls": "not performed",
        "coding_client_ui": "not tested",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("POLICYNIM_") and key not in {"NVIDIA_API_KEY", "PYTHONPATH", "PORT"}
    }
    with tempfile.TemporaryDirectory(prefix="policynim-install-") as temporary:
        cwd = Path(temporary)
        config = cwd / "empty.env"
        config.write_text("")
        env.update(
            {
                "POLICYNIM_CONFIG_FILE": str(config),
                "POLICYNIM_GENERATIONS_DIR": str(cwd / "generations"),
                "POLICYNIM_EVAL_WORKSPACE_DIR": str(cwd / "evaluations"),
                "NVIDIA_API_KEY": "",
                "APPDATA": str(cwd / "config"),
                "LOCALAPPDATA": str(cwd / "local"),
                "XDG_CONFIG_HOME": str(cwd / "config"),
                "XDG_DATA_HOME": str(cwd / "local"),
                "PYTHONUTF8": "1",
            }
        )
        require("Usage:" in run_command(["--help"], env=env, cwd=cwd), "Missing CLI help.")
        require(
            importlib.metadata.version("policynim") in run_command(["--version"], env=env, cwd=cwd),
            "Console version differs.",
        )
        source = run_cli(["policies", "validate", "--source", "--format", "json"], env=env, cwd=cwd)
        require(source["effective_count"] > 0, "Packaged source catalog is empty.")
        imported = run_cli(
            [
                "generation",
                "import",
                str(args.bundle),
                "--expect-digest",
                args.bundle_digest,
                "--require-source-match",
                "--format",
                "json",
            ],
            env=env,
            cwd=cwd,
        )
        inspected = run_cli(["generation", "inspect", "--format", "json"], env=env, cwd=cwd)
        require(
            imported["generation_id"] == inspected["generation_id"], "Generation identity drift."
        )
        require(
            inspected["catalog"]["digest"] == source["catalog_digest"],
            "Packaged sources and served catalog differ.",
        )
        report["generation_id"] = inspected["generation_id"]
        report["catalog_digest"] = source["catalog_digest"]
        report["policy_count"] = source["effective_count"]
        report["chunk_count"] = inspected["manifest"]["chunk_count"]
        evaluated = run_cli(["eval", "--headless", "--no-compare-rerank"], env=env, cwd=cwd)
        report["synthetic_offline_cases"] = evaluated["runs"][0]["metrics"]
        report["stdio"] = asyncio.run(check_stdio(env, cwd))

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        token = secrets.token_urlsafe(32)
        env.update(
            {
                "PORT": str(port),
                "POLICYNIM_MCP_HOST": "127.0.0.1",
                "POLICYNIM_MCP_REQUIRE_AUTH": "true",
                "POLICYNIM_MCP_BEARER_TOKENS": token,
                "POLICYNIM_MCP_PUBLIC_BASE_URL": "https://package-check.invalid",
            }
        )
        origin = f"http://127.0.0.1:{port}"
        with (cwd / "server.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    installed_command(),
                    "mcp",
                    "--transport",
                    "streamable-http",
                ],
                cwd=cwd,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                deadline = time.monotonic() + 45
                with httpx2.Client(timeout=2, trust_env=False) as client:
                    while True:
                        require(
                            process.poll() is None, "Installed HTTP server exited during startup."
                        )
                        try:
                            health = client.get(origin + "/healthz")
                        except httpx2.TransportError:
                            health = None
                        if health is not None and health.status_code == 200:
                            break
                        require(time.monotonic() < deadline, "Installed HTTP startup timed out.")
                        time.sleep(0.1)
                    require(
                        health.json()["generation_id"] == report["generation_id"],
                        "HTTP generation drift.",
                    )
                    require(
                        client.post(origin + "/mcp", json={}).status_code == 401,
                        "Missing-token request accepted.",
                    )
                report["http"] = asyncio.run(check_http(origin, token))
            except Exception as error:
                log.flush()
                diagnostic = safe_diagnostic(
                    (cwd / "server.log").read_text(encoding="utf-8", errors="replace"), env
                )
                raise RuntimeError(f"{error}\nHTTP server diagnostics:\n{diagnostic}") from error
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    report["status"] = "passed"
    return report


def main() -> None:
    """Persist a reviewable result with explicit failure instead of silently skipping checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--lockfile", type=Path, required=True)
    parser.add_argument("--lock-digest", required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bundle-digest", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.wheel, args.bundle = args.wheel.resolve(), args.bundle.resolve()
    try:
        report = verify(args)
    except Exception as error:
        report = {"status": "failed", "error_type": type(error).__name__, "message": str(error)}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
