# Changelog

All notable PolicyNIM changes are tracked here so release evidence is visible
outside GitHub Actions logs.

## [Unreleased]

- No unreleased changes yet.

## [0.1.1]

- Keep public launch evidence collection strict until PyPI, hosted MCP, hosted
  smoke, and real MCP client-session proof are attached.
- Add `scripts/release_check.py --strict-public` so public launch decisions can
  fail on missing external evidence before the package build smoke starts.
- Require PyPI trusted-publishing evidence to come from the same commit as the
  GitHub release target before `pypi_project` proof is accepted.
- Add strict public launch evidence for clean PyPI install smoke so public
  readiness proves the package installs and first-run CLI commands execute from
  the public channel.
- Extend that public PyPI install smoke to cover primary command help, all
  first-run quickstart targets, support-bundle JSON, doctor JSON, and local MCP
  config JSON from the installed package.
- Clarify public PyPI install docs so package availability is not mistaken for
  a launch-ready first-run path before `pypi_install_smoke` passes.
- Require Hosted Beta Smoke evidence to come from the same commit as the GitHub
  release target before hosted-smoke proof is accepted.
- Reject stale external launch evidence when `verified_at` is more than 14 days
  old or more than 10 minutes in the future.
- Reject strict-public evidence records whose references point at the wrong
  proof surface, such as a PyPI page where a trusted-publishing run URL is
  required.
- Make hosted `policynim quickstart` validate `/mcp` URLs and derive the
  same-origin `beta_portal_url` when a real hosted endpoint is supplied.
- Keep hosted `policynim quickstart` on the direct installed CLI entrypoint so
  the no-clone MCP path does not leak source-checkout `uv run` commands.
- Add the same hosted `beta_portal_url` metadata to `mcp-config --format json`
  so Codex and Claude Code setup evidence names both the portal and MCP route.
- Validate clean-wheel Codex and Claude Code `mcp-config --format json`
  contracts in release gates so installed local stdio config cannot regress to
  source-checkout launches and hosted config stays placeholder/env-var safe.
- Add `policynim mcp-smoke --mcp-config-file` and wire it into CI/release smoke
  so generated Codex and Claude Code local stdio configs prove a list-tools
  handshake before users paste them into clients.
- Run standalone release smoke from an empty cwd and apply the same MCP config
  contracts so packaged binaries prove no-clone Codex and Claude Code setup.
- Clarify GitHub label/topic sync recovery when `gh` is missing or
  unauthenticated so maintainers rerun `--live` before explicit `--apply`.
- Redact local path prefixes in `policynim support-bundle` by default and add
  `--include-local-paths` for private maintainer triage.
- Add a support-bundle `first_run` summary with hosted MCP, local CLI, and local
  MCP quickstart commands so public issues include safe setup-routing context.
- Validate the clean-wheel support-bundle first-run contract in the release gate
  so installed issue diagnostics cannot regress to checkout-only commands.
- Add installed `policynim init --help` smoke to CI, release, and standalone
  evidence artifacts so first-run setup help cannot regress silently.
- Extend first-run command help smoke to `policynim ingest --help` and
  `policynim preflight --help` so indexing and verification entrypoints cannot
  disappear from clean installs silently.
- Extend that support-bundle release gate to validate each target's
  `quickstart_command` field, not only the generated follow-up commands.
- Report missing or skipped `publish-pypi` job details from launch evidence
  collection without accepting PyPI trusted-publishing proof.
- Show probe failure `detail` fields in text launch-evidence output so
  attestation and live-service failures are actionable without parsing JSON.
- Add `collect_launch_evidence.py --require-requested-probes` so release
  automation can fail when a supplied external proof input does not verify.
- Validate launch-issue collection commands in the release gate so external
  proof snippets keep `--require-requested-probes` before packaging starts.
- Add an explicit Release workflow public-launch mode that requires
  `publish_pypi=true` before a manual run can be treated as public launch
  evidence.
- Add `.github/topics.yml` and `scripts/sync_github_topics.py` so repository
  discoverability metadata has the same dry-run/apply workflow as labels.
- Add strict external launch evidence for GitHub repository topics so public
  discoverability metadata must match `.github/topics.yml`.
- Add a validated MCP client-session evidence record writer so maintainers can
  create collector-ready real-client proof without hand-editing JSON.
- Verify the small `install.sh` release asset attestation inside the Release
  workflow and retain review-only `release-attestation-evidence` before draft
  release creation.
- Derive launch-issue evidence commands from `pyproject.toml` so release tags
  stay current after version bumps, and default attestation proof to the small
  `install.sh` release asset for faster routine evidence collection.
- Replace hard-coded release versions in the public launch runbook's manual
  proof commands with `v<version>` placeholders and point maintainers to the
  generated launch issue for exact current-version commands.
- Ignore `launch-notes/` by default so real MCP client-session evidence,
  screenshots, and operator notes are kept out of commits unless a maintainer
  intentionally publishes sanitized proof elsewhere.
- Add public-launch evidence prompts to the PR template so launch claims include
  the generated launch issue and strict public release gate result instead of
  relying on the local release check alone.
- Add a public launch evidence issue form and `type/launch` label so maintainers
  can review strict public readiness output, generated launch issue text,
  attached evidence records, and remaining external proof in one routed issue.
- Link the public launch evidence form from SUPPORT.md, the docs index, and the
  public launch runbook so launch proof review has a discoverable intake path.
- Add a `needs/launch-evidence` triage label and maintainer guidance for issues
  or PRs that cannot claim public readiness until strict external launch proof
  is attached.
- Add live dry-run modes for GitHub label and topic sync so maintainers can
  inspect the authenticated GitHub delta before applying taxonomy changes.
- Expand PyPI keywords and README trust badges for the MCP + CLI verification
  positioning without claiming hosted health or strict public readiness.
- Continue hardening first-run CLI, MCP client setup, release gates, and
  maintainer trust workflows before broad public launch claims.

## [0.1.0]

- Added OSS readiness gates for local repository health, public-launch evidence,
  GitHub label taxonomy, CODEOWNERS ownership, and maintainer triage.
- Added no-clone CLI first-run workflows with `policynim quickstart`,
  `policynim doctor`, `policynim support-bundle`, and installed wheel smoke
  coverage.
- Added MCP setup and verification flows for hosted HTTP and local stdio,
  including Codex and Claude Code config generation plus `mcp-smoke` tool-list
  checks.
- Added release gate coverage for Ruff, Pyright, offline pytest, clean wheel
  install smoke, release manifests, checksums, installers, and standalone
  bundles.
- Added GitHub release notes generated from the checked versioned changelog
  section.
- Added public docs for contributor setup, release evidence, hosted beta
  operations, support routing, security reporting, roadmap, and OSS readiness.
