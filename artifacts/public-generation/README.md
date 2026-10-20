# Public generation bundle

This ZIP preserves the public production index and runtime rules for the selected
15-policy, 111-chunk pack. It's migrated and validated offline against the exact
pack source bytes, with model `nvidia/nemotron-3-embed-1b`, endpoint
`https://integrate.api.nvidia.com/v1`, and 2048 dimensions. It doesn't contain
credentials, auth state, runtime execution evidence, or model evaluation results.

`generation.sha256` binds the transport bytes. Docker import revalidates hashes,
schemas, source membership/provenance, catalog, index, rules, model identity, and
current packaged source text before activation. Source changes need a reviewed
bundle refresh. License and upstream notices are retained in `snapshot.json`.

See [generation operations](../../docs/policy-generations.md) for export, import,
rollback, retention, and refresh commands.
