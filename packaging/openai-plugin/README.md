# PolicyNIM portable plugin source

Build the source archive from the repository root:

```bash
uv sync --locked --group test --group dev
uv run --no-sync python scripts/build_plugin.py --output dist/policynim-plugin-source.zip
```

The result reports `source_only`. The empty source `mcpServers` object deliberately
leaves the production endpoint unconfigured. This archive isn't an upload candidate.
The [handoff](../../docs/plugin-submission/README.md) lists the remaining values,
owner decisions and live acceptance evidence.

`plugin.json` owns the listing, starter prompts, release notes and exactly five
positive/three negative planned review cases. The server supplies its own discovery
contract for all eight tools. No tool subset, new skill, local executor or UI is
bundled. The builder copies exactly `plugin.json`, `mcp.json`, `assets/icon.svg`,
`LICENSE` and `NOTICE` to the ZIP root. Schemas, this README and all review documents
stay outside that archive.

For a candidate build, supply a local JSON object with exactly these six string
keys: `mcp_endpoint`, `websiteURL`, `supportURL`, `privacyPolicyURL`,
`termsOfServiceURL`, `demo_recording_url`. Use actual public HTTPS URLs without
embedded credentials, query strings, fragments or percent escapes. Keep account access details in
the dashboard's private review form. Then run:

```bash
uv run --no-sync python scripts/build_plugin.py \
  --release-inputs /path/to/release-inputs.json \
  --output dist/policynim-plugin-candidate.zip
```

Replace the command's input path with your local file. Successful rendering reports
`candidate_requires_live_acceptance`. The builder validates URL syntax; public
reachability, publisher identity, OAuth acceptance and executed cases require the
handoff's separate checks. Validation finishes before an existing output's replaced.
Identical source and release inputs produce identical ZIP bytes on supported
Python versions: stored compression, sorted fixed allowlist, 1980 timestamps and
regular-file permissions avoid host-dependent archive metadata.

`mcp_endpoint` must use exactly `/mcp` at the service origin, matching the runtime's
public resource URL. A path prefix, website root, admin page or trailing-slash
variant isn't accepted. Plain, unescaped release URLs keep encoded credential
text out of the package; use a stable direct URL when a sharing link needs escapes.

Validation errors identify the rejected field or package rule without echoing the
supplied value. Directory aliases such as macOS `/tmp` resolve to their physical
destination. A final output file that's a symlink is rejected, and the resolved
output must stay outside package source.

The staged ZIP file stays readable by its owner only. Its internal member modes
are fixed at 0644. Upload it from the account that built it, or let the owner choose
permissions when copying it for another local user. The builder rejects recognized
credential forms and rooted path references while allowing validated public URL paths.
Human review of the five allowlisted files remains required before distribution.
Public listing and review text must omit rooted POSIX paths, Windows-drive paths,
UNC paths and credential assignments. Use public URLs or plugin-relative asset
references where a published resource needs a path.

## Schema and asset provenance

Agent Plugins 1.0.0 schemas were retrieved on October 23, 2026. Their original
bytes are vendored for offline validation and checked against these SHA256 values:

| Schema URL | SHA256 |
| --- | --- |
| https://agent-plugins.org/schemas/1.0.0/plugin.schema.json | `0a4aad95ce337878ad38802ebf0daa3fde76abe3f65400c86bcbb1ec0b3ab883` |
| https://agent-plugins.org/schemas/1.0.0/mcp.schema.json | `6539175bfcdf43085855183e86da40ea94b166547a72b47ae9a0a390516d3acb` |

Both schema files match the upstream repository at
`ff8ab5e392cc87bd88d87c060815a87490e51003`. Software material, including schemas,
uses Apache-2.0 under the upstream
[license notice](https://github.com/agentplugins/agent-plugins-spec/blob/ff8ab5e392cc87bd88d87c060815a87490e51003/LICENSE.md).
The unmodified license text is retained in `schemas/Apache-2.0.txt`; schemas and
that license stay outside the install ZIP because they're build-time inputs.

The portable schemas leave OpenAI extension contents open. The builder separately
enforces the current listing limits, review-case counts, icon references and a
deliberately narrow supported field set. When adding metadata, update its consumer
validation and tests together. No schema is fetched during builds or tests.

The SVG is a 512-by-512 wrapper with a square viewport over the existing light-mode
PNG. The embedded PNG bytes and dimensions are checked; no external image or script
is loaded. `NOTICE` records source attribution and the unresolved NVIDIA mark
redistribution gate. The same icon works on both themes using its existing opaque
light background. It adds no new brand artwork.

Official references checked on October 23, 2026:
[package format](https://developers.openai.com/plugins/build/plugins),
[submission fields](https://developers.openai.com/plugins/deploy/submission),
[submission errors](https://developers.openai.com/plugins/deploy/submission-errors),
and [plugin guidelines](https://developers.openai.com/plugins/plugin-guidelines).
