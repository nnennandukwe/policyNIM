# PolicyNIM Public Launch Runbook

Use this runbook when moving from local OSS readiness to a public launch claim.
The local checks can prove repository quality, package smoke, and maintainer
workflow coverage. They cannot prove GitHub release availability, a clean
GitHub release installer smoke, PyPI state, a clean public PyPI install, a live
hosted MCP domain, applied GitHub labels/topics, hosted smoke output, or a real
MCP client session until a maintainer attaches external evidence.

## Launch Decision

SHIP only when the final strict check returns `public_ready`.

HOLD when the strict check returns `hold_external_missing`, when any external
proof point is ambiguous, or when evidence includes secrets. No API keys,
bearer tokens, private policy content, or hosted beta session secrets belong in
the evidence file, launch issue, release note, or public screenshots. The
external evidence should name what was verified, where the proof lives, who
verified it, and when it was verified; do not include tokens.

## 1. Confirm The Local Gate Sequence

First inspect the release gate order without building artifacts:

```bash
uv run python scripts/release_check.py --dry-run --format json
```

Then run the local release check:

```bash
uv run python scripts/release_check.py
```

This must pass before tagging or publishing. It covers deterministic local
checks, local OSS readiness, GitHub label taxonomy dry-run, package build, clean
wheel install smoke, and installed CLI smoke from outside the source checkout.

When using a manual Release workflow dispatch for a public launch candidate,
set `public_launch=true` and `publish_pypi=true` together. Keep both false for a
GitHub-only release candidate. The workflow records the selected mode in the
Actions summary and fails fast when `public_launch=true` is selected without
`publish_pypi=true`, because the PyPI trusted-publishing proof point would be
impossible to collect from that run.

After external evidence is attached, use the same release gate in strict public
mode to make the top-level ship/hold decision depend on public proof before the
package build starts:

```bash
uv run python scripts/release_check.py \
  --strict-public \
  --external-evidence-file docs/launch-evidence.json \
  --format json
```

Capture the current launch-readiness summary for a launch issue or draft
release note:

```bash
uv run python scripts/oss_readiness_check.py --format markdown
```

Open or refresh the launch tracking issue from the same check contract:

```bash
uv run python scripts/oss_readiness_check.py --format launch-issue
```

After `docs/launch-evidence.json` exists, include it so already verified proof
renders as checked:

```bash
uv run python scripts/oss_readiness_check.py --external-evidence-file docs/launch-evidence.json --format launch-issue
```

The launch issue also includes a `Missing Evidence Collection Commands` section
for any external check that is still open. Use those missing-only commands as
the next copy-paste actions for attestation, GitHub release installer smoke,
PyPI, PyPI install smoke, hosted MCP, Hosted Beta Smoke, GitHub label, and real
client-session evidence. The
generated collection commands derive `--release-tag` from `pyproject.toml` and
verify the small `install.sh` release asset by default. They include
`--format json` so writing `docs/launch-evidence.json` still prints a
machine-readable probe report when a proof point is not yet verifiable.
Checklist `Next` lines point to those command blocks instead of repeating
shorter commands.

The default readiness check is allowed to report missing external proof while
local evidence is ready. That is useful for tracking launch work, but it is not
a public launch approval.

## 2. Create The Evidence File

Generate the evidence template from the current script contract:

```bash
uv run python scripts/oss_readiness_check.py --write-external-evidence-template docs/launch-evidence.json
```

Then collect the machine-checkable external facts. This command calls GitHub,
PyPI, and `gh label list`; it is intentionally separate from the offline CI and
release gates. Use `--merge-existing` for incremental collection so a later
PyPI, hosted MCP, hosted smoke, or client-session run does not erase evidence
that was already reviewed. A current live label or topic drift probe clears the
older GitHub metadata record instead of preserving stale proof; a requested
GitHub release installer smoke or PyPI install smoke failure clears older
`github_release_install_smoke` or `pypi_install_smoke` proof for the same
reason. GitHub release artifact probe failures, including draft releases,
missing required assets, or invalid release metadata, clear older
`github_release_artifacts` proof instead of preserving stale release evidence.
Replace `v<version>` with the current release tag, or use
`scripts/oss_readiness_check.py --format launch-issue` to generate the
current-version commands directly from `pyproject.toml`:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

For GitHub release artifact evidence, the collector downloads
`RELEASE_MANIFEST.json` and `SHA256SUMS` from the release and requires both
files to list the same expected payload assets with matching SHA-256 digests
before it fills `github_release_artifacts`.
Strict readiness also validates that the `github_release_artifacts` summary
mentions every current expected release asset for the project version. This
keeps older launch evidence from passing after the release artifact contract
changes.

After release assets are present, prove the copied GitHub installer command
from the README reaches the current first-run CLI contract. This opt-in smoke
downloads the published `install.sh`, runs it in a clean `HOME`, and validates
`policynim --help`, primary command help, semantic hosted/local CLI/local MCP
quickstart JSON, support-bundle hosted `client_commands` for Codex and Claude
Code, doctor JSON, and Codex/Claude Code local stdio MCP config JSON:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --github-install-smoke \
  --require-requested-probes \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

After the Release workflow generates artifact attestations, verify one public
release asset before claiming provenance proof. The generated launch issue uses
`install.sh` because it is the smallest public asset covered by the release
checksum file. The collector downloads the named asset with `gh release
download`, runs `gh attestation verify`, and inspects
`verificationResult.statement.subject`. It fills the
`github_artifact_attestations` record only when verification succeeds and the
attestation output includes the selected release asset in
`verificationResult.statement.subject`. Use a standalone bundle name instead
when you are auditing bundle-specific provenance:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --release-attestation-asset-name install.sh \
  --require-requested-probes \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

After the Release workflow publishes to PyPI through the `pypi` environment,
add `--pypi-publish-run-url` with the GitHub Actions run URL. Public PyPI JSON
proves the project/version and must list both expected release files
(`policynim-<version>-py3-none-any.whl` and `policynim-<version>.tar.gz`); the run URL
proves the `publish-pypi` job completed inside the trusted-publishing workflow.
The collector also requires that run's `headSha` match the GitHub release
`targetCommitish`, so stale trusted-publish runs cannot prove the current
release. If the supplied run succeeded overall but `publish-pypi` was skipped
or failed, the collector leaves `pypi_project` blank and reports the job name,
status, and conclusion in the probe output:

Text output mirrors the actionable probe details from JSON.
It includes failed attestation or hosted-service `detail` messages.
The next recovery command stays visible in the same probe output.
Use `--require-requested-probes` when a CI job or release operator supplies a
specific attestation asset, GitHub release installer smoke, PyPI run, PyPI
install smoke, hosted MCP URL, hosted smoke run, or client-session file and
should fail if that requested proof does not verify.
Leave it off for exploratory partial collection.

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --pypi-publish-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>' \
  --require-requested-probes \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

After PyPI lists the release version, prove installability from the public
package channel. This opt-in smoke creates a clean virtualenv, installs
`policynim==<version>` from PyPI, and validates `policynim --help`, primary
command help, semantic hosted/local CLI/local MCP quickstart JSON,
support-bundle hosted `client_commands` for Codex and Claude Code, doctor JSON,
and Codex/Claude Code local stdio MCP config JSON:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --pypi-install-smoke \
  --require-requested-probes \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

When a public hosted MCP deployment exists, add `--hosted-mcp-url` with the
deployed `/mcp` endpoint. The collector checks same-origin `/healthz` and then
verifies `/mcp` rejects an invalid bearer token; it does not need a real hosted
beta token for this domain proof:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --hosted-mcp-url 'https://<railway-domain>/mcp' \
  --require-requested-probes \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

After the secret-gated Hosted Beta Smoke workflow passes, add
`--hosted-smoke-run-url` with the GitHub Actions run URL. The collector verifies
that the run belongs to `Hosted Beta Smoke`, was manually dispatched, completed
successfully, includes a passing `hosted-smoke` job, downloads the
`hosted-smoke-evidence` artifact, and validates
`policynim-hosted-smoke-junit.xml` for the required live MCP checks. The run
must also report a `headSha` matching the GitHub release `targetCommitish`:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --hosted-smoke-run-url 'https://github.com/<owner>/<repo>/actions/runs/<run-id>' \
  --require-requested-probes \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

After Codex or Claude Code connects to the generated hosted or local MCP config,
record the sanitized client-session facts in the shape shown by
`docs/mcp-client-evidence.example.json`. The checked-in example intentionally
leaves `setup_command` and `reference` blank so it cannot be used as real
client-session proof by accident. Generate a launch-notes copy from the same
script contract, fill the sanitized reference after a real client run, and keep
`secrets_included` set to `false`. For hosted HTTP sessions, prefer
`--mcp-client-hosted-url` so the collector generates the Codex or Claude Code
setup command from the verified `/mcp` URL instead of requiring a hand-written
command. For Codex hosted HTTP evidence, that generated command has the same
shape as `codex mcp add policynim --url 'https://<railway-domain>/mcp'
--bearer-token-env-var POLICYNIM_TOKEN`. Use `--mcp-client-setup-command` only
when recording a local `stdio` session or an already-reviewed client command. The
`launch-notes/` directory is ignored by Git so local session records,
screenshots, and operator notes do not get committed by accident:

```bash
uv run python scripts/collect_launch_evidence.py \
  --write-mcp-client-evidence-template launch-notes/codex-mcp-session.json \
  --mcp-client-template-client codex \
  --mcp-client-template-transport hosted-http
```

After the redacted transcript, screenshot, issue, or release-note section exists,
you can create the filled JSON record without hand-editing the template:

```bash
uv run python scripts/collect_launch_evidence.py \
  --write-mcp-client-evidence-record launch-notes/codex-mcp-session.json \
  --mcp-client-template-client codex \
  --mcp-client-template-transport hosted-http \
  --mcp-client-hosted-url 'https://<railway-domain>/mcp' \
  --mcp-client-reference 'launch-notes/codex-mcp-session.md'
```

The record writer can replace its own blank template without `--force`; it still
requires `--force` before replacing a nonblank reviewed record.

The collector accepts the filled file only when it names `codex` or
`claude-code`, uses `hosted-http` or `local-stdio`, includes a secret-safe setup
command matching that client and transport, lists `policy_preflight` and
`policy_search`, records a `policy_preflight` call, includes a non-secret
reference, and sets `secrets_included` to `false`. Placeholder or example
client-session references and setup commands are rejected, including
`github.com/example/...`,
angle-bracket templates, TODO text, or `.invalid` URLs:

```bash
uv run python scripts/collect_launch_evidence.py \
  --release-tag v<version> \
  --mcp-client-evidence-file launch-notes/codex-mcp-session.json \
  --require-requested-probes \
  --write-external-evidence-file docs/launch-evidence.json \
  --merge-existing \
  --format json
```

Review the generated file before using it. The collector fills only evidence it
can verify directly, such as complete GitHub release assets, GitHub artifact
attestation verification for a named release asset, or GitHub labels that match
`.github/labels.yml`. It can fill PyPI evidence when public PyPI JSON matches
the release, lists the expected wheel and sdist, and
`--pypi-publish-run-url` proves the `publish-pypi` job passed from the same
commit as the GitHub release target. It can fill PyPI install smoke evidence
when `--pypi-install-smoke` installs the public package version in a clean
virtualenv and the installed first-run commands return valid output. It can
also fill hosted MCP domain evidence when `--hosted-mcp-url` proves ready
`/healthz` and bearer-token enforcement.
It can fill Hosted Beta Smoke evidence from `--hosted-smoke-run-url` after the
workflow has already run with secrets and its JUnit artifact proves the expected
live MCP checks from the same commit as the GitHub release target. It can fill
real MCP client-session evidence from
`--mcp-client-evidence-file` after a maintainer has reviewed the session record.
It leaves records blank when they still need maintainer proof.
`--merge-existing` validates the existing evidence file, keeps complete records
for checks the current run did not verify, replaces records when the current
run produces a complete verified record, and clears older GitHub metadata proof
when the current live labels or topics probe reports drift. It also clears older
`github_release_install_smoke` or `pypi_install_smoke` proof when the requested
public installer smoke fails because the installed first-run contract is
missing or invalid. Use `--force` only when you want to replace the file from
scratch.

Partial evidence files are valid launch checklists. Blank records do not count
as verified proof; the strict readiness gate reports those checks as
`missing_external` until a maintainer fills every field.

Fill one record for each external proof point. Every record needs these fields:

- `summary`: what passed and why it is enough
- `reference`: the GitHub URL, PyPI URL, workflow run URL, hosted domain, issue,
  release note, or sanitized artifact reference
- `verified_by`: maintainer, release manager, or automation identity
- `verified_at`: timezone-aware ISO timestamp from the last 14 days

Evidence timestamps more than 10 minutes in the future or more than 14 days old
are rejected before strict public readiness can pass. Refresh the collector run
or maintainer review instead of reusing stale launch proof.

Placeholder or example references are rejected. Do not use angle-bracket
template values, `github.com/example/...`, `example.invalid`, or TODO-style
references in the real evidence file.

Reference shapes are also checked before strict public readiness can pass:

- `github_release_artifacts`: GitHub release tag URL.
- `github_release_install_smoke`: GitHub release tag URL.
- `github_artifact_attestations`: GitHub release tag URL with the attested
  asset name after `#`.
- `pypi_project`: GitHub Actions run URL for the Release workflow run that
  completed trusted publishing.
- `pypi_install_smoke`: PyPI project version URL.
- `hosted_mcp_domain`: HTTPS hosted `/healthz` or `/mcp` URL.
- `hosted_beta_live_smoke`: GitHub Actions run URL for the Hosted Beta Smoke
  workflow run.
- `github_labels_applied`: the exact `gh label list --json name,color,description --limit 1000` command.
- `github_topics_applied`: the exact `gh repo view --json repositoryTopics,nameWithOwner` command.
- `real_mcp_client_session`: sanitized session artifact, issue, or release-note
  reference that is not a placeholder.

Keep `docs/launch-evidence.example.json` as the safe checked-in example. The
real evidence file may live in a launch issue, private release workspace,
ignored `launch-notes/` workspace, or a temporary local file if it contains
non-secret operator names or URLs that should not be committed.

## 3. Attach External Proof

Collect the external proof in this order:

1. GitHub release artifacts: draft or published release contains the wheel,
   source distribution, standalone bundles, installers, `SHA256SUMS`, and
   `RELEASE_MANIFEST.json`; the collector downloads `SHA256SUMS` and
   `RELEASE_MANIFEST.json` and confirms the checksum entries match the manifest.
2. GitHub artifact attestation: at least one public release asset, usually the
   small `install.sh` installer for routine launch proof or
   `policynim-v<version>-linux-amd64.tar.gz` for bundle-specific review,
   verifies with `gh attestation verify`. The release workflow uses
   `actions/attest` with `subject-checksums: release-assets/SHA256SUMS`, and
   the collector fills `github_artifact_attestations` after the verification
   command passes and the JSON output includes the selected release asset in the
   attested subjects.
3. GitHub release installer smoke: `--github-install-smoke` downloads the
   published `install.sh`, runs it in a clean `HOME`, and validates `--help`,
   primary command help, semantic hosted/local CLI/local MCP quickstart JSON,
   support-bundle hosted `client_commands` for Codex and Claude Code, doctor
   JSON, and Codex/Claude Code local stdio MCP config JSON.
4. PyPI: publishing state is explicit. Package install docs may point at PyPI
   once the project exists, but public launch proof still needs the project page
   and trusted publishing run evidence for the current tag. Public PyPI JSON
   proves the project, version, wheel, and sdist, but not the repository
   trusted-publishing configuration. The collector requires the trusted-publish
   run `headSha` to match the GitHub release `targetCommitish`, and reports the
   `publish-pypi` job status when that job is missing, skipped, or failed.
5. PyPI install smoke: `--pypi-install-smoke` installs `policynim==<version>`
   from public PyPI in a clean virtualenv and validates `--help`, primary
   command help, semantic hosted/local CLI/local MCP quickstart JSON,
   support-bundle hosted `client_commands` for Codex and Claude Code, doctor
   JSON, and Codex/Claude Code local stdio MCP config JSON.
6. hosted MCP domain: `/healthz` responds on the public domain and `/mcp` uses
   bearer auth without exposing tokens.
7. Hosted Beta Smoke: the secret-gated hosted smoke workflow passed for
   `/healthz`, `policy_search`, and `policy_preflight`, or the release decision
   explicitly records why hosted promotion is skipped. Inspect
   `hosted-smoke-evidence/policynim-hosted-smoke-junit.xml` from the workflow
   run before treating it as launch evidence. The hosted-smoke run `headSha`
   must match the GitHub release `targetCommitish`.
8. GitHub labels and topics: `gh auth status` succeeds from the maintainer
   shell, `scripts/sync_github_labels.py --live --format json` and
   `scripts/sync_github_topics.py --live --format json` were inspected first,
   then `scripts/sync_github_labels.py --apply --format json` and
   `scripts/sync_github_topics.py --apply --format json` ran against the
   repository. `.github/labels.yml` is reflected in GitHub, and
   `.github/topics.yml` matches `gh repo view --json repositoryTopics,nameWithOwner`.
9. real MCP client session: Codex or Claude Code successfully connected to
   hosted MCP or local `stdio`, listed `policy_preflight` and `policy_search`,
   and ran the intended launch demo without leaking secrets.

## 4. Run The Strict Public Gate

Render a paste-ready strict report:

```bash
uv run python scripts/oss_readiness_check.py --strict-public --external-evidence-file docs/launch-evidence.json --format markdown
```

Then run the machine-readable strict gate:

```bash
uv run python scripts/oss_readiness_check.py --strict-public --external-evidence-file docs/launch-evidence.json --format json
```

The JSON output is the final public launch decision. A `public_ready` decision
means the local repository evidence and all external proof records are present.
A `hold_external_missing` decision means the project is still locally ready but
must not claim public launch readiness.

If the launch decision needs public maintainer review, open the
[Public Launch Evidence](../.github/ISSUE_TEMPLATE/public_launch_evidence.yml)
issue form. It applies `type/launch` and `needs/launch-evidence`, and asks for
strict public readiness output, the generated launch issue, attached evidence
records, and remaining external proof in one place.

## 5. Update Public Docs After Hosted Proof Exists

After hosted MCP domain evidence exists, strict public readiness runs
`strict_public_hosted_onboarding_docs`. That check fails if README.md,
docs/agent-workflows.md, examples/codex/README.md, or
examples/claude-code/README.md still point first-run users at
`<railway-domain>` instead of the verified hosted `/mcp` origin.

Update public install and launch wording only for the channels that are actually
verified:

- Keep PyPI install wording aligned with the published package, and keep
  trusted-publishing workflow evidence as a separate strict-gate requirement.
- Replace placeholder hosted MCP domains only after the hosted MCP domain and
  Hosted Beta Smoke evidence exist.
- Publish badges only after they point to stable checks that a contributor can
  inspect.
- Keep `docs/oss-readiness-audit.md`, `docs/release.md`, and
  `docs/roadmap.md` aligned with the evidence file so external claims and
  maintainer gates do not drift.
