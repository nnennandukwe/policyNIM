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
Each matrix job uploads its public `package-evidence.json` receipt, downloads the
same named artifact into a separate directory, and requires byte-for-byte equality
with the original. Downloads retain the action's default digest-mismatch error;
missing or changed receipts fail the job. This credential-free round trip covers
artifact transport on both operating systems and Python versions without
dispatching a release or making provider calls.

To repeat these checks, use the fresh environment's Python from a directory
outside this repository to run the absolute path to `scripts/verify_installed_package.py`. Supply the
absolute wheel and public bundle paths with `--wheel` and `--bundle`, the trusted
contents of `artifacts/public-generation/generation.sha256` with `--bundle-digest`,
the source commit with `--revision`, and an output JSON path with `--output`.
Also supply `--lockfile /absolute/path/to/checkout/uv.lock`,
`--lock-digest sha256:<digest>`, and `--requirements` with the absolute path to the
exported runtime requirements used for installation. Relative paths resolve from
the working directory where you launch the verifier, not from its script directory.
CI exports with
`uv export --locked --no-dev --group verification --no-emit-project`, installs those
artifacts with required hashes, and installs the wheel without dependency resolution.
The `verification` group supplies the verifier's locked `packaging` parser; include
it in the export for manual fresh-environment checks too. The report records the
lock digest, requirements digest and actual installed versions.
Bootstrap installer packages are recorded separately when they weren't selected
for the runtime installation.
The verification group isn't part of the application runtime. Before running the
manifest command manually, use
`uv sync --locked --no-dev --no-default-groups --group verification`.
The verifier refuses an installation resolved back to a source checkout. It never
calls model providers; protocol discovery doesn't establish live retrieval quality
or Codex/Claude UI interoperability. Retain those acceptance results separately.

Native standalone builds use uv-managed CPython 3.11.13 on every platform. The
workflow selects that interpreter explicitly for dependency sync, runtime checks,
PyInstaller and archive verification; the source/wheel test jobs still cover
Python 3.11 and 3.12. To check the native build interpreter locally:

```bash
uv python install --managed-python 3.11.13
uv sync --locked --managed-python --python 3.11.13 --group release
uv run --no-sync --managed-python --python 3.11.13 python scripts/check_standalone_runtime.py --expected-python 3.11.13
uv run --no-sync --managed-python --python 3.11.13 pyinstaller --clean --noconfirm packaging/pyinstaller.spec
```

The runtime check prints JSON identifying the executing Python, SQLite vector
extension and both Python/cryptography OpenSSL implementations. It enables SQLite
extension loading, loads the real vector extension, queries its version, then
disables loading and closes the in-memory connection. Missing APIs, native imports,
cleanup failures and an unexpected interpreter version block freezing. Error
records retain a fixed phase and exception class without native loader payloads.
This check doesn't verify the later frozen library layout: each compressed archive
must still pass the existing generation and transport checks below.

The failed v0.2.0 macOS artifacts remain failed evidence. A Python.org 3.11.9
SQLite component lacked the required extension-loading API; the Intel archive also
failed to load a cryptographic binding because of an OpenSSL symbol mismatch.
Selecting the managed runtime doesn't establish either architecture's recovery
until its new archive passes verification. If verification fails after an archive
has been created, the workflow retains that archive under the existing standalone
artifact name for diagnosis. The failed job still blocks publication. Runtime
check output stays in job logs and doesn't add a release asset or change the
publisher's inputs.

Standalone builds must also import the public generation and negotiate both MCP
protocols over stdio and authenticated HTTP. Run `scripts/verify_standalone.py`
with `--command` pointing to a local build or `--archive` selecting a compressed
release artifact, `--bundle` and `--bundle-digest`
identifying the reviewed public archive, `--lockfile` and `--lock-digest`
identifying the selected build lock, `--expected-version X.Y.Z` matching the
release package version, and `--revision` and `--output` identifying the source
commit and receipt. Both verifiers give child processes an owned temporary home directory, keeping
user profiles outside the packaged-source check while preserving the parent environment.
This check runs without provider credentials and records a
digest of the complete standalone directory, including native-library bytes and
file/directory permissions. Special entries such as FIFOs are rejected before
execution; supported entries are regular files, directories and contained symlinks.
Credential redaction covers the bounded log-tail
boundary; diagnostics are omitted if a known credential exceeds its capture bound.

Final publication requires `--archive /absolute/path/to/the/platform.tar.gz` (or the
Windows `.zip`). The verifier copies those compressed bytes into an owned temporary
directory, safely extracts them, and runs the extracted command instead of the
original build directory. Only a completed six-case offline fixture and both MCP
protocol/transport checks can produce `archive_verified: true` and the archive
checksum. Changed, unsafe or incomplete archives fail before publication; failed
receipts retain the requested source, platform, version and digest context as
incomplete execution.

The lock digest identifies the selected build checkout. Git attributes preserve
`uv.lock` with LF line endings on every platform so Windows receipts and the
publisher hash the same bytes. A platform-specific hash remains a promotion failure.
The workflow performs a
locked dependency sync before building from that checkout; the standalone receipt
alone doesn't independently inspect every dependency version inside the frozen
executable.
Authenticated HTTP checks cover beta/operator keys and missing-token rejection.
Receipts record `authentication_scope: beta_operator_key` and
`external_oauth_e2e: not_run`. External issuer/client acceptance remains open in
#97; this gate doesn't establish OAuth E2E, live retrieval quality or coding-client acceptance.

The release workflow retains a separate `standalone-evidence-<platform>.json`
receipt for each platform. A successful help command alone can't detect a missing
SQLite vector extension; generation import must succeed before the archive ships.

## GitHub Release

Before signing and publishing, the workflow checks the remote release tag against
the artifact source commit. An existing lightweight or annotated tag must resolve
to that commit. A missing tag can be created at the verified source; an unavailable
remote or mismatched existing tag blocks publication. The gate never moves tags.

Before creating an immutable tag for a native-runtime change, run the `Release`
workflow manually at the reviewed branch with `build_only=true` and version
`0.2.1`. This mode runs the source, wheel and native archive jobs and retains their
existing verification reports and archives. Both publisher jobs are disabled:
it doesn't sign assets, create a draft, create a tag or upload to PyPI. The hold
also applies if `publish_pypi=true` was selected. Wait for both macOS archive checks
to pass at the selected revision before tagging; don't infer recovery from a
preflight pass or a different platform's result. `build_only` defaults to false,
so ordinary manual runs still prepare a GitHub draft and tag runs keep the normal
publication gates.

Create and push a version tag from the commit you want to release:

```bash
git tag v0.2.1
git push origin v0.2.1
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
`uv run --no-sync python scripts/release_manifest.py release-assets --tag vX.Y.Z --source-sha <40-character-commit>`.
The publishing job independently rebuilds the wheel from its reviewed checkout
with the pinned build backend. The downloaded wheel must have the same filename
and byte-for-byte checksum as that rebuild before manifest creation or signing.
Matching receipts supplied by the build job can't authorize a changed wheel.
This comparison trusts the publisher's checkout and build environment; it doesn't
establish a hermetic build or protect a compromised publishing job.
The source revision must match the checked-out commit and the tag must match
`pyproject.toml`. The gate requires fresh wheel receipts for Python 3.11 and 3.12 plus four standalone platform
receipts with the same dependency lock, generation and catalog. Every wheel and
standalone receipt must include exactly six offline cases with a pass rate of one;
missing or reduced fixture evidence blocks publication. Each standalone
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

Wheel `METADATA` and source `PKG-INFO` must match the selected `pyproject.toml`
package identity, Python requirement, dependencies and advertised extras. The gate
uses canonical requirement/specifier objects and retains dependency markers for
all target environments; it doesn't check only dependencies active on the current
host. A mismatch blocks manifest creation. Rebuild the distribution from the
selected checkout before retrying.

Before signing or draft creation, the workflow also renders the selected version's
reviewed CHANGELOG section with `check_release_notes.py`. After actual attestation
verification, it runs the existing evidence collector with artifact/provenance checks.
Expected identities come from CI and the checked-out lock/public bundle; downloaded
receipts can't choose them. Selection and report are retained in `release-evidence/`
outside the signed assets. Once asset preparation succeeds, selection and collection
also run after a failed signing/tag/provenance step unless the run was cancelled.
That preserves a held report for missing or failed attestation evidence; the original
failed step still blocks draft creation. Retention runs after failed collection,
but skips when asset preparation never completed. A missing, failed or mismatched
receipt stops draft creation.
See [the scoped evidence guide](release-evidence.md) for its trust boundary. This gate
doesn't establish actual coding-client, published installation, deployment or rollback
acceptance. Those phases still need their own executed receipts.

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
receipt. On the Ubuntu publishing runner, stderr is drained through a pipe with
only a bounded prefix held in memory; it isn't spooled to disk. Timeout cleanup
terminates the owned verifier process group, including children that
retain the pipe, and reaps the direct verifier process. Hints don't establish the cause or relax any verification constraint.
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
`POLICYNIM_VERIFY_ATTESTATION=required` in the installer process. Both installers
accept the documented mode names regardless of letter case.
The installer resolves the selected release tag's commit and verifies the archive
against that source and release workflow. A missing verifier, unresolved tag or
rejected attestation stops installation and preserves the previous install.
Unset the option or set it to `0` for checksum-only installation; invalid values
fail rather than silently skipping a requested check. Older releases without
attestations can't pass the required mode.
