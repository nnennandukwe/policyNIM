# Public generation bundle

This ZIP contains the reviewed public engineering baseline: 26 policies and 230
chunks, embedded on October 20, 2026 with `nvidia/nemotron-3-embed-1b` at
`https://integrate.api.nvidia.com/v1`, with 2048 dimensions. The actual
`policynim ingest` command rebuilt the full index after review restored default
personal-data redaction, explicit startup rejection, correlation fallback, job lifecycle logging, structured service events, and API rollout review.
The final rebuild also retains OWASP adaptation labels for logging/configuration and limits model-identity guidance to services that use models. The latest rebuild follows the independent review of all 16 changed policies and 64 authored scenarios, narrowed per-policy source sections, corrected adaptation notices, and two rationale references. `generation export` produced the transport archive. It doesn't contain credentials,
auth state, private policy packs, execution evidence, or model-quality results.
Embedding usage wasn't collected; no cost or quality result is inferred.

Generation: `3c4e948289c6cd843b8002c83e094f1fb3eb9ce9f44d7bc88d26a07862cacb63`.
Catalog: `sha256:04a26d71f3b54eb63e41e779585177ce397767cdf89da9368de3e92d61667a26`.

`generation.sha256` binds the transport bytes. Docker import revalidates hashes,
schemas, source membership/provenance, catalog, index, rules, model identity, and
current packaged source text before activation. Source changes need a reviewed
bundle refresh. Source and license notices are retained in `snapshot.json`.
The generation's rule artifact contains no new runtime execution authority.

See [generation operations](../../docs/policy-generations.md) for export, import,
rollback, retention, and refresh commands.
