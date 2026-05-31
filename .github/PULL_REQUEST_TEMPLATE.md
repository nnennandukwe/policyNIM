## Summary

Describe the CLI, MCP, docs, release, or runtime workflow this changes.

## PR Lane

Choose one primary lane from
[docs/oss-readiness-audit.md#high-value-pr-sequence](../docs/oss-readiness-audit.md#high-value-pr-sequence).
Keep the PR scoped to one user-facing thesis, one primary evidence surface, and
a bounded rollback story.

- [ ] First-run and hosted MCP onboarding
- [ ] Local CLI and MCP verification loop
- [ ] Installability and release trust
- [ ] SQLite migration and storage contract
- [ ] Maintainer trust and public launch proof
- [ ] Other: explain why this does not fit one high-value lane.

## Verification

- [ ] `uv run ruff check .`
- [ ] `uv run pyright`
- [ ] `uv run pytest -q -m "not live and not docker_live"`
- [ ] `uv lock --check`
- [ ] `uv build --out-dir dist`
- [ ] `uv run policynim doctor --format json`
- [ ] `uv run policynim support-bundle`
- [ ] Release-affecting change: `uv run python scripts/release_check.py`
- [ ] Public launch evidence changed or claimed:
      `uv run python scripts/oss_readiness_check.py --format launch-issue`
- [ ] Public launch is claimed ready:
      `uv run python scripts/release_check.py --strict-public --external-evidence-file docs/launch-evidence.json`

## User-Facing Evidence

Include command output or screenshots for changed CLI, MCP, hosted beta, docs,
or release behavior. If a listed check was skipped, explain why. For local MCP
`stdio` changes, include `uv run policynim support-bundle --include-mcp-smoke`.
When CI has run, reviewers can also inspect the `package-smoke-evidence`
artifact; it includes `policynim-mcp-smoke.json`, generated MCP client config,
doctor output, primary command help (`init --help`, `ingest --help`, and
`preflight --help`), support-bundle output, local OSS-readiness JSON, and the
paste-ready launch issue from the clean wheel install.

## Live Or Hosted Checks

- [ ] No live NVIDIA, Docker, hosted MCP, or Railway check is required.
- [ ] Live or hosted check was required and the exact command/result is included.
- [ ] Public launch proof is not being claimed, or strict public readiness returned
      `public_ready`. If it returned `hold_external_missing`, list the remaining
      external proof points instead of presenting the PR as launch-ready.

## Safety

- [ ] No API keys, bearer tokens, generated beta tokens, or private policy
      corpus content are included.
- [ ] Docs match the current command output and install channel state.
