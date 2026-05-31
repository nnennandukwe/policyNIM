# PolicyNIM Release Checklist

Use this guide when preparing a developer-facing CLI release. The default
release path is offline and deterministic; live NVIDIA, hosted Railway, and
Docker checks stay opt-in.

## Release Channels

PolicyNIM supports two direct install channels:

- Python package users will install the CLI with
  `pipx install --python 3.11 policynim` or
  `uv tool install --python 3.11 policynim`.
- Standalone users install GitHub release binaries with `install.sh` or
  `install.ps1`, without cloning the repo or managing Python dependencies.

Keep PyPI package installs in user-facing docs, but keep public launch proof
separate: public PyPI JSON lists the current wheel and sdist, while a successful
Release workflow `publish-pypi` job proves the trusted-publishing path for the
tag under review. Public launch proof also requires a clean PyPI install smoke
for the published version. If that smoke reports a missing first-run command,
hold the public launch claim until a new PyPI release is built from the current
CLI and the smoke passes.

Both paths should support:

```bash
policynim --help
policynim quickstart
policynim doctor
policynim init
policynim ingest
```

## Ship/Hold Release Gate

SHIP only when all required evidence is present:

- deterministic gates passed: `uv lock --check`, `uv run ruff check .`,
  `uv run pyright`, and `uv run pytest -q -m "not live and not docker_live"`
- release notes passed:
  `uv run python scripts/check_release_notes.py --format json`
- local OSS-readiness and maintainer taxonomy gates passed:
  `uv run python scripts/oss_readiness_check.py --format json` and
  `uv run python scripts/sync_github_labels.py --format json` plus
  `uv run python scripts/sync_github_topics.py --format json`
- built wheel was installed in a clean environment and ran `policynim --help`,
  `policynim init --help`, `policynim ingest --help`,
  `policynim preflight --help`, `policynim quickstart --format json`,
  `policynim doctor --format json`, `policynim support-bundle`,
  `policynim mcp-smoke --format json`, and no-checkout local stdio config for
  Codex and Claude Code, generated-config MCP smoke for both local clients,
  hosted HTTP config for Codex and Claude Code, and `policynim --version`
- each standalone bundle ran `--help`, `init --help`, `ingest --help`,
  `preflight --help`, `quickstart --format json`,
  `quickstart --target local-cli --format json`,
  `quickstart --target local-mcp --format json`, `doctor --format json`,
  `support-bundle`, `mcp-config --help`,
  standalone local stdio config for Codex and Claude Code,
  hosted Codex and Claude Code config JSON, and `--version`
- release workflow retained review-only smoke evidence artifacts:
  `release-wheel-smoke-evidence` and `standalone-smoke-evidence-*`
- manual release workflow dispatch recorded the intended release mode; if the
  run is a public launch candidate, `public_launch=true` and
  `publish_pypi=true` were both selected so PyPI trusted-publishing evidence is
  available
- draft GitHub release contains the Python distribution, all standalone
  bundles, `install.sh`, `install.ps1`, `SHA256SUMS`, and
  `RELEASE_MANIFEST.json`
- `RELEASE_MANIFEST.json` lists every public asset with `size_bytes` and
  `sha256`
- release workflow generated GitHub artifact attestations from the same
  `SHA256SUMS` file used for download verification
- PyPI publish evidence is explicit: public PyPI JSON lists the current wheel
  and sdist, and trusted-publishing run evidence is attached before a public
  launch claim
- public PyPI install smoke passed from a clean environment with
  `policynim --help`, primary command help, semantic first-run quickstart JSON
  targets, support-bundle hosted `client_commands` for Codex and Claude Code,
  support-bundle `hosted_url`/`beta_portal_url` token flow, doctor JSON, and
  local MCP config JSON
- public GitHub release installer smoke passed from a clean `HOME` with the
  published `install.sh`, install.sh guidance that distinguishes hosted MCP
  from local CLI/MCP setup, primary command help, semantic first-run quickstart
  JSON targets, support-bundle hosted `client_commands` for Codex and Claude Code,
  support-bundle `hosted_url`/`beta_portal_url` token flow, doctor JSON, and
  local MCP config JSON
- Hosted Beta Smoke decision is explicit: skipped with a reason, or run with the
  deployed Railway URL and secret-gated live test output attached from the
  `hosted-smoke-evidence` artifact

HOLD when any release artifact is missing, checksums do not match, installer
asset names differ from the scripts, PyPI trusted publishing is unconfirmed, or
hosted smoke status is ambiguous.

For a local operator-ready check, run:

```bash
uv run python scripts/release_check.py
```

This command runs the deterministic gates, builds the distribution into a
temporary directory, installs the wheel into a clean virtual environment, and
smokes the installed CLI from an empty working directory. Before packaging, it
also runs the local OSS-readiness JSON check and the GitHub label taxonomy
and topic taxonomy dry-runs without calling live hosted services. It also renders
`scripts/oss_readiness_check.py --format launch-issue` so the paste-ready public
launch issue stays covered by the release gate, and validates that external
proof collection commands in that issue use `--require-requested-probes` before
packaging starts. When
`--external-evidence-file docs/launch-evidence.json` is supplied, the release
gate passes that same file to the readiness JSON and launch-issue renderer so
checked-off external proof does not reappear as missing in generated evidence.
The clean wheel smoke includes `policynim init --help`,
`policynim ingest --help`, `policynim preflight --help`,
`policynim quickstart --format json`,
`policynim quickstart --target local-cli --format json`,
`policynim quickstart --target local-mcp --format json`, and
`policynim mcp-smoke --format json`. The release gate validates that setup,
indexing, and verification command help still expose the installed commands;
`init --help` must name `NVIDIA_API_KEY`, and `preflight --help` must show
`--task`. It then parses the first-run JSON outputs and validates semantic quickstart contracts,
including hosted `client_commands`, copyable `agent_workflows` for
`policy_preflight`, `policy_search`, and MCP tool-list verification, plus the clean installed wheel reporting local MCP
`local_launch_mode` as `installed-cli` instead of a source-checkout path. It
also validates the support-bundle first-run contract, including each target's `quickstart_command`,
hosted `client_commands` for Codex and Claude Code, and `agent_workflows`, so public issue
diagnostics from a clean installed wheel cannot point reporters at
checkout-only `uv run` or `--repo-root` commands.
`policynim mcp-smoke --format json`
initializes local MCP stdio and lists `policy_preflight` and `policy_search`
without calling either tool. The gate also validates `mcp-config --format json`
for Codex and Claude Code: local stdio config must launch the installed
`policynim` entrypoint without a source checkout, and hosted config must keep
the `https://example.invalid/mcp` placeholder plus `POLICYNIM_TOKEN` env-var
reference instead of embedding secrets. The gate then runs `mcp-smoke
--mcp-config-file` against both generated local stdio configs so the release
proves the config can drive the same list-tools handshake. The release workflow
also runs standalone MCP stdio smoke from each packaged binary before uploading
release assets, then keeps that JSON output in review-only smoke evidence. It exits `0` with a
`ship` decision only when every required check passes; the standalone MCP stdio smoke
check is part of that review path. It exits `1` with a `hold` decision on the
first failed required check.

Use JSON output when attaching release evidence to a PR, issue, or draft
release note:

```bash
uv run python scripts/release_check.py --format json
```

Use `--dry-run --format json` when you only need to inspect the planned gate
sequence. Dry runs return `not_evaluated` because they do not prove release
readiness.

When the release decision is also the public launch decision, run the same
release gate in strict public mode after `docs/launch-evidence.json` has been
filled. This adds `scripts/oss_readiness_check.py --strict-public` before the
package build and exits `1` if external proof is still incomplete:

```bash
uv run python scripts/release_check.py \
  --strict-public \
  --external-evidence-file docs/launch-evidence.json \
  --format json
```

Use the default release check for local package readiness. Use strict public
mode only when you want the top-level release gate to enforce GitHub release,
PyPI trusted-publishing, hosted MCP, hosted smoke, label-apply, and real MCP
client-session evidence too.

When changing the package version in `pyproject.toml`, add the matching
`CHANGELOG.md` section before running the release gate. The release notes check
fails before packaging when the current version is missing:

```bash
uv run python scripts/check_release_notes.py --format json
```

The GitHub release workflow uses the same checker to render draft release notes
from the matching changelog section:

```bash
python3 scripts/check_release_notes.py --write-github-release-notes release-notes.md
```

For OSS launch readiness, run the broader local evidence check:

```bash
uv run python scripts/oss_readiness_check.py --format json
```

This command does not call GitHub, PyPI, Railway, hosted MCP, or real MCP
clients. It exits `0` when local repository evidence is ready and reports
external proof points as `missing_external`. Use Markdown output when attaching
the current state to a launch issue or release note:

```bash
uv run python scripts/oss_readiness_check.py --format markdown
```

Use the launch-issue renderer when the local gate is clean but public proof is
still being collected. It outputs a checkbox list for the remaining external
evidence without changing the strict public gate:

```bash
uv run python scripts/oss_readiness_check.py --format launch-issue
```

After the launch evidence file exists, render the issue with partial evidence
checked off:

```bash
uv run python scripts/oss_readiness_check.py --external-evidence-file docs/launch-evidence.json --format launch-issue
```

The generated launch issue includes `Missing Evidence Collection Commands` for
any external proof point that is not checked off yet. Use those commands rather
than copying older release-note snippets when collecting remaining public
launch evidence. They include `--format json` so the collector still prints
probe statuses when it writes or merges `docs/launch-evidence.json`. Checklist
`Next` lines point to those command blocks instead of duplicating shorter
commands. For GitHub labels and topics, the generated command blocks keep the
maintainer workflow dry-run-first: `gh auth status`, then `--live --format
json`, then `--apply --format json`, and only then evidence collection.

Before claiming public launch readiness, run:

```bash
uv run python scripts/oss_readiness_check.py --strict-public --format json
```

`--strict-public` exits `1` with `hold_external_missing` until GitHub release
artifacts, GitHub release installer smoke, PyPI state, PyPI install smoke,
hosted MCP domain, hosted smoke, GitHub label/topic apply, and real MCP
client-session evidence are attached. Generate the evidence file from
the current script contract, replace the empty `summary`, `reference`,
`verified_by`, and `verified_at` values with real evidence from the last 14
days, and pass it explicitly when the external proof points exist. Evidence
timestamps more than 10 minutes in the future or more than 14 days old are
rejected as stale before strict public readiness can pass:

```bash
uv run python scripts/oss_readiness_check.py --write-external-evidence-template docs/launch-evidence.json
```

Strict public evidence also checks reference shapes before accepting a record.
Use a GitHub release tag URL for release artifacts, a GitHub release tag URL
for GitHub release installer smoke, a GitHub release tag URL with
`#<asset-name>` for artifact attestations, a GitHub Actions run URL for PyPI
trusted-publishing and Hosted Beta Smoke proof, an HTTPS hosted `/healthz` or
`/mcp` URL for hosted MCP domain proof, and the exact
`gh label list --json name,color,description --limit 1000` command for label
proof.
When `hosted_mcp_domain` evidence is present, strict public readiness also runs
`strict_public_hosted_onboarding_docs`: the README, agent workflow guide, Codex
example, and Claude Code example must publish the same verified hosted `/mcp`
origin instead of leaving first-run users on `<railway-domain>` placeholders.

Use the opt-in collector to prefill only the externally verifiable records:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

The collector calls GitHub, PyPI, and `gh label list`. `--merge-existing`
preserves complete reviewed records for checks the current run cannot verify,
but a current live label or topic drift probe clears the older GitHub metadata
record instead of preserving stale proof; requested GitHub release installer
smoke or PyPI install smoke failures clear older `github_release_install_smoke`
or `pypi_install_smoke` proof for the same reason. GitHub release artifact
probe failures, including draft releases, missing required assets, or invalid
release metadata, clear older `github_release_artifacts` proof instead of
preserving stale release evidence. Use `--force` only when replacing the
evidence file from scratch. For GitHub release artifact
evidence, the collector downloads `RELEASE_MANIFEST.json` and `SHA256SUMS` and
requires both files to list the same expected payload assets with matching SHA-256 digests
before filling `github_release_artifacts`. The strict public gate also requires
the `github_release_artifacts` summary to name every current expected release
asset for the project version, so older evidence cannot pass after a new
standalone asset enters the release contract. After release asset attestations exist, add
`--release-attestation-asset-name install.sh` for routine launch proof so the
collector downloads a small public asset and verifies it with `gh attestation
verify` before filling `github_artifact_attestations`. Use a standalone bundle
name, such as `policynim-v<version>-linux-amd64.tar.gz`, when auditing
bundle-specific provenance. Add `--github-install-smoke` to run the published
Unix installer in a clean `HOME` and prove the installed CLI reaches the same
first-run contract as the README. The collector also requires the JSON output to
include at least one attested subject from
`verificationResult.statement.subject` and that the selected release asset name
appears in that subject list. When a public deployment exists, add
`--pypi-publish-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>'`
after the Release workflow publishes to PyPI. The collector combines public
PyPI JSON with the successful `publish-pypi` job to fill package-channel
evidence. Public PyPI JSON must include the release version and both expected
distribution files: `policynim-<version>-py3-none-any.whl` and
`policynim-<version>.tar.gz`. The Release workflow run must also report a
`headSha` matching the GitHub release `targetCommitish`; otherwise the
collector leaves PyPI evidence blank so an older trusted-publish run cannot
prove the current release. Add
`--hosted-mcp-url 'https://<railway-domain>/mcp'` so it can also check same-origin `/healthz` and
verify `/mcp` rejects an invalid bearer token.
After the secret-gated Hosted Beta Smoke workflow passes, add
`--hosted-smoke-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>'`
so the collector can verify the successful workflow run before filling hosted
smoke evidence. The run must report a `headSha` matching the GitHub release
`targetCommitish`; otherwise stale hosted-smoke output cannot prove the current
release. The collector also downloads the `hosted-smoke-evidence` artifact and
validates `policynim-hosted-smoke-junit.xml` for the expected live MCP checks.
After a real Codex or Claude Code session has connected to the generated MCP
config, create a reviewed JSON record matching
`docs/mcp-client-evidence.example.json`. The checked-in example leaves
`setup_command` and `reference` blank so it cannot count as proof by itself.
Generate a launch-notes copy with
`--write-mcp-client-evidence-template launch-notes/codex-mcp-session.json`,
then fill the sanitized reference from the real client run. For hosted HTTP
sessions, use `--mcp-client-hosted-url 'https://<railway-domain>/mcp'` so the
collector derives the secret-safe setup command; use
`--mcp-client-setup-command` for local `stdio` sessions or reviewed custom
client commands. Pass `--mcp-client-evidence-file <path>` so the collector can
validate the client, transport, setup command, tool list, `policy_preflight`
call, sanitized reference, and `secrets_included=false` before filling
client-session evidence.
It stays out of the offline release gate and leaves any proof it cannot verify
directly blank for maintainer review. Blank records are allowed while launch
evidence is still in progress, but they remain `missing_external` in the strict
readiness report.

```bash
uv run python scripts/oss_readiness_check.py \
  --strict-public \
  --external-evidence-file docs/launch-evidence.json \
  --format json
```

Use [public-launch-runbook.md](public-launch-runbook.md) when turning those
external proof points into an ordered launch issue or release-note workflow.

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
uv run python -m venv /tmp/policynim-wheel-smoke
/tmp/policynim-wheel-smoke/bin/python -m pip install --upgrade pip
/tmp/policynim-wheel-smoke/bin/python -m pip install dist/*.whl
mkdir -p /tmp/policynim-wheel-cwd
cd /tmp/policynim-wheel-cwd
/tmp/policynim-wheel-smoke/bin/policynim --help
/tmp/policynim-wheel-smoke/bin/policynim init --help
/tmp/policynim-wheel-smoke/bin/policynim ingest --help
/tmp/policynim-wheel-smoke/bin/policynim preflight --help
/tmp/policynim-wheel-smoke/bin/policynim quickstart --format json
/tmp/policynim-wheel-smoke/bin/policynim quickstart --target local-cli --format json
/tmp/policynim-wheel-smoke/bin/policynim quickstart --target local-mcp --format json
/tmp/policynim-wheel-smoke/bin/policynim doctor --format json
/tmp/policynim-wheel-smoke/bin/policynim support-bundle
/tmp/policynim-wheel-smoke/bin/policynim mcp-smoke --format json
/tmp/policynim-wheel-smoke/bin/policynim mcp-config --client codex --target local-stdio --format json
/tmp/policynim-wheel-smoke/bin/policynim mcp-config --client claude-code --target local-stdio --format json
/tmp/policynim-wheel-smoke/bin/policynim mcp-config --target hosted-http --client codex --hosted-url https://example.invalid/mcp --bearer-token-env-var POLICYNIM_TOKEN --format json
/tmp/policynim-wheel-smoke/bin/policynim mcp-config --target hosted-http --client claude-code --hosted-url https://example.invalid/mcp --bearer-token-env-var POLICYNIM_TOKEN --format json
/tmp/policynim-wheel-smoke/bin/policynim --version
```

## GitHub Release

Create and push a version tag from the commit you want to release:

```bash
git tag v<version>
git push origin v<version>
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
- `RELEASE_MANIFEST.json`

The workflow also uploads review-only Actions artifacts named
`release-wheel-smoke-evidence` and `standalone-smoke-evidence-*`. These contain
the `init --help`, `ingest --help`, and `preflight --help` output plus smoke-test
JSON generated by `quickstart`, `doctor`, `support-bundle`, `mcp-smoke`, and MCP
config commands, including `mcp-smoke --mcp-config-file` for generated local
Codex and Claude Code stdio config. Standalone bundle smoke runs from an empty
`standalone-smoke-cwd` and validates the same semantic MCP config contracts used
by the wheel smoke, so standalone local stdio config cannot depend on a source
checkout accidentally. They are
intentionally not copied into the draft GitHub release assets; use them for
maintainer review, then keep the published release focused on install payloads,
checksums, and the manifest.

The release workflow generates `SHA256SUMS` and `RELEASE_MANIFEST.json` with
`scripts/release_manifest.py`. The script fails the workflow when any expected
public asset is missing, which turns asset completeness into a ship/hold gate
rather than a manual spot check.

The release workflow also creates GitHub artifact attestations for the public
assets by running `actions/attest` against the generated checksum file:

```yaml
- name: Generate release asset attestations
  uses: actions/attest@59d89421af93a897026c735860bf21b6eb4f7b26 # v4.1.0
  with:
    subject-checksums: release-assets/SHA256SUMS
```

Immediately after generating attestations, the workflow verifies the small
`install.sh` release asset and uploads the JSON result as a review-only
`release-attestation-evidence` Actions artifact:

```bash
gh attestation verify release-assets/install.sh \
  --repo "$GITHUB_REPOSITORY" \
  --format json
```

That evidence artifact is not copied into the draft GitHub release. It exists
so maintainers can inspect provenance verification before publishing.

After downloading an asset, users can verify its provenance with GitHub CLI:

```bash
gh attestation verify policynim-v<version>-linux-amd64.tar.gz -R <owner>/<repo>
```

The installers keep checksum verification as the default hard gate. Users who
want install-time provenance verification can install GitHub CLI and set
`POLICYNIM_VERIFY_ATTESTATION=1` before running `install.sh` or `install.ps1`;
the installer runs `gh attestation verify` before extracting the downloaded
bundle.

PolicyNIM publishes separate macOS bundles for Apple Silicon and Intel hosts.

Review the draft GitHub release before publishing. Confirm that the release
asset names match the installer scripts and that `SHA256SUMS` includes every
downloaded asset. Also review `RELEASE_MANIFEST.json` for the expected tag,
source SHA, asset names, sizes, and checksums, and confirm the
`gh attestation verify` command succeeds for at least one public release asset,
using `install.sh` for fast routine proof or a standalone bundle for
bundle-specific review, before publishing.

## PyPI

PyPI publishing uses PyPI trusted publishing through GitHub OIDC. Configure the
PyPI project to trust this repository and the `pypi` GitHub environment before
using the `publish-pypi` job. The release workflow runs `publish-pypi` only
after the GitHub release asset job succeeds, because PyPI versions are immutable
once uploaded.

For normal releases, publish PyPI from the final `vX.Y.Z` tag. For manual
workflow dispatches, leave `publish_pypi=false` unless you intentionally want to
publish the built distribution from that exact commit. When a manual dispatch
is the public launch candidate, set both `public_launch=true` and
`publish_pypi=true`; the workflow fails fast if `public_launch=true` is selected
without `publish_pypi=true` so a GitHub-only release candidate cannot be
mistaken for public launch proof.

After publishing, collect PyPI launch evidence with the public project/version,
the wheel and sdist listed under the matching PyPI release, and the successful
Release workflow run:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --pypi-publish-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>' \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

Then prove public installability from PyPI with a clean virtualenv smoke:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --pypi-install-smoke \
  --require-requested-probes \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

This installs `policynim==<version>` from public PyPI, then validates
`policynim --help`, primary command help, semantic hosted/local CLI/local MCP
quickstart JSON, support-bundle hosted `client_commands` for Codex and Claude Code,
support-bundle `hosted_url`/`beta_portal_url` token flow, doctor JSON, and
Codex/Claude Code local stdio MCP config JSON from the installed package.

## Optional Hosted Smoke

After the draft release artifacts pass local smoke tests, run the manual
`Hosted Beta Smoke` workflow only when the deployed Railway beta and hosted MCP
secrets are available. This is a deployment promotion check, not part of the
offline release artifact build. Review the uploaded `hosted-smoke-evidence`
artifact, especially `policynim-hosted-smoke-junit.xml`, before attaching the
run URL as hosted beta launch proof.
