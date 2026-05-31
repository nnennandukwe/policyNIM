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

## GitHub Release

Create and push a version tag from the commit you want to release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The `Release` workflow builds the wheel, source distribution, and standalone
archives for Linux, Apple Silicon macOS, and Windows. It creates a draft GitHub
release with:

- Python wheel and source distribution
- `install.sh` and `install.ps1`
- `policynim-vX.Y.Z-linux-amd64.tar.gz`
- `policynim-vX.Y.Z-darwin-arm64.tar.gz`
- `policynim-vX.Y.Z-windows-amd64.zip`
- `SHA256SUMS`

PolicyNIM does not publish a macOS Intel standalone bundle because the pinned
LanceDB dependency does not ship macOS x86_64 wheels.

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
