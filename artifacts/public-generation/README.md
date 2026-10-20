# Public generation bundle

This ZIP contains the reviewed public engineering baseline: 26 policies and 230
chunks, embedded on October 20, 2026 with `nvidia/nemotron-3-embed-1b` at
`https://integrate.api.nvidia.com/v1`, with 2048 dimensions. The actual
`policynim ingest` command rebuilt the full index after review restored default
personal-data redaction, explicit startup rejection, correlation fallback, job lifecycle logging, structured service events, and API rollout review.
The final rebuild also retains OWASP adaptation labels for logging/configuration and limits model-identity guidance to services that use models. `generation export` produced the transport archive. It doesn't contain credentials,
auth state, private policy packs, execution evidence, or model-quality results.
Embedding usage wasn't collected; no cost or quality result is inferred.

Generation: `a9d51e8459369bcb96f467557408f53c4e8ecc77e20c52b1c14cad467b549908`.
Catalog: `sha256:5b871269603d20a80b1f4bcb5a146e60f4224503a9e87c707df5e29b7bf83c27`.

`generation.sha256` binds the transport bytes. Docker import revalidates hashes,
schemas, source membership/provenance, catalog, index, rules, model identity, and
current packaged source text before activation. Source changes need a reviewed
bundle refresh. Source and license notices are retained in `snapshot.json`.
The generation's rule artifact contains no new runtime execution authority.

See [generation operations](../../docs/policy-generations.md) for export, import,
rollback, retention, and refresh commands.
