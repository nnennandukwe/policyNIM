"""Verify a built standalone command, generation import, and MCP transports offline."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import secrets
import socket
import stat
import subprocess
import tempfile
from pathlib import Path

import httpx2
import verify_installed_package as checks


def artifact_identity(command: Path) -> dict[str, object]:
    """Bind the complete standalone tree, including relative symlink targets."""
    root = command.parent
    digest = hashlib.sha256()
    count = 0
    digest.update(
        (
            json.dumps(["directory", ".", stat.S_IMODE(root.stat().st_mode)], separators=(",", ":"))
            + "\n"
        ).encode("ascii")
    )
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            checks.require(
                path.resolve().is_relative_to(root), "Artifact symlink escapes its tree."
            )
            record = ["link", relative, os.readlink(path)]
        elif path.is_file():
            with path.open("rb") as content:
                file_digest = hashlib.file_digest(content, "sha256").hexdigest()
            record = ["file", relative, file_digest, stat.S_IMODE(path.stat().st_mode)]
            count += 1
        elif path.is_dir():
            record = ["directory", relative, stat.S_IMODE(path.stat().st_mode)]
        else:
            continue
        digest.update(
            json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        )
    checks.require(count > 0, "Standalone artifact tree is empty.")
    return {
        "tree_sha256": digest.hexdigest(),
        "file_count": count,
        "digest_format": "json-records-v3-file-directory-modes",
    }


def verify(args: argparse.Namespace) -> dict[str, object]:
    """Run the real frozen command outside its checkout without provider credentials."""
    command = args.command.resolve()
    checks.require(command.is_file(), "Standalone command is missing.")
    lock_digest = "sha256:" + hashlib.sha256(args.lockfile.read_bytes()).hexdigest()
    checks.require(lock_digest == args.lock_digest, "Dependency lock digest differs.")
    report: dict[str, object] = {
        "source_revision": args.revision,
        "public_bundle_digest": args.bundle_digest,
        "client_python": platform.python_version(),
        "client_sdk_version": importlib.metadata.version("mcp"),
        "platform": platform.system(),
        "artifact": artifact_identity(command),
        "dependency_lock": lock_digest,
        "provider_calls": "not performed",
        "coding_client_ui": "not tested",
        "authentication_scope": "beta_operator_key",
        "external_oauth_e2e": "not_run",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("POLICYNIM_") and key not in {"NVIDIA_API_KEY", "PYTHONPATH", "PORT"}
    }
    with tempfile.TemporaryDirectory(prefix="policynim-standalone-") as temporary:
        cwd = Path(temporary)
        configuration = cwd / "empty.env"
        configuration.write_text("", encoding="utf-8")
        child_home = cwd / "home"
        child_home.mkdir()
        env.update(
            {
                "HOME": str(child_home),
                "USERPROFILE": str(child_home),
                "POLICYNIM_CONFIG_FILE": str(configuration),
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
        command_text = str(command)
        checks.require(
            "Usage:" in checks.run_command(["--help"], env=env, cwd=cwd, command=command_text),
            "Missing standalone CLI help.",
        )
        report["version_output"] = checks.run_command(
            ["--version"], env=env, cwd=cwd, command=command_text
        ).strip()
        checks.require(
            report["version_output"] == args.expected_version, "Standalone version differs."
        )
        report["expected_version"] = args.expected_version
        source = checks.run_cli(
            ["policies", "validate", "--source", "--format", "json"],
            env=env,
            cwd=cwd,
            command=command_text,
        )
        imported = checks.run_cli(
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
            command=command_text,
        )
        inspected = checks.run_cli(
            ["generation", "inspect", "--format", "json"],
            env=env,
            cwd=cwd,
            command=command_text,
        )
        checks.require(
            imported["generation_id"] == inspected["generation_id"], "Generation identity drift."
        )
        checks.require(
            source["catalog_digest"] == inspected["catalog"]["digest"], "Packaged catalog drift."
        )
        report.update(
            generation_id=inspected["generation_id"],
            catalog_digest=source["catalog_digest"],
            policy_count=source["effective_count"],
            chunk_count=inspected["manifest"]["chunk_count"],
        )
        evaluation = checks.run_cli(
            ["eval", "--headless", "--no-compare-rerank"],
            env=env,
            cwd=cwd,
            command=command_text,
        )
        metrics = evaluation["runs"][0]["metrics"]
        checks.require(
            metrics["case_count"] == 6, "Standalone offline fixture requires exactly 6 cases."
        )
        checks.require(metrics["overall_pass_rate"] == 1.0, "Standalone offline fixture failed.")
        report["synthetic_offline_cases"] = metrics
        report["stdio"] = asyncio.run(checks.check_stdio(env, cwd, command=command_text))
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
                "POLICYNIM_MCP_PUBLIC_BASE_URL": "https://standalone-check.invalid",
            }
        )
        origin = f"http://127.0.0.1:{port}"
        with (cwd / "server.log").open("wb") as log:
            process = subprocess.Popen(
                [command_text, "mcp", "--transport", "streamable-http"],
                cwd=cwd,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                with httpx2.Client(timeout=2, trust_env=False) as client:
                    health = checks.wait_for_health(client, process, origin, env)
                    checks.require(
                        health.json()["generation_id"] == report["generation_id"],
                        "Standalone HTTP generation drift.",
                    )
                    checks.require(
                        client.post(origin + "/mcp", json={}).status_code == 401,
                        "Standalone HTTP accepted a missing token.",
                    )
                report["http"] = asyncio.run(checks.check_http(origin, token))
            except Exception as error:
                log.flush()
                diagnostic = checks.read_diagnostic(cwd / "server.log", env)
                raise RuntimeError(
                    f"{error}\nStandalone server diagnostics:\n{diagnostic}"
                ) from error
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
    """Write a version-bound receipt and return nonzero on incomplete verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bundle-digest", required=True)
    parser.add_argument("--lockfile", type=Path, required=True)
    parser.add_argument("--lock-digest", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.command, args.bundle = args.command.resolve(), args.bundle.resolve()
    args.lockfile, args.output = args.lockfile.resolve(), args.output.resolve()
    try:
        report = verify(args)
    except Exception as error:
        report = {
            "status": "failed",
            "source_revision": args.revision,
            "platform": platform.system(),
            "runner_machine": platform.machine(),
            "expected_version": args.expected_version,
            "public_bundle_digest": args.bundle_digest,
            "expected_dependency_lock": args.lock_digest,
            "context_scope": "requested verification identity; execution didn't complete",
            "error_type": type(error).__name__,
            "message": str(error),
        }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
