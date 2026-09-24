# PolicyNIM Roadmap

This roadmap describes the current public direction without promising dates.
PolicyNIM should earn adoption through reliable install paths, useful first-run
diagnostics, verifiable MCP workflows, deterministic release gates, and clear
maintainer expectations.

## Now

- Keep the hosted-first MCP examples current for Codex and Claude Code.
- Keep `policynim quickstart`, `policynim doctor`, `policynim support-bundle`,
  and `policynim mcp-smoke` aligned with the real first-run experience.
- Keep generated-config MCP smoke working so local Codex and Claude Code stdio
  configs can be handshaked before users paste them into a client.
- Keep CI offline by default with Ruff, Pyright, live/Docker marker exclusion,
  package build, clean wheel install smoke, and release manifest checks.
- Keep GitHub release artifacts tied to `SHA256SUMS` and
  `RELEASE_MANIFEST.json`.
- Keep `scripts/oss_readiness_check.py` green so maintainers can distinguish
  local readiness from missing external launch proof.
- Keep `.github/CODEOWNERS` and `.github/dependabot.yml` aligned with release,
  installer, MCP, and CI ownership risk.
- Keep `scripts/sync_github_labels.py` as the offline dry-run, live dry-run,
  and apply path for label taxonomy maintenance.
- Keep `.github/topics.yml` and `scripts/sync_github_topics.py` aligned with
  the MCP + CLI verification positioning before public promotion, with live
  dry-run checks before apply.
- Keep PyPI package installs documented while treating trusted-publishing
  evidence and clean public install smoke as separate public-launch proof
  points.

## Next

- Publish and verify the first public GitHub release artifact set.
- Run `scripts/oss_readiness_check.py --strict-public --format json` before the
  first broad public launch, and attach the resulting external proof status to
  the launch issue or release notes using `docs/public-launch-runbook.md`.
- Attach PyPI trusted-publishing evidence for the current tag so package
  availability and release-workflow provenance are both reviewable.
- Attach clean PyPI install smoke evidence for the current tag so the public
  package path proves first-run CLI behavior.
- Tighten Hosted beta smoke evidence so `/healthz`, `policy_search`, and
  `policy_preflight` promotion status is easy to attach to release decisions.
- Refine the applied `.github/labels.yml` taxonomy after the first external
  issue flow shows which buckets contributors actually use.
- Refine the applied `.github/topics.yml` taxonomy after search, install, and
  issue traffic shows which discoverability channels are working.

## Later

- Broaden the sample policy corpus without weakening citation grounding.
- Keep README's bounded public metadata badges current, and add hosted health and
  public-ready badges only when they reflect stable, meaningful checks.
- Expand maintained client examples beyond Codex and Claude Code when there is
  evidence of real user demand.
- Add observability around hosted MCP usage, upstream provider failures, and
  first-run support-bundle patterns.

## Not committed yet

- Enterprise policy corpus hosting.
- A hosted multi-tenant admin console beyond the current beta portal.
- A default-on live NVIDIA CI gate.
- A full compatibility matrix for every MCP client.
- A release date for 1.0.
