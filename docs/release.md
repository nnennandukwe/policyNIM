# PolicyNIM Release Checklist

Use this guide when preparing a developer-facing CLI release. The default
release path is offline and deterministic; live NVIDIA, hosted Railway, and
Docker checks stay opt-in.

## Release Channels

PolicyNIM supports two direct install channels:

- Python package users install the CLI with
  `pipx install --python 3.11 policynim` or
  `uv tool install --python 3.11 policynim`.
- Standalone users install GitHub release binaries with `install.sh` or
  `install.ps1`, without cloning the repo or managing Python dependencies.

Both paths should support:

```bash
policynim --help
policynim init
policynim ingest
```

## Before Tagging

Run the deterministic gates locally:

```bash
uv lock --check
uv run ruff check .
uv run pyright
uv run pytest -q -m "not live and not docker_live"
uv build --out-dir dist
```

Then smoke the built wheel in a clean environment:

```bash
python -m venv /tmp/policynim-wheel-smoke
/tmp/policynim-wheel-smoke/bin/python -m pip install dist/*.whl
/tmp/policynim-wheel-smoke/bin/policynim --help
/tmp/policynim-wheel-smoke/bin/policynim --version
```

The `package-offline` CI job verifies fresh wheel installations on Linux and
Windows with Python 3.11 and 3.12. It creates each environment outside the source
checkout, compares installed package bytes to the wheel, validates packaged policy
sources, imports the checksum-pinned public generation with source matching, and
checks the served catalog identity. It also runs the original synthetic offline
evaluation and actual stdio/HTTP discovery for MCP protocols `2026-07-28` and
`2025-11-25`, including rejection of unauthenticated HTTP requests.

To repeat these checks, use the fresh environment's Python from a directory
outside this repository to run `scripts/verify_installed_package.py`. Supply the
absolute wheel and public bundle paths with `--wheel` and `--bundle`, the trusted
contents of `artifacts/public-generation/generation.sha256` with `--bundle-digest`,
the source commit with `--revision`, and an output JSON path with `--output`.
The verifier refuses an installation resolved back to a source checkout. It never
calls model providers; protocol discovery doesn't establish live retrieval quality
or Codex/Claude UI interoperability. Retain those acceptance results separately.

## GitHub Release

Create and push a version tag from the commit you want to release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The `Release` workflow builds the wheel, source distribution, and standalone
archives for Linux, Apple Silicon macOS, Intel macOS, and Windows. It creates a draft GitHub
release with:

- Python wheel and source distribution
- `install.sh` and `install.ps1`
- `policynim-vX.Y.Z-linux-amd64.tar.gz`
- `policynim-vX.Y.Z-darwin-arm64.tar.gz`
- `policynim-vX.Y.Z-darwin-amd64.tar.gz`
- `policynim-vX.Y.Z-windows-amd64.zip`
- `SHA256SUMS`

PolicyNIM publishes separate macOS bundles for Apple Silicon and Intel hosts.

Review the draft GitHub release before publishing. Confirm that the release
asset names match the installer scripts and that `SHA256SUMS` includes every
downloaded asset.

## PyPI

PyPI publishing uses PyPI trusted publishing through GitHub OIDC. Configure the
PyPI project to trust this repository and the `pypi` GitHub environment before
using the `publish-pypi` job. The release workflow runs `publish-pypi` only
after the GitHub release asset job succeeds, because PyPI versions are immutable
once uploaded.

For normal releases, publish PyPI from the final `vX.Y.Z` tag. For manual
workflow dispatches, leave `publish_pypi=false` unless you intentionally want to
publish the built distribution from that exact commit.

## Optional Hosted Smoke

After the draft release artifacts pass local smoke tests, run the manual
`Hosted Beta Smoke` workflow only when the deployed Railway beta and hosted MCP
secrets are available. This is a deployment promotion check, not part of the
offline release artifact build.

The fresh-wheel verifier requires `--lockfile uv.lock` and `--lock-digest sha256:<digest>`.
CI installs exported locked dependency artifacts with required hashes before installing
the wheel without dependency resolution. Its report records the lock digest and actual
installed versions; bootstrap installer packages are identified separately.
