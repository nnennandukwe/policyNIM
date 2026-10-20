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

See the [compatibility and evidence matrix](release-compatibility.md) for exact
SDK, protocol, Python, client, authorization and optional-extra coverage.

## Before Tagging

Run the deterministic gates locally:

```bash
uv sync --locked --group test --group dev
uv lock --check
uv run --no-sync ruff check .
uv run --no-sync pyright
uv run --no-sync pytest -q -m "not live and not docker_live"
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
outside this repository to run the absolute path to `scripts/verify_installed_package.py`. Supply the
absolute wheel and public bundle paths with `--wheel` and `--bundle`, the trusted
contents of `artifacts/public-generation/generation.sha256` with `--bundle-digest`,
the source commit with `--revision`, and an output JSON path with `--output`.
Also supply `--lockfile /absolute/path/to/checkout/uv.lock`,
`--lock-digest sha256:<digest>`, and `--requirements` with the absolute path to the
exported runtime requirements used for installation. Relative paths resolve from
the working directory where you launch the verifier, not from its script directory.
CI exports with `uv export --locked --no-dev --no-emit-project`, installs those
artifacts with required hashes, and installs the wheel without dependency resolution.
The report records the lock digest, requirements digest and actual installed versions.
Bootstrap installer packages are recorded separately when they weren't selected
for the runtime installation.
The verifier refuses an installation resolved back to a source checkout. It never
calls model providers; protocol discovery doesn't establish live retrieval quality
or Codex/Claude UI interoperability. Retain those acceptance results separately.

Standalone builds must also import the public generation and negotiate both MCP
protocols over stdio and authenticated HTTP. Run `scripts/verify_standalone.py`
with `--command` pointing to the built executable, `--bundle` and `--bundle-digest`
identifying the reviewed public archive, `--lockfile` and `--lock-digest`
identifying the build lock, and `--revision` and `--output` identifying the source
commit and receipt. This check runs without provider credentials and records a
digest of the complete standalone directory, including native-library bytes.
It doesn't establish live retrieval quality or coding-client acceptance.

The release workflow retains a separate `standalone-evidence-<platform>.json`
receipt for each platform. A successful help command alone can't detect a missing
SQLite vector extension; generation import must succeed before the archive ships.

## GitHub Release

Before signing and publishing, the workflow checks the remote release tag against
the artifact source commit. An existing lightweight or annotated tag must resolve
to that commit. A missing tag can be created at the verified source; an unavailable
remote or mismatched existing tag blocks publication. The gate never moves tags.

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
- `policynim-public-generation.zip` and its `policynim-public-generation.sha256`
- Platform-specific `standalone-evidence-<platform>.json` receipts

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

## Artifact evidence manifest

Before creating the draft, publication runs
`python scripts/release_manifest.py release-assets --tag vX.Y.Z --source-sha <40-character-commit>`.
The source revision must match the checked-out commit and the tag must match
`pyproject.toml`. The gate requires fresh wheel receipts for Python 3.11 and 3.12 plus four standalone platform
receipts with the same dependency lock, generation and catalog. Each standalone
receipt binds its archive checksum after compression; the wheel receipt binds the
actual installed wheel. Full offline, lint and type checks run on both Python versions. Missing platforms, failed probes, changed downloads,
unexpected assets and stale receipts stop publication.

`RELEASE_MANIFEST.json` records these identities and each download's checksum.
`SHA256SUMS` also covers the manifest. This gate covers artifact verification;
real coding-client, live-provider, production and rollback acceptance stay separate
under #104. Keep those receipts tied to the final release revision before promotion.

The source distribution includes tracked package source, public policies, evaluation
fixtures, README, license, build metadata, lockfile and ignore rules. Checkout pointers
and agent state aren't package inputs. The gate compares every source-archive file
with these tracked inputs and checks package metadata before adding the source
download to the manifest; missing, altered and unexpected source files block publication.

## Signed release provenance

After manifest checks, the release job signs every subject in `SHA256SUMS` using
GitHub's pinned `actions/attest` action. Before draft creation, each subject must
pass `gh attestation verify` against this repository,
`nnennandukwe/policyNIM/.github/workflows/release.yml` and the exact release source
commit. Each verifier call has a 45-second timeout. A timeout immediately stops
verification as incomplete; only nonzero exits retry, up to three attempts with
two-second waits. Failures retain partial receipts and stop publication. Receipts
include each attempt's outcome and return code, plus static recovery hints derived
from at most 8 KiB of stderr. Raw command output isn't printed or retained in the
receipt. Hints don't establish the cause or relax any verification constraint.
Signing permissions belong only to that job.
The workflow retains the signing bundle and verification receipt as workflow
artifacts, outside the release inputs they describe.

This verifies the workflow identity and artifact bytes. It doesn't establish
independent review, a hermetic build, a security certification or a SLSA level.
[GitHub's action documentation](https://github.com/actions/attest/tree/59d89421af93a897026c735860bf21b6eb4f7b26)
explains its provenance and signing permissions. Actual signing and verification
are observed during the release workflow; local subprocess tests don't create
cryptographic evidence. Synthetic archive tests prove installer gate ordering
and preservation of an existing install. Fresh-artifact packaging, runtime and
client acceptance belong to the separate release evidence and runtime verifier
receipts; those checks don't replace signing evidence.

Installers can require this check before extraction and replacement. Install
GitHub CLI and authenticate it with `gh auth login`. Set
`POLICYNIM_VERIFY_ATTESTATION=required` in the installer process.
The installer resolves the selected release tag's commit and verifies the archive
against that source and release workflow. A missing verifier, unresolved tag or
rejected attestation stops installation and preserves the previous install.
Unset the option or set it to `0` for checksum-only installation; invalid values
fail rather than silently skipping a requested check. Older releases without
attestations can't pass the required mode.
