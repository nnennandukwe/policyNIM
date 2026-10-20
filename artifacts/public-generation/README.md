# Public generation bundle

This ZIP contains the reviewed public engineering baseline: 26 policies and 230
chunks, embedded on October 20, 2026 with `nvidia/nemotron-3-embed-1b` at
`https://integrate.api.nvidia.com/v1`, with 2048 dimensions. The build used the
actual `policynim ingest` command. A later OpenSSF license metadata correction
rebuilt the complete generation through the publication service, reusing all 230
persisted vectors only after exact source-text and embedding-identity comparison.
That metadata rebuild made no additional provider calls. `generation export`
produced the transport archive. It doesn't contain
credentials, auth state, private policy packs, execution evidence, or model-quality
results. Embedding usage wasn't collected; no cost or quality result is inferred.

Generation: `ca8b6b81c4282f992b638f038720daaeb695698500c3d19c1cfb64306c52959d`.
Catalog: `sha256:f054b48e165adcca68cea55595ea8f6aa623051556b8193a42ba013c432f0113`.

`generation.sha256` binds the transport bytes. Docker import revalidates hashes,
schemas, source membership/provenance, catalog, index, rules, model identity, and
current packaged source text before activation. Source changes need a reviewed
bundle refresh. Source and license notices are retained in `snapshot.json`.
The generation's rule artifact contains no new runtime execution authority.

See [generation operations](../../docs/policy-generations.md) for export, import,
rollback, retention, and refresh commands.
