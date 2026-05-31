"""Workflow contract checks for offline CI and opt-in live smoke gates."""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HOSTED_SMOKE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "hosted-smoke.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _read_text(path: Path) -> str:
    """Read a workflow file as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def test_ci_pytest_gate_excludes_all_live_markers() -> None:
    """Ensure normal CI cannot discover live or Docker-only tests by accident."""
    text = _read_text(CI_WORKFLOW)

    assert "uv run ruff check ." in text
    assert "uv run pyright" in text
    assert 'uv run pytest -q -m "not live and not docker_live"' in text
    assert "uv run ruff check\n" not in text
    assert 'uv run pytest -q -m "not live"\n' not in text


def test_ci_builds_and_smokes_python_distribution_without_live_credentials() -> None:
    """Catch packaging regressions before release tags are cut."""
    text = _read_text(CI_WORKFLOW)

    for token in (
        "package:",
        'NVIDIA_API_KEY: ""',
        "uv lock --check",
        "python3 scripts/oss_readiness_check.py --format json >/tmp/policynim-oss-readiness.json",
        "python3 -m json.tool /tmp/policynim-oss-readiness.json",
        (
            "python3 scripts/oss_readiness_check.py --format launch-issue "
            ">/tmp/policynim-launch-issue.md"
        ),
        'grep -F "## Missing Evidence Collection Commands" /tmp/policynim-launch-issue.md',
        "uv build --out-dir dist",
        "python -m venv /tmp/policynim-wheel-smoke",
        "mkdir -p /tmp/policynim-wheel-cwd",
        "/tmp/policynim-wheel-smoke/bin/python -m pip install --upgrade pip",
        "/tmp/policynim-wheel-smoke/bin/python -m pip install dist/*.whl",
        "cd /tmp/policynim-wheel-cwd",
        "/tmp/policynim-wheel-smoke/bin/policynim --help",
        "/tmp/policynim-wheel-smoke/bin/policynim init --help >/tmp/policynim-init-help.txt",
        'grep -F "Usage: policynim init" /tmp/policynim-init-help.txt',
        ("/tmp/policynim-wheel-smoke/bin/policynim ingest --help >/tmp/policynim-ingest-help.txt"),
        'grep -F "Usage: policynim ingest" /tmp/policynim-ingest-help.txt',
        (
            "/tmp/policynim-wheel-smoke/bin/policynim preflight --help "
            ">/tmp/policynim-preflight-help.txt"
        ),
        'grep -F "Usage: policynim preflight" /tmp/policynim-preflight-help.txt',
        "/tmp/policynim-wheel-smoke/bin/policynim quickstart --format json",
        ("/tmp/policynim-wheel-smoke/bin/policynim quickstart --target local-cli --format json"),
        ("/tmp/policynim-wheel-smoke/bin/policynim quickstart --target local-mcp --format json"),
        "/tmp/policynim-wheel-smoke/bin/policynim doctor --format json >/tmp/policynim-doctor.json",
        (
            "/tmp/policynim-wheel-smoke/bin/policynim support-bundle "
            ">/tmp/policynim-support-bundle.json"
        ),
        "/tmp/policynim-wheel-smoke/bin/policynim mcp-smoke --format json",
        (
            "/tmp/policynim-wheel-smoke/bin/policynim mcp-config --client codex "
            "--target local-stdio --format json"
        ),
        (
            "/tmp/policynim-wheel-smoke/bin/policynim mcp-config --client claude-code "
            "--target local-stdio --format json"
        ),
        (
            "/tmp/policynim-wheel-smoke/bin/policynim mcp-smoke --mcp-config-file "
            "/tmp/policynim-mcp-config.json --format json"
        ),
        (
            "/tmp/policynim-wheel-smoke/bin/policynim mcp-smoke --mcp-config-file "
            "/tmp/policynim-claude-mcp-config.json --format json"
        ),
        (
            "/tmp/policynim-wheel-smoke/bin/policynim mcp-config --target hosted-http "
            "--client codex --hosted-url https://example.invalid/mcp "
            "--bearer-token-env-var POLICYNIM_TOKEN --format json"
        ),
        (
            "/tmp/policynim-wheel-smoke/bin/policynim mcp-config --target hosted-http "
            "--client claude-code --hosted-url https://example.invalid/mcp "
            "--bearer-token-env-var POLICYNIM_TOKEN --format json"
        ),
        "python -m json.tool /tmp/policynim-quickstart.json",
        "python -m json.tool /tmp/policynim-quickstart-local-cli.json",
        "python -m json.tool /tmp/policynim-quickstart-local-mcp.json",
        "python -m json.tool /tmp/policynim-doctor.json",
        "python -m json.tool /tmp/policynim-support-bundle.json",
        "python -m json.tool /tmp/policynim-mcp-smoke.json",
        "python -m json.tool /tmp/policynim-mcp-config.json",
        "python -m json.tool /tmp/policynim-claude-mcp-config.json",
        "python -m json.tool /tmp/policynim-mcp-smoke-from-codex-config.json",
        "python -m json.tool /tmp/policynim-mcp-smoke-from-claude-config.json",
        "python -m json.tool /tmp/policynim-hosted-mcp-config.json",
        "python -m json.tool /tmp/policynim-claude-hosted-mcp-config.json",
        "/tmp/policynim-wheel-smoke/bin/policynim --version",
    ):
        assert token in text


def test_ci_uploads_reviewable_package_smoke_evidence() -> None:
    """Keep clean-install smoke output available to PR reviewers."""
    text = _read_text(CI_WORKFLOW)

    for token in (
        "Upload package smoke evidence",
        "uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4",
        "name: package-smoke-evidence",
        "/tmp/policynim-oss-readiness.json",
        "/tmp/policynim-launch-issue.md",
        "/tmp/policynim-init-help.txt",
        "/tmp/policynim-ingest-help.txt",
        "/tmp/policynim-preflight-help.txt",
        "/tmp/policynim-quickstart.json",
        "/tmp/policynim-quickstart-local-cli.json",
        "/tmp/policynim-quickstart-local-mcp.json",
        "/tmp/policynim-doctor.json",
        "/tmp/policynim-support-bundle.json",
        "/tmp/policynim-mcp-smoke.json",
        "/tmp/policynim-mcp-config.json",
        "/tmp/policynim-claude-mcp-config.json",
        "/tmp/policynim-mcp-smoke-from-codex-config.json",
        "/tmp/policynim-mcp-smoke-from-claude-config.json",
        "/tmp/policynim-hosted-mcp-config.json",
        "/tmp/policynim-claude-hosted-mcp-config.json",
        "if-no-files-found: error",
        "retention-days: 14",
    ):
        assert token in text


def test_hosted_smoke_workflow_is_manual_and_secret_gated() -> None:
    """Ensure the hosted smoke workflow is opt-in and requires deployed beta secrets."""
    text = _read_text(HOSTED_SMOKE_WORKFLOW)

    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "POLICYNIM_BETA_MCP_URL: ${{ secrets.POLICYNIM_BETA_MCP_URL }}" in text
    assert "POLICYNIM_BETA_MCP_TOKEN: ${{ secrets.POLICYNIM_BETA_MCP_TOKEN }}" in text
    assert "Validate hosted smoke secrets" in text
    assert "pytest -q -m live tests/test_hosted_mcp_live.py" in text
    assert "permissions:\n  contents: read" in text


def test_hosted_smoke_workflow_uses_pinned_actions() -> None:
    """Ensure the hosted smoke workflow keeps GitHub actions pinned to SHAs."""
    text = _read_text(HOSTED_SMOKE_WORKFLOW)
    action_refs = re.findall(r"uses: [^@\n]+@([0-9a-f]{40})", text)

    assert len(action_refs) == 4


def test_hosted_smoke_workflow_uploads_reviewable_live_evidence() -> None:
    """Keep manual hosted smoke proof reviewable without exposing beta secrets."""
    text = _read_text(HOSTED_SMOKE_WORKFLOW)

    for token in (
        "mkdir -p hosted-smoke-evidence",
        "pytest -q -m live tests/test_hosted_mcp_live.py",
        "--junitxml hosted-smoke-evidence/policynim-hosted-smoke-junit.xml",
        "Upload hosted smoke evidence",
        "if: always()",
        "uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4",
        "name: hosted-smoke-evidence",
        "hosted-smoke-evidence/policynim-hosted-smoke-junit.xml",
        "if-no-files-found: warn",
        "retention-days: 30",
    ):
        assert token in text

    assert "POLICYNIM_BETA_MCP_TOKEN" not in text.split("Upload hosted smoke evidence", 1)[1]


def test_release_workflow_runs_offline_verification_before_release_artifacts() -> None:
    """Ensure release automation builds from the same offline quality gate as CI."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "workflow_dispatch:",
        "tags:",
        '- "v*.*.*"',
        "uv lock --check",
        "uv run ruff check .",
        "uv run pyright",
        'uv run pytest -q -m "not live and not docker_live"',
        "python3 scripts/oss_readiness_check.py --format json >/tmp/policynim-oss-readiness.json",
        "python3 -m json.tool /tmp/policynim-oss-readiness.json",
        "uv build --out-dir dist",
        "wheel-smoke:",
        "mkdir -p /tmp/policynim-wheel-cwd",
        "pip install --upgrade pip",
        "cd /tmp/policynim-wheel-cwd",
        "policynim init --help >/tmp/policynim-init-help.txt",
        'grep -F "Usage: policynim init" /tmp/policynim-init-help.txt',
        "policynim ingest --help >/tmp/policynim-ingest-help.txt",
        'grep -F "Usage: policynim ingest" /tmp/policynim-ingest-help.txt',
        "policynim preflight --help >/tmp/policynim-preflight-help.txt",
        'grep -F "Usage: policynim preflight" /tmp/policynim-preflight-help.txt',
        "policynim quickstart --format json",
        "policynim quickstart --target local-cli --format json",
        "policynim quickstart --target local-mcp --format json",
        "policynim doctor --format json",
        "policynim support-bundle",
        "policynim mcp-smoke --format json",
        ("policynim mcp-config --client codex --target local-stdio --format json"),
        ("policynim mcp-config --client claude-code --target local-stdio --format json"),
        "policynim mcp-smoke --mcp-config-file /tmp/policynim-mcp-config.json --format json",
        (
            "policynim mcp-smoke --mcp-config-file "
            "/tmp/policynim-claude-mcp-config.json --format json"
        ),
        (
            "policynim mcp-config --target hosted-http --client codex "
            "--hosted-url https://example.invalid/mcp --bearer-token-env-var "
            "POLICYNIM_TOKEN --format json"
        ),
        (
            "policynim mcp-config --target hosted-http --client claude-code "
            "--hosted-url https://example.invalid/mcp --bearer-token-env-var "
            "POLICYNIM_TOKEN --format json"
        ),
        "python -m json.tool /tmp/policynim-quickstart.json",
        "python -m json.tool /tmp/policynim-quickstart-local-cli.json",
        "python -m json.tool /tmp/policynim-quickstart-local-mcp.json",
        "python -m json.tool /tmp/policynim-mcp-smoke.json",
        "python -m json.tool /tmp/policynim-mcp-config.json",
        "python -m json.tool /tmp/policynim-claude-mcp-config.json",
        "python -m json.tool /tmp/policynim-hosted-mcp-config.json",
        "python -m json.tool /tmp/policynim-claude-hosted-mcp-config.json",
        "standalone-build:",
        "publish-github-release:",
    ):
        assert token in text
    assert "tests/test_hosted_mcp_live.py" not in text
    assert "POLICYNIM_BETA_MCP_TOKEN" not in text


def test_release_workflow_embedded_python_blocks_parse() -> None:
    """Catch syntax regressions in heredoc Python blocks before Actions runs."""
    text = _read_text(RELEASE_WORKFLOW)
    blocks = re.findall(r"python3? - <<'PY'(?: >> \"\$GITHUB_ENV\")?\n(.*?)\n\s+PY", text, re.S)

    assert blocks
    for block in blocks:
        ast.parse(textwrap.dedent(block))


def test_release_workflow_uses_pinned_actions_and_trusted_pypi_publish() -> None:
    """Ensure release actions are pinned and PyPI publish uses OIDC, not secrets."""
    text = _read_text(RELEASE_WORKFLOW)
    uses_lines = re.findall(r"uses: [^\n]+", text)

    assert uses_lines
    for line in uses_lines:
        assert re.search(r"@[0-9a-f]{40}(?:\s+#.*)?$", line), line
    assert "id-token: write" in text
    assert "pypa/gh-action-pypi-publish@6733eb7d741f0b11ec6a39b58540dab7590f9b7d # v1.14.0" in text
    assert "password:" not in text
    assert "TWINE_PASSWORD" not in text


def test_release_workflow_publishes_pypi_after_release_assets() -> None:
    """Do not let immutable PyPI files publish before release assets pass."""
    text = _read_text(RELEASE_WORKFLOW)

    publish_pypi_job = re.search(
        r"publish-pypi:\n(?P<body>.*?)(?=\n  [a-zA-Z0-9_-]+:|\Z)",
        text,
        re.S,
    )
    assert publish_pypi_job is not None
    body = publish_pypi_job.group("body")

    assert "needs:\n      - publish-github-release" in body
    assert "pypa/gh-action-pypi-publish@" in body
    assert text.index("publish-github-release:") < text.index("publish-pypi:")


def test_release_workflow_public_launch_input_requires_pypi_publish() -> None:
    """Prevent manual GitHub-only release runs from looking like public launches."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "public_launch:",
        "Require public-launch proof mode; fails unless publish_pypi is enabled.",
        "Validate public launch inputs",
        "PUBLIC_LAUNCH: ${{ inputs.public_launch }}",
        "PUBLISH_PYPI: ${{ inputs.publish_pypi }}",
        'if [ "$PUBLIC_LAUNCH" = "true" ] && [ "$PUBLISH_PYPI" != "true" ]; then',
        "public_launch=true requires publish_pypi=true",
        "Record release mode",
        "## PolicyNIM release mode",
        "GitHub-only release candidate",
        "Public launch candidate",
    ):
        assert token in text


def test_release_workflow_attests_release_assets_from_checksums() -> None:
    """Generate provenance for the same public assets covered by SHA256SUMS."""
    text = _read_text(RELEASE_WORKFLOW)

    publish_job = re.search(
        r"publish-github-release:\n(?P<body>.*?)(?=\n  publish-pypi:|\Z)",
        text,
        flags=re.S,
    )
    assert publish_job is not None
    body = publish_job.group("body")

    for token in (
        "contents: write",
        "id-token: write",
        "attestations: write",
        "Generate release asset attestations",
        "uses: actions/attest@59d89421af93a897026c735860bf21b6eb4f7b26 # v4.1.0",
        "subject-checksums: release-assets/SHA256SUMS",
        "show-summary: true",
        "Verify release asset attestation",
        "mkdir -p attestation-evidence",
        'ASSET_PATH="release-assets/install.sh"',
        'OUTPUT_PATH="attestation-evidence/install-sh-attestation.json"',
        "gh attestation verify",
        '--repo "$GITHUB_REPOSITORY"',
        "--format json",
        'python3 -m json.tool "$OUTPUT_PATH"',
        "Upload release attestation evidence",
        "name: release-attestation-evidence",
        "path: attestation-evidence/install-sh-attestation.json",
    ):
        assert token in body

    assert body.index("python3 scripts/release_manifest.py release-assets") < body.index(
        "Generate release asset attestations"
    )
    assert body.index("Generate release asset attestations") < body.index(
        "Verify release asset attestation"
    )
    assert body.index("Verify release asset attestation") < body.index(
        "Upload release attestation evidence"
    )
    assert body.index("Upload release attestation evidence") < body.index(
        "Create draft GitHub release"
    )


def test_release_workflow_uploads_reviewable_smoke_evidence() -> None:
    """Keep release-candidate smoke output reviewable without publishing it."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "Upload release wheel smoke evidence",
        "name: release-wheel-smoke-evidence",
        "/tmp/policynim-init-help.txt",
        "/tmp/policynim-ingest-help.txt",
        "/tmp/policynim-preflight-help.txt",
        "/tmp/policynim-quickstart.json",
        "/tmp/policynim-quickstart-local-cli.json",
        "/tmp/policynim-quickstart-local-mcp.json",
        "/tmp/policynim-doctor.json",
        "/tmp/policynim-support-bundle.json",
        "/tmp/policynim-mcp-smoke.json",
        "/tmp/policynim-mcp-config.json",
        "/tmp/policynim-claude-mcp-config.json",
        "/tmp/policynim-hosted-mcp-config.json",
        "/tmp/policynim-claude-hosted-mcp-config.json",
        "Upload standalone smoke evidence",
        "name: standalone-smoke-evidence-${{ matrix.platform }}",
        'SMOKE_EVIDENCE="$PWD/smoke-evidence"',
        'mkdir -p "$SMOKE_EVIDENCE" "$STANDALONE_SMOKE_CWD"',
        "smoke-evidence/policynim-standalone-init-help.txt",
        "smoke-evidence/policynim-standalone-ingest-help.txt",
        "smoke-evidence/policynim-standalone-preflight-help.txt",
        "smoke-evidence/policynim-standalone-quickstart.json",
        "smoke-evidence/policynim-standalone-quickstart-local-cli.json",
        "smoke-evidence/policynim-standalone-quickstart-local-mcp.json",
        "smoke-evidence/policynim-standalone-doctor.json",
        "smoke-evidence/policynim-standalone-support-bundle.json",
        "smoke-evidence/policynim-standalone-mcp-config.json",
        "smoke-evidence/policynim-standalone-claude-mcp-config.json",
        "smoke-evidence/policynim-standalone-hosted-mcp-config.json",
        "smoke-evidence/policynim-standalone-claude-hosted-mcp-config.json",
        "if-no-files-found: error",
        "retention-days: 30",
    ):
        assert token in text


def test_release_workflow_publish_job_downloads_only_public_payload_artifacts() -> None:
    """Internal evidence artifacts must not be copied into draft release assets."""
    text = _read_text(RELEASE_WORKFLOW)
    publish_job = re.search(
        r"publish-github-release:\n(?P<body>.*?)(?=\n  publish-pypi:|\Z)",
        text,
        flags=re.S,
    )
    assert publish_job is not None
    body = publish_job.group("body")

    for token in (
        "Download Python distribution for release",
        "name: python-dist",
        "path: release-downloads/python-dist",
        "Download Linux standalone bundle for release",
        "name: standalone-linux-amd64",
        "path: release-downloads/standalone-linux-amd64",
        "Download Apple Silicon macOS standalone bundle for release",
        "name: standalone-darwin-arm64",
        "path: release-downloads/standalone-darwin-arm64",
        "Download Intel macOS standalone bundle for release",
        "name: standalone-darwin-amd64",
        "path: release-downloads/standalone-darwin-amd64",
        "Download Windows standalone bundle for release",
        "name: standalone-windows-amd64",
        "path: release-downloads/standalone-windows-amd64",
    ):
        assert token in body

    assert "Download release artifacts" not in body
    assert "release-wheel-smoke-evidence" not in body
    assert "standalone-smoke-evidence" not in body
    assert "Download release attestation evidence" not in body
    assert "find release-downloads -type f -exec cp {} release-assets/ \\;" in body


def test_release_workflow_uploads_expected_install_artifacts() -> None:
    """Lock the public artifact contract used by curl and PowerShell installers."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "policynim-${RELEASE_TAG}-linux-amd64.tar.gz",
        "policynim-${RELEASE_TAG}-darwin-arm64.tar.gz",
        "policynim-${RELEASE_TAG}-darwin-amd64.tar.gz",
        "policynim-${RELEASE_TAG}-windows-amd64.zip",
        "scripts/install.sh",
        "scripts/install.ps1",
        "SHA256SUMS",
        "RELEASE_MANIFEST.json",
        "scripts/release_manifest.py",
        "gh release create",
        "--draft",
    ):
        assert token in text


def test_release_workflow_builds_macos_intel_standalone_artifact() -> None:
    """Restore macOS Intel as a first-class standalone release target."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "os: macos-15-intel",
        "platform: darwin-amd64",
        'darwin-amd64) ASSET_NAME="policynim-${RELEASE_TAG}-darwin-amd64.tar.gz" ;;',
    ):
        assert token in text


def test_release_workflow_generates_notes_from_checked_changelog() -> None:
    """Keep GitHub release notes tied to the versioned changelog section."""
    text = _read_text(RELEASE_WORKFLOW)

    assert "scripts/check_release_notes.py --format json" in text
    assert (
        "python3 scripts/check_release_notes.py --write-github-release-notes release-notes.md"
    ) in text
    assert "--notes-file release-notes.md" in text
    assert 'grep -F "artifact attestations" release-notes.md' in text
    assert "cat > release-notes.md <<'EOF'" not in text


def test_release_workflow_smokes_standalone_first_run_diagnostics() -> None:
    """Keep standalone release bundles aligned with installed first-run diagnostics."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        'POLICYNIM_STANDALONE="$PWD/dist/policynim/policynim.exe"',
        'POLICYNIM_STANDALONE="$PWD/dist/policynim/policynim"',
        '"$POLICYNIM_STANDALONE" doctor --format json',
        '"$POLICYNIM_STANDALONE" init --help',
        '"$POLICYNIM_STANDALONE" ingest --help',
        '"$POLICYNIM_STANDALONE" preflight --help',
        '"$POLICYNIM_STANDALONE" quickstart --format json',
        '"$POLICYNIM_STANDALONE" quickstart --target local-cli --format json',
        '"$POLICYNIM_STANDALONE" quickstart --target local-mcp --format json',
        '"$POLICYNIM_STANDALONE" support-bundle',
        '"$POLICYNIM_STANDALONE" mcp-smoke --format json',
        '"$POLICYNIM_STANDALONE" mcp-config --help',
        '"$POLICYNIM_STANDALONE" mcp-config --client codex --target local-stdio --format json',
        (
            '"$POLICYNIM_STANDALONE" mcp-config --client claude-code --target '
            "local-stdio --format json"
        ),
        (
            '"$POLICYNIM_STANDALONE" mcp-smoke --mcp-config-file '
            '"$SMOKE_EVIDENCE/policynim-standalone-mcp-config.json" --format json'
        ),
        (
            '"$POLICYNIM_STANDALONE" mcp-smoke --mcp-config-file '
            '"$SMOKE_EVIDENCE/policynim-standalone-claude-mcp-config.json" --format json'
        ),
        (
            '"$POLICYNIM_STANDALONE" mcp-config --target hosted-http --client codex '
            "--hosted-url https://example.invalid/mcp --bearer-token-env-var "
            "POLICYNIM_TOKEN --format json"
        ),
        (
            '"$POLICYNIM_STANDALONE" mcp-config --target hosted-http --client '
            "claude-code --hosted-url https://example.invalid/mcp "
            "--bearer-token-env-var POLICYNIM_TOKEN --format json"
        ),
        'python -m json.tool "$SMOKE_EVIDENCE/policynim-standalone-quickstart.json"',
        ('python -m json.tool "$SMOKE_EVIDENCE/policynim-standalone-quickstart-local-cli.json"'),
        ('python -m json.tool "$SMOKE_EVIDENCE/policynim-standalone-quickstart-local-mcp.json"'),
        'python -m json.tool "$SMOKE_EVIDENCE/policynim-standalone-mcp-smoke.json"',
        'python -m json.tool "$SMOKE_EVIDENCE/policynim-standalone-mcp-config.json"',
        ('python -m json.tool "$SMOKE_EVIDENCE/policynim-standalone-claude-mcp-config.json"'),
        (
            'python -m json.tool "$SMOKE_EVIDENCE/'
            'policynim-standalone-mcp-smoke-from-codex-config.json"'
        ),
        (
            'python -m json.tool "$SMOKE_EVIDENCE/'
            'policynim-standalone-mcp-smoke-from-claude-config.json"'
        ),
        'grep -F "Usage: policynim init" "$SMOKE_EVIDENCE/policynim-standalone-init-help.txt"',
        (
            'grep -F "Usage: policynim ingest" '
            '"$SMOKE_EVIDENCE/policynim-standalone-ingest-help.txt"'
        ),
        (
            'grep -F "Usage: policynim preflight" '
            '"$SMOKE_EVIDENCE/policynim-standalone-preflight-help.txt"'
        ),
        '"$POLICYNIM_STANDALONE" --version',
        "smoke-evidence/policynim-standalone-mcp-smoke.json",
        "smoke-evidence/policynim-standalone-mcp-smoke-from-codex-config.json",
        "smoke-evidence/policynim-standalone-mcp-smoke-from-claude-config.json",
    ):
        assert token in text


def test_release_workflow_builds_four_standalone_platforms() -> None:
    """Restore macOS Intel as a first-class standalone release target."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "platform: linux-amd64",
        "platform: darwin-arm64",
        "platform: darwin-amd64",
        "platform: windows-amd64",
        "os: macos-15-intel",
        'darwin-amd64) ASSET_NAME="policynim-${RELEASE_TAG}-darwin-amd64.tar.gz" ;;',
        "name: standalone-darwin-amd64",
        "path: release-downloads/standalone-darwin-amd64",
        '--expected "policynim-${RELEASE_TAG}-darwin-amd64.tar.gz"',
    ):
        assert token in text


def test_release_workflow_validates_standalone_mcp_config_contracts_from_empty_cwd() -> None:
    """Standalone bundles must prove no-clone MCP config, not source-checkout config."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        'SMOKE_EVIDENCE="$PWD/smoke-evidence"',
        'STANDALONE_SMOKE_CWD="$PWD/standalone-smoke-cwd"',
        'cd "$STANDALONE_SMOKE_CWD"',
        'POLICYNIM_STANDALONE="$PWD/dist/policynim/policynim.exe"',
        'POLICYNIM_STANDALONE="$PWD/dist/policynim/policynim"',
        '"$POLICYNIM_STANDALONE" mcp-config --client codex --target local-stdio --format json',
        (
            '"$POLICYNIM_STANDALONE" mcp-smoke --mcp-config-file '
            '"$SMOKE_EVIDENCE/policynim-standalone-mcp-config.json" --format json'
        ),
        (
            '"$POLICYNIM_STANDALONE" mcp-smoke --mcp-config-file '
            '"$SMOKE_EVIDENCE/policynim-standalone-claude-mcp-config.json" --format json'
        ),
        (
            '"$POLICYNIM_STANDALONE" mcp-config --target hosted-http --client codex '
            "--hosted-url https://example.invalid/mcp --bearer-token-env-var "
            "POLICYNIM_TOKEN --format json"
        ),
        (
            'python "$GITHUB_WORKSPACE/scripts/release_check.py" '
            "--validate-json-contract mcp-config-codex-local-stdio "
            '"$SMOKE_EVIDENCE/policynim-standalone-mcp-config.json"'
        ),
        (
            'python "$GITHUB_WORKSPACE/scripts/release_check.py" '
            "--validate-json-contract mcp-config-claude-code-local-stdio "
            '"$SMOKE_EVIDENCE/policynim-standalone-claude-mcp-config.json"'
        ),
        (
            'python "$GITHUB_WORKSPACE/scripts/release_check.py" '
            "--validate-json-contract mcp-config-codex-hosted-http "
            '"$SMOKE_EVIDENCE/policynim-standalone-hosted-mcp-config.json"'
        ),
        (
            'python "$GITHUB_WORKSPACE/scripts/release_check.py" '
            "--validate-json-contract mcp-config-claude-code-hosted-http "
            '"$SMOKE_EVIDENCE/policynim-standalone-claude-hosted-mcp-config.json"'
        ),
    ):
        assert token in text


def test_release_workflow_rejects_mismatched_manual_versions() -> None:
    """Prevent manual release dispatches from publishing inconsistent artifacts."""
    text = _read_text(RELEASE_WORKFLOW)

    assert 'project_version = project["project"]["version"]' in text
    assert 'requested_version = requested.removeprefix("v")' in text
    assert "requested_version != project_version" in text
    assert "Release version mismatch:" in text
