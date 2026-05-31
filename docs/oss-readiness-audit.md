# PolicyNIM OSS Readiness Audit

This audit tracks the current developer journey for making PolicyNIM credible as
an open-source MCP and CLI verification tool. It is intentionally operational:
each path names what a new user or maintainer tries first, what should prove it
works, and which follow-up priorities make the project easier to trust.

## Current Developer Journey

### Hosted MCP first run

Audience: agent users who want value without cloning the repository.

Expected path:

1. Open the hosted beta portal.
2. Generate or rotate a hosted API key.
3. Add the hosted `/mcp` endpoint to Codex or Claude Code.
4. Call `policy_preflight` for the main workflow or `policy_search` for raw
   retrieval/debugging.

Evidence today:

- README and client examples are hosted-first.
- Browser visits to the hosted `/mcp` URL route unauthenticated humans to
  `/beta` for token creation, while MCP clients keep protocol/auth responses.
- `policynim mcp-config --target hosted-http` emits Codex and Claude Code
  hosted setup commands with secret-safe `POLICYNIM_TOKEN` references and
  same-origin `beta_portal_url` metadata in JSON output.
- Hosted operations docs include `/healthz`, token, insufficient-context,
  upstream-failure, and service-unavailable recovery notes.
- Opt-in hosted live tests cover tool listing, `policy_search`, and
  `policy_preflight`.

Main gap:

- Public examples still require a real hosted domain supplied by the operator,
  so public onboarding is only as strong as the current deployment and token
  issue flow.

### Installed CLI first run

Audience: users who want a local CLI without a source checkout.

Expected path:

1. Install from the GitHub release installer.
2. Run `policynim --help`.
3. Run `policynim quickstart` to choose hosted MCP, local CLI, or local MCP.
4. Run `policynim doctor --format json`.
5. Run `policynim init`.
6. Run `policynim ingest`.

Evidence today:

- Install docs present PyPI package installs for Python-managed CLI users and
  GitHub release installers for standalone no-clone installs.
- `policynim quickstart` prints hosted MCP, local CLI, and local MCP first-run
  paths without writing config, launching MCP, or calling external services. For
  hosted MCP, it validates that `--hosted-url` points at `/mcp` and derives the
  same-origin `beta_portal_url` when a real hosted URL is supplied. Quickstart
  JSON also includes hosted `client_commands` plus copyable `agent_workflows`
  prompts for `policy_preflight`, `policy_search`, and MCP tool-list
  verification.
- `policynim doctor --format json` reports standalone setup state without
  calling NVIDIA-hosted APIs or printing secret values.
- The CI package job builds the wheel, installs it into a clean virtualenv, and
  runs the installed CLI from an empty temporary working directory, including
  `policynim init --help`, `policynim ingest --help`, and
  `policynim preflight --help` before setup writes any config.
- Release wheel smoke runs the same installed-entrypoint checks before release
  artifacts continue.

Main gap:

- Trusted-publishing run evidence, release artifact availability, and clean
  public install-channel smokes still need to be confirmed before public docs can
  claim full public-launch provenance.

### Source checkout contributor path

Audience: contributors changing code, docs, MCP behavior, or release gates.

Expected path:

1. Run `uv sync --group test --group dev`.
2. Run `uv run policynim init` or copy `.env.development.example`.
3. Run `uv run policynim doctor --format json`.
4. Run `uv run policynim ingest` when exercising retrieval workflows.
5. Run the offline gates before opening a pull request:

```bash
uv run ruff check .
uv run pyright
uv run pytest -q -m "not live and not docker_live"
uv lock --check
uv build --out-dir dist
uv run policynim doctor --format json
```

Evidence today:

- CONTRIBUTING.md, the PR template, release docs, and CI all use the same
  offline pytest marker expression.
- Live NVIDIA, hosted MCP, and Docker checks are opt-in and documented
  separately.
- Docs parity tests cover setup, install, hosted onboarding, and maintainer
  metadata.
- `scripts/release_check.py` turns the local deterministic gates and clean wheel
  smoke into one executable ship/hold check with machine-readable JSON output.
  The clean wheel smoke now checks `init --help`, `ingest --help`,
  `preflight --help`, hosted, local CLI, and local MCP quickstart JSON output
  before MCP stdio/config smokes. It also renders
  the `--format launch-issue` output before building artifacts so the public
  launch handoff remains covered by the local release gate.
- CI uploads `package-smoke-evidence` for PR reviewers with the local readiness
  JSON, paste-ready launch issue, primary CLI help output, all first-run quickstart targets,
  `agent_workflows`, `doctor`, `support-bundle`, MCP stdio smoke, local
  Codex and Claude Code MCP config, and hosted HTTP config JSON.

Main gap:

- Public releases still need actual GitHub artifact evidence, PyPI
  trusted-publishing evidence, and clean public PyPI install smoke before
  public docs can claim stable launch provenance.

### MCP local fallback

Audience: Codex and Claude Code users who need local `stdio` instead of the
hosted HTTP endpoint. This includes installed CLI users who do not want a clone
and contributors who need source-checkout behavior.

Expected path:

1. Install the CLI, or sync dependencies in a source checkout.
2. Set `NVIDIA_API_KEY` in the shell or checkout `.env`.
3. Run `policynim doctor` or `uv run policynim doctor`.
4. Run `policynim ingest` or `uv run policynim ingest`.
5. Run `policynim mcp-smoke` or `uv run policynim mcp-smoke`.
6. Run `policynim mcp-config --target local-stdio --client codex` or
   `policynim mcp-config --target local-stdio --client claude-code` for an
   installed CLI.
7. Save the generated local config JSON and run
   `policynim mcp-smoke --mcp-config-file <config.json> --format json` to prove
   the config can launch the local stdio server.
8. For a source checkout, run `uv run policynim mcp-config --client codex
   --repo-root /ABS/PATH/TO/policyNIM` or the Claude Code equivalent.
9. Configure the client to launch either `policynim mcp --transport stdio` or
   `uv run --directory /ABS/PATH/TO/policyNIM policynim mcp --transport stdio`.

Evidence today:

- Codex and Claude Code examples show hosted MCP first and local `stdio`
  fallback second.
- `policynim doctor` prints MCP launch hints without starting a server or
  calling hosted APIs.
- `policynim mcp-smoke` launches the local `stdio` server and verifies that
  `policy_preflight` and `policy_search` are listed without calling either tool.
- `policynim mcp-config` emits local Codex/Claude Code `stdio` config snippets
  for installed no-clone launches and source-checkout launches, with
  secret-safe `NVIDIA_API_KEY` references.
- `policynim mcp-smoke --mcp-config-file <config.json>` launches from generated
  Codex or Claude Code local stdio config JSON, while rejecting hosted HTTP
  configs so local smoke is not mistaken for hosted proof.
- MCP tests cover typed payloads, auth wrapping, health behavior, failure
  classification, and service cleanup.

Main gap:

- The local checks prove config generation, generated-config stdio launch, and
  tool registration, but they still do not launch a real Codex or Claude Code
  client session end to end.

### Release and CI trust path

Audience: maintainers deciding whether a PR or release is safe to merge/publish.

Expected path:

1. CI runs lint, Pyright, offline tests, lockfile check, build, and wheel smoke.
2. Release workflow repeats offline verification before producing artifacts.
3. Hosted and Docker checks remain manually triggered and secret-gated.
4. Hosted Beta Smoke preserves review-only live evidence for launch decisions.
5. PRs include exact user-facing evidence for CLI, MCP, docs, or release
   behavior changes.

Evidence today:

- CI uses pinned GitHub Actions and `ubuntu-24.04`.
- Offline CI blanks `NVIDIA_API_KEY` for package smoke and excludes live and
  Docker markers.
- Release workflow runs wheel smoke from a clean install location.
- Package and release wheel smoke parse hosted HTTP MCP config JSON and local
  `stdio` config JSON without calling a live hosted endpoint.
- Hosted Beta Smoke uploads review-only `hosted-smoke-evidence` with
  `policynim-hosted-smoke-junit.xml` so launch reviewers can inspect live
  check names and pass/fail status without exposing bearer tokens.
- `scripts/release_check.py` lets a maintainer reproduce the local ship/hold
  decision before tagging. It runs the OSS-readiness JSON check and the
  launch-issue renderer plus GitHub label taxonomy dry-run before package
  artifacts are built, and it validates that launch-issue external proof
  commands keep `--require-requested-probes`. When the
  release decision is also a public launch decision, `--strict-public
  --external-evidence-file docs/launch-evidence.json` adds the strict public
  launch evidence gate before the build smoke starts.
  The clean-wheel smoke validates `init --help`, `ingest --help`,
  `preflight --help`, quickstart `agent_workflows`, and the support-bundle first-run contract
  so public issue diagnostics from an installed wheel cannot
  regress to source-checkout `uv run` or `--repo-root` guidance.
  It also validates Codex and Claude Code `mcp-config --format json` contracts:
  installed local stdio setup must launch the `policynim` entrypoint without a
  source checkout, while hosted setup must keep placeholder URLs and token
  environment variables out of captured config evidence.
  Standalone release smoke runs the packaged binary from an empty cwd and applies
  those same MCP config contracts before release assets can be published, so
  standalone evidence proves no-clone setup instead of accidentally proving a
  source checkout path.
- `scripts/check_release_notes.py` verifies that `CHANGELOG.md` contains
  `Unreleased` notes and a section for the current `pyproject.toml` package
  version before the release gate builds artifacts. The release workflow uses
  `--write-github-release-notes` so draft GitHub release notes come from the
  same checked changelog section.
- `scripts/oss_readiness_check.py` separates local launch readiness from
  external public-launch proof. Its default JSON decision is
  `local_ready_external_missing` when local evidence is present but GitHub
  release, PyPI, public PyPI install smoke, hosted MCP, label-apply, and real
  client-session evidence are still missing; `--strict-public` returns
  `hold_external_missing` until those proof points are attached through
  `--external-evidence-file`. The safe
  template is `docs/launch-evidence.example.json`, where each proof point has
  `summary`, `reference`, `verified_by`, and `verified_at` fields. The template
  contains empty values so it cannot accidentally claim public readiness, and
  `--write-external-evidence-template` generates the same shape for a launch
  issue or release note. Evidence records require timezone-aware `verified_at`
  values from the last 14 days; timestamps more than 10 minutes in the future,
  stale evidence, and placeholder references are rejected before strict public
  readiness can pass. Reference shapes are validated too: release and
  attestation proof must point at a GitHub release tag URL, PyPI and hosted
  smoke proof must point at a GitHub Actions run URL, hosted MCP proof must
  point at an HTTPS `/healthz` or `/mcp` URL, and label proof must use the
  exact `gh label list --json name,color,description --limit 1000` command.
  `--format markdown` renders the current readiness
  state as a paste-ready release-note section, while `--format launch-issue`
  renders a checkbox-based launch issue for remaining external proof.
- `scripts/collect_launch_evidence.py` is the opt-in bridge from live external
  state to the evidence file. It checks GitHub release assets, GitHub labels,
  downloaded `RELEASE_MANIFEST.json` and `SHA256SUMS` metadata consistency,
  optional `--release-attestation-asset-name` provenance facts through
  `gh attestation verify` plus attested-subject parsing that must include the
  selected release asset, public PyPI project/version and wheel/sdist facts, and
  optional `--pypi-publish-run-url` Release workflow facts before filling PyPI
  evidence. It can run `--github-install-smoke` to download the published GitHub
  `install.sh`, install into a clean `HOME`, and validate install.sh guidance
  plus the no-clone first-run contract before filling
  `github_release_install_smoke` evidence. It can also
  run `--pypi-install-smoke` to install the public package version in a clean
  virtualenv and validate installed `--help`, primary command help, semantic
  hosted/local CLI/local MCP quickstart JSON, support-bundle hosted
  `client_commands`, support-bundle `hosted_url`/`beta_portal_url` token flow,
  doctor JSON, and local MCP config JSON output before filling install smoke evidence.
  PyPI trusted-publishing evidence is accepted only when the run `headSha`
  matches the GitHub release `targetCommitish`.
  It can check optional `--hosted-mcp-url` health/auth facts and verify a
  supplied `--hosted-smoke-run-url` through `gh run view`, `gh run download`,
  and the retained hosted-smoke JUnit artifact before filling Hosted Beta Smoke
  evidence. Hosted-smoke evidence is accepted only when the run `headSha`
  matches the GitHub release `targetCommitish`. It can validate reviewed Codex
  or Claude Code session
  records from `--mcp-client-evidence-file` against
  `docs/mcp-client-evidence.example.json` before filling real client-session
  evidence. The checked-in example leaves `setup_command` and `reference` blank
  so it documents the shape without counting as real proof; maintainers can
  generate the same safe placeholder with
  `--write-mcp-client-evidence-template` before filling a real sanitized
  session reference. For hosted HTTP evidence, `--mcp-client-hosted-url`
  derives the secret-safe Codex or Claude Code setup command from the verified
  hosted `/mcp` URL; `--mcp-client-setup-command` remains available for local
  `stdio` sessions and reviewed custom client commands. The generated
  `launch-notes/` workspace is ignored by Git so
  sanitized transcripts, screenshots, and operator notes do not get committed
  by accident. Placeholder or example client-session references and setup commands are rejected before the collector fills
  `real_mcp_client_session` evidence. It
  fills only records it can verify directly. Incremental runs should use
  `--merge-existing` so complete reviewed records survive later partial
  evidence collection, while current live label or topic drift clears older
  GitHub metadata proof and requested GitHub release installer smoke or PyPI
  install smoke failures clear older `github_release_install_smoke` or
  `pypi_install_smoke` proof instead of preserving stale evidence.
  Release-enforcement runs should add `--require-requested-probes` so
  explicitly supplied attestation, PyPI, hosted MCP, hosted-smoke, or
  client-session proof fails the command when it does not verify; `--force` is
  reserved for replacing the evidence file from scratch.
- docs/public-launch-runbook.md turns the external evidence contract into an
  ordered maintainer workflow for GitHub release artifacts, GitHub artifact
  attestation proof, GitHub release installer smoke, PyPI state, hosted MCP
  domain proof, Hosted Beta Smoke output, GitHub labels/topics, and real MCP
  client session evidence. The
  generated launch issue commands derive the release tag from `pyproject.toml`
  and use `install.sh` as the default attestation asset so copy-paste proof
  commands stay current after version bumps and avoid downloading the largest
  standalone bundle for routine launch proof.
- Release workflow generates `RELEASE_MANIFEST.json` and `SHA256SUMS` from the
  same asset directory, then fails when expected public assets are missing.
- Release workflow uploads review-only `release-wheel-smoke-evidence` and
  `standalone-smoke-evidence-*` artifacts with primary CLI help, first-run JSON
  output, standalone MCP stdio smoke, and standalone local stdio MCP config evidence,
  while the draft GitHub release job downloads only the public install
  payload artifacts.
- Release workflow records whether a manual dispatch is a GitHub-only release
  candidate or public launch candidate. Public launch mode requires
  `public_launch=true` and `publish_pypi=true` together, so a skipped
  `publish-pypi` job cannot be mistaken for PyPI trusted-publishing evidence.
- Release workflow generates GitHub artifact attestations with `actions/attest`
  and `subject-checksums: release-assets/SHA256SUMS`, verifies the small
  `install.sh` release asset with `gh attestation verify`, and uploads review-only
  `release-attestation-evidence`, so users can verify downloaded asset
  provenance with GitHub artifact attestations.
- Unix and PowerShell installers keep checksum verification as the default hard
  gate and support opt-in install-time provenance verification with
  `POLICYNIM_VERIFY_ATTESTATION=1` and GitHub CLI before extraction.
- README publishes bounded public README badges for CI, PyPI package metadata,
  Python versions, GitHub release metadata, license, and NVIDIA NIM without
  claiming hosted MCP health or strict public readiness before external launch
  evidence is attached.
- Strict public readiness includes `strict_public_hosted_onboarding_docs` after
  hosted MCP domain proof exists. The README, agent workflow guide, Codex
  example, and Claude Code example must publish the same verified hosted `/mcp` origin
  before the project can claim the hosted setup path is self-serve.
- PR template asks for command output, live-check status, a launch-issue
  rendering when public launch evidence changed, and the strict public
  `release_check.py --strict-public --external-evidence-file
  docs/launch-evidence.json` result before any PR claims `public_ready`.

Main gap:

- Hosted health and public-ready badge/status evidence should stay out of the
  README until those channels have stable, externally verified proof.

### Maintainer trust path

Audience: outside contributors, issue reporters, and security reporters.

Expected path:

1. Issues collect affected surface, reproduction steps, `policynim
   support-bundle` output, and live/hosted-service status.
2. Feature requests name the blocked workflow and verification approach.
3. PRs include deterministic checks and user-facing evidence.
4. Security issues route privately and avoid secrets in public logs.

Evidence today:

- Structured bug and feature templates are present.
- A Public launch evidence issue form routes strict public readiness output,
  generated launch issue text, attached evidence records, and remaining
  external proof under `type/launch` and `needs/launch-evidence`.
- `.github/labels.yml` defines a public triage taxonomy for issue type,
  priority, affected surface, evidence needs, and external blockers.
- The taxonomy includes `needs/launch-evidence` so PRs or issues that claim
  public launch readiness can stay visibly blocked until strict public launch
  evidence is complete.
- `scripts/sync_github_labels.py` parses the taxonomy offline by default,
  supports `--live` for a non-mutating authenticated GitHub diff, and applies
  changes only with `--apply --format json`. Missing or unauthenticated `gh`
  failures point maintainers back through `gh auth status`, `--live --format
  json` inspection, and explicit `--apply --format json` mutation.
- `.github/topics.yml` defines the public repository topic taxonomy for MCP,
  CLI, NVIDIA NIM, policy preflight, Python, and verification discoverability.
- `scripts/sync_github_topics.py` parses the taxonomy offline by default,
  supports `--live` for a non-mutating authenticated GitHub diff, and applies
  changes only with `--apply --format json`. Missing or unauthenticated `gh`
  failures point maintainers back through `gh auth status`, `--live --format
  json` inspection, and explicit `--apply --format json` mutation.
- GitHub label taxonomy apply evidence is tracked as `github_labels_applied`.
  GitHub label taxonomy apply evidence remains external launch proof until
  `scripts/sync_github_labels.py --apply --format json` has run from an
  authenticated maintainer session and the exact
  `gh label list --json name,color,description --limit 1000` reference is attached.
- GitHub topic taxonomy apply evidence is tracked as `github_topics_applied`;
  GitHub topic taxonomy apply evidence remains external launch proof until
  `scripts/sync_github_topics.py --apply --format json` has run from an
  authenticated maintainer session and the exact
  `gh repo view --json repositoryTopics,nameWithOwner` reference is attached.
  Until that proof exists, the default readiness check reports
  `github_labels_applied` and `github_topics_applied` as `missing_external`
  even though the local taxonomy files and dry-run scripts pass.
- `.github/CODEOWNERS` maps release automation, installers, CLI, MCP, security,
  and dependency-update configuration to explicit owner review.
- `.github/dependabot.yml` opens bounded weekly update PRs for the `uv`
  dependency graph and GitHub Actions with maintainer triage labels attached.
- docs/maintainer-triage.md maps those labels to evidence requests and response
  expectations.
- `policynim support-bundle` collects version, Python, platform, a first-run target summary, `doctor`, and optional local MCP smoke output without
  printing configured secret values. The first-run summary includes hosted MCP,
  local CLI, and local MCP `quickstart_command`, hosted `hosted_url`,
  `beta_portal_url`, Codex and Claude Code hosted `client_commands`, and token-creation steps, plus
  `agent_workflows` entries so maintainers can route setup reports without asking for raw quickstart output. Local path prefixes
  are redacted by default for public issues, with `--include-local-paths`
  reserved for private maintainer triage.
- SUPPORT.md routes bugs, MCP stdio issues, hosted MCP issues, release artifact
  issues, and security reports to the right evidence path.
- CODE_OF_CONDUCT.md sets project-specific community standards around review,
  secret safety, and enforcement.
- docs/roadmap.md separates Now, Next, Later, and Not committed yet work so the
  project can be ambitious without over-claiming current support.
- SECURITY.md documents private reporting and secret redaction.
- CONTRIBUTING.md sets an evidence-backed review bar.

Main gap:

- The label taxonomy still needs refinement after real external issue flow
  shows which buckets contributors actually use.

## High-Value PR Sequence

Use this sequence when turning the current OSS-readiness work into reviewable
pull requests. Each PR should have one user-facing thesis, one primary evidence
surface, and a bounded rollback story.

1. **First-run and hosted MCP onboarding.**
   Scope: README hosted MCP quickstart, `policynim quickstart`, hosted
   `mcp-config`, hosted beta portal token commands, and Codex/Claude Code
   examples. Evidence: hosted and local quickstart JSON, docs parity tests, and
   the `package-smoke-evidence` artifact showing hosted placeholder config does
   not require a checkout or embed bearer tokens.
2. **Local CLI and MCP verification loop.**
   Scope: `policynim doctor`, `support-bundle`, `mcp-smoke`, generated-config
   local stdio smoke, and recovery guidance for missing credentials, missing
   runtime artifacts, invalid SQLite index files, and stale legacy index paths.
   Evidence: focused CLI tests, MCP smoke JSON, support-bundle output, and
   `uv run python scripts/release_check.py --format json`.
3. **Installability and release trust.**
   Scope: PyPI/installer docs, macOS Apple Silicon and Intel standalone
   support, release manifest, checksums, attestation hooks, clean wheel smoke,
   and standalone smoke evidence. Evidence: installer contract tests,
   package-release metadata tests, release workflow smoke artifacts, and a
   `ship` decision from `scripts/release_check.py`.
4. **SQLite migration and storage contract.**
   Scope: sqlite-vec storage, factory wiring, removed LanceDB docs and tests,
   local index path diagnostics, and runtime services that depend on the index.
   Evidence: sqlite storage tests, ingest/search round-trip tests, docs parity
   checks proving public docs no longer mention the retired backend, and a
   source-checkout `policynim doctor --format json` run that reports actionable
   recovery for legacy directory paths.
5. **Maintainer trust and public launch proof.**
   Scope: issue templates, PR template, CODEOWNERS, labels/topics, sync scripts,
   launch evidence collector, public launch runbook, and strict public readiness
   gate. Evidence: `oss_readiness_check.py --format json`,
   `oss_readiness_check.py --format launch-issue`, label/topic dry-runs, and
   external evidence records only when GitHub release, PyPI, hosted MCP, hosted
   smoke, and real client-session proof actually exist.

Do not combine these into one review unless the reviewer explicitly asks for a
single large integration PR. The project is more credible when each PR proves a
developer workflow end to end and leaves public-launch claims blocked on
external evidence until the strict gate can pass.

## Prioritized Improvements

### P0: Keep The First Run Honest

- Maintain `policynim doctor --format json` as the first diagnostic for
  installed and source-checkout users.
- Maintain `policynim quickstart` as the no-network first-run path selector for
  hosted MCP, local CLI, and local MCP workflows.
- Maintain `policynim support-bundle` as the issue-ready diagnostic that wraps
  first-run state and optional MCP launch evidence.
- Keep README, contributor docs, PR template, and CI aligned on the same offline
  gate commands.
- Keep package smoke running the installed CLI outside the repository checkout.

### P1: Make MCP Verification Self-Serve

- Keep `policynim mcp-smoke` as the deterministic local MCP handshake that can
  list `policy_preflight` and `policy_search` without a manual Codex or Claude
  Code session.
- Keep `policynim mcp-config` aligned with the checked-in Codex and Claude Code
  examples so users do not hand-copy stale hosted HTTP or local `stdio` config.
- Document the exact recovery path when `stdio` launch fails because `uv`, the
  repo path, credentials, or the local index are missing.

### P1: Make Release Readiness Binary

- Keep release docs framed as a ship or hold checklist with required command
  output, artifact checksums, installer smoke, and hosted beta decision notes.
- Keep `scripts/release_check.py` aligned with the release guide so maintainers
  can attach one local ship/hold result before tagging.
- Keep `scripts/oss_readiness_check.py` attached to launch decisions so
  maintainers can show local readiness without claiming external proof too
  early, then move to public-ready status only from an explicit
  `--external-evidence-file` record.
- Keep `scripts/collect_launch_evidence.py` opt-in so live GitHub/PyPI publish
  run, public PyPI install smoke, hosted MCP URL/run, and client-session checks
  help fill launch evidence without entering offline CI or release gates; use
  `--require-requested-probes` when a release job should fail on a supplied
  proof input that does not verify.
- Keep `RELEASE_MANIFEST.json` as the machine-readable release artifact contract.
- Keep PyPI guidance honest: package installs are documented, while strict
  public readiness still requires the current wheel, sdist, trusted publishing
  configuration, successful publish run, and clean PyPI install smoke to be
  attached as evidence.

### P2: Grow Contributor Trust Signals

- Keep the `.github/labels.yml` taxonomy and docs/maintainer-triage.md aligned
  with real issue volume.
- Keep `.github/topics.yml`, `pyproject.toml` keywords, and README positioning
  aligned with the actual MCP + CLI verification surface.
- Keep `.github/CODEOWNERS` and `.github/dependabot.yml` aligned with real
  release, installer, MCP, and dependency ownership risk.
- Run `scripts/sync_github_labels.py --live` before broad public issue triage,
  then `--apply` only from an authenticated maintainer session, and keep a live
  dry-run result attached to maintainer release notes when labels change.
- Run `scripts/sync_github_topics.py --live` before broad public promotion,
  then `--apply` only from an authenticated maintainer session, and keep the
  live dry-run result attached when topic taxonomy changes.
- Keep public status badges only when they reflect stable, meaningful checks.
- Keep SUPPORT.md, CODE_OF_CONDUCT.md, and docs/roadmap.md aligned with actual
  maintainer capacity and verified release channels.
- Keep limitations explicit so the project earns trust through bounded claims
  rather than broad positioning.
