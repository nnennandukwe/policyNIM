# PolicyNIM Support

PolicyNIM support works best when reports include reproducible CLI, MCP,
release, or hosted-beta evidence. Please do not paste API keys, bearer tokens,
hosted beta tokens, private policy content, or private runtime evidence.
`policynim support-bundle` redacts local path prefixes by default for public
issues; use `--include-local-paths` only for private maintainer triage.

## Where To Ask

- Bugs: open a bug report and include `policynim support-bundle` output.
- Local MCP stdio launch issues: run
  `policynim support-bundle --include-mcp-smoke` and include the output.
- Hosted MCP or hosted beta issues: include the hosted URL shape, the failing
  client command, the `/healthz` status when available, and whether the failure
  was `401`, insufficient context, upstream NVIDIA failure, or service
  unavailable. For hosted MCP client setup issues, also include redacted
  `policynim mcp-config --target hosted-http --client codex --hosted-url
  'https://<host>/mcp' --format json` output and the `hosted_url_placeholder`
  value.
- Install or release artifact issues: include the installer command, platform,
  asset name, checksum or `RELEASE_MANIFEST.json` evidence, and exact failure
  output.
- Public launch evidence: use the
  [Public launch evidence](.github/ISSUE_TEMPLATE/public_launch_evidence.yml)
  issue form when strict public readiness, generated launch issue output,
  attached evidence records, or remaining external proof need maintainer review.
- Feature requests: use the feature request template and name the CLI, MCP,
  hosted beta, docs, runtime evidence, installability, or CI/release surface.

Security issues belong in [SECURITY.md](SECURITY.md), not public issues.
Maintainer triage labels and response rules live in
[docs/maintainer-triage.md](docs/maintainer-triage.md).

## Before Opening A Bug

Run the lowest-risk diagnostics first:

```bash
policynim support-bundle
```

For source checkouts, use `uv run`:

```bash
uv run policynim support-bundle
```

Attach `policynim support-bundle` output to public issues. The bundle includes
a `first_run` section with hosted MCP, local CLI, and local MCP quickstart
targets plus each target's `quickstart_command`, so maintainers can route setup
reports without asking for raw quickstart output. Keep raw `policynim doctor --format json` output local unless a maintainer asks for it in a private channel, because raw doctor output can include exact filesystem paths.

If a maintainer asks for exact filesystem paths in a private channel, rerun:

```bash
policynim support-bundle --include-local-paths
```

For MCP stdio issues:

```bash
uv run policynim support-bundle --include-mcp-smoke
```

For hosted MCP client setup issues:

```bash
policynim mcp-config --target hosted-http --client codex --hosted-url 'https://<host>/mcp' --format json
```

If the output reports `"hosted_url_placeholder": true`, replace the placeholder
with the deployed `/mcp` URL before debugging auth, client, or service failures.

## Issue Response Expectations

PolicyNIM is pre-1.0. Maintainer priority goes to:

1. security, secret exposure, token handling, and hosted MCP auth issues
2. install failures, broken release artifacts, and checksum or manifest drift
3. first-run setup failures in `init`, `doctor`, `support-bundle`, and
   `mcp-smoke`
4. deterministic CLI, MCP, runtime evidence, and CI gate regressions
5. feature requests and roadmap discussion

If a report cannot be reproduced from the supplied evidence, maintainers may ask
for a smaller reproduction or a refreshed support bundle.
