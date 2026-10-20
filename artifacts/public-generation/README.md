# Public generation bundle

This ZIP contains the reviewed public engineering baseline: 26 policies and 230
chunks, embedded on October 20, 2026 with `nvidia/nemotron-3-embed-1b` at
`https://integrate.api.nvidia.com/v1`, with 2048 dimensions. The actual
`policynim ingest` command rebuilt the full index after review restored default
personal-data redaction, explicit startup rejection, correlation fallback, job lifecycle logging, structured service events, and API rollout review.
`generation export` produced the transport archive. It doesn't contain credentials,
auth state, private policy packs, execution evidence, or model-quality results.
Embedding usage wasn't collected; no cost or quality result is inferred.

Generation: `ed3b3fd4142e5232b62d880ad9c7943d76ba3bf56d5e24664e47741691223d02`.
Catalog: `sha256:4afa01ea24f98cb7745998b0f6ba57bdbd61ca617f817fcdaba04528c4ea716b`.

`generation.sha256` binds the transport bytes. Docker import revalidates hashes,
schemas, source membership/provenance, catalog, index, rules, model identity, and
current packaged source text before activation. Source changes need a reviewed
bundle refresh. Source and license notices are retained in `snapshot.json`.
The generation's rule artifact contains no new runtime execution authority.

See [generation operations](../../docs/policy-generations.md) for export, import,
rollback, retention, and refresh commands.
