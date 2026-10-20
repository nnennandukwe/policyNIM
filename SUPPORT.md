# PolicyNIM support

For reproducible bugs, use the bug report form. For a feature request, describe the
workflow, expected result and accepted [roadmap](docs/roadmap.md) scope. Security
issues use the private route in [SECURITY.md](SECURITY.md).

## Gather safe diagnostics

Start with:

```bash
policynim support-bundle
```

In a source checkout, prefix commands with `uv run`. Review the bundle before
posting: automatic redaction isn't a guarantee that every private value is removed.
Don't paste API keys, bearer tokens, private policies, task text or evaluation
artifacts. Raw doctor output can contain exact filesystem paths; keep it local.
Use `support-bundle --include-local-paths` only in an agreed private channel.

For local MCP launch issues, opt into the local handshake check:

```bash
policynim support-bundle --include-mcp-smoke
```

For hosted setup, include the MCP client/version, transport, redacted generated
configuration, HTTP/error code and whether discovery or a tool failed. Use
`policynim mcp-config --help` to select the matching client/transport. If configuration
reports `hosted_url_placeholder: true`, replace the example endpoint before testing.
Never post a configured authorization header.

For install/release issues, include the tag or commit, install command, platform,
asset name, checksum result and exact safe failure. Follow [docs/release.md](docs/release.md)
for the checks that exist in the current release workflow. Don't infer production
state from a source checkout or preview deployment.

## Help maintainers reproduce

Include the smallest command sequence, expected/observed behavior and whether the
run used local doubles, live NVIDIA, hosted MCP, Docker or Railway. Report the
version actually tested. If an external service blocks reproduction, name the
blocker without claiming the implementation failed or the rollout succeeded.

Maintainer priority goes to security/data exposure, broken installs/artifacts,
blocked CLI/MCP workflows and reproducible regressions. There isn't a response-time
or availability guarantee. [The triage guide](docs/maintainer-triage.md) explains
labels and recovery evidence.
