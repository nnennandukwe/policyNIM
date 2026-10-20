# Public generation bundle

This ZIP contains the reviewed public engineering baseline: 26 policies and 230
chunks, embedded on October 20, 2026 with `nvidia/nemotron-3-embed-1b` at
`https://integrate.api.nvidia.com/v1`, with 2048 dimensions. The actual
`policynim ingest` command rebuilt the full index after review restored default
personal-data redaction, explicit startup rejection, correlation fallback, job lifecycle logging, structured service events, and API rollout review.
`generation export` produced the transport archive. It doesn't contain credentials,
auth state, private policy packs, execution evidence, or model-quality results.
Embedding usage wasn't collected; no cost or quality result is inferred.

Generation: `3872748209444592f271c84a870714bf759f5d00622f105782878a16dbd05a52`.
Catalog: `sha256:a95f9b3e777716553982115425a681adc7038e417e55430eea7878f869222661`.

`generation.sha256` binds the transport bytes. Docker import revalidates hashes,
schemas, source membership/provenance, catalog, index, rules, model identity, and
current packaged source text before activation. Source changes need a reviewed
bundle refresh. Source and license notices are retained in `snapshot.json`.
The generation's rule artifact contains no new runtime execution authority.

See [generation operations](../../docs/policy-generations.md) for export, import,
rollback, retention, and refresh commands.
