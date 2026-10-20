# Public generation bundle

This ZIP contains the reviewed public engineering baseline: 26 policies and 230
chunks, embedded on October 20, 2026 with `nvidia/nemotron-3-embed-1b` at
`https://integrate.api.nvidia.com/v1`, with 2048 dimensions. The actual
`policynim ingest` command rebuilt the full index after review restored default
personal-data redaction, explicit startup rejection, correlation fallback, and job lifecycle logging.
`generation export` produced the transport archive. It doesn't contain credentials,
auth state, private policy packs, execution evidence, or model-quality results.
Embedding usage wasn't collected; no cost or quality result is inferred.

Generation: `7ca8cd84322c089bf88d82a3c4cc944fe34077faa815a9bfa67c2c3fbafee5b6`.
Catalog: `sha256:f5daeab987c3dc4607159d7b478cb130da3c1d54276b7c4ee012660b3b9bdba6`.

`generation.sha256` binds the transport bytes. Docker import revalidates hashes,
schemas, source membership/provenance, catalog, index, rules, model identity, and
current packaged source text before activation. Source changes need a reviewed
bundle refresh. Source and license notices are retained in `snapshot.json`.
The generation's rule artifact contains no new runtime execution authority.

See [generation operations](../../docs/policy-generations.md) for export, import,
rollback, retention, and refresh commands.
