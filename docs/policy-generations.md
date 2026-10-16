# Complete policy generations

`policynim ingest` captures selected source bytes, embeds the effective policies,
and builds a new complete generation under `POLICYNIM_GENERATIONS_DIR`. It calls
NVIDIA only when explicitly invoked. The default path is `data/generations` in a
source checkout or hosted process; standalone installations use their user data
root's `generations` directory. Absolute paths work for an operator-owned store.

Each content-addressed object contains five files: the manifest, captured raw
source/profile text and license notices, effective catalog, SQLite index, and
runtime rules. Validation binds artifact hashes, model/provider/endpoint and
vector dimension, schemas, exact chunks/provenance, and source-derived rules.
Activation replaces `active.json` once, retaining both active and previous IDs.
A failed preparation preserves the prior pointer. Cleanup or sync failures after
activation return committed results with warnings. POSIX publication syncs files
and directories. Windows coverage establishes process-crash consistency;
power-loss durability isn't certified.

## Inspect or migrate an installation

These commands don't need NVIDIA credentials or setup prompts:

```bash
policynim generation inspect --format json
policynim generation migrate --format json
policynim inventory --format json
policynim policies list --format json
policynim policies show POLICY-ID --format json
policynim policies validate --source --format json
```

Migration copies `POLICYNIM_INDEX_DB_PATH` and
`POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH`, verifies them against configured sources,
and preserves both originals. Empty, corrupt, incomplete, mismatched, or stale
pairs aren't silently repaired. LanceDB directories remain diagnostic inputs;
there's no automatic LanceDB conversion. Preserve them and build a fresh
generation from authoritative sources when migration isn't possible.

Catalog commands read the active generation by default. `--source` inspects the
configured editable source/profile instead. With no active generation, catalog
commands report source scope and an unknown indexed count. An invalid active
pointer fails validation rather than silently reading editable files.

Search, preflight, runtime decisions, health, and live eval expose `generation_id`.
A live eval job pins one generation across all modes, cases, and regeneration
attempts; it doesn't ingest a temporary index. Offline eval uses synthetic fixtures
and reports no generation. An explicit `POLICYNIM_GENERATION_ID` selects a retained
object for repeatable jobs without changing the active pointer.

## Roll back and retain readers

Inspect the current ID first and pass it as a concurrency token:

```bash
policynim generation rollback --expect-active CURRENT-ID --format json
policynim generation activate RETAINED-ID --expect-active CURRENT-ID --format json
policynim generation prune --keep-recent 2 --format json
```

If an active object is damaged, failed `generation inspect --format json` still
reports a valid owned `activation` record when it can read one. Use its active ID
as the rollback token. Rollback, activate, import, and migration validate the
replacement independently and preserve damaged old bytes. A malformed pointer or
unowned store still fails; recover into a separate operator-owned store rather
than guessing state. Paid builds reject damaged active state before provider
construction.

Publishers serialize under one stable process lock. Readers hold kernel-backed
leases, so pruning retains active, previous, recent, and in-flight generations.
Dead processes release their locks; later cleanup can remove their stale markers.
Pruning deletes only validated owned objects. Changed or unknown entries remain
preserved with warnings. All processes must use the same store and filesystem
locking semantics. This isn't a sandbox against arbitrary writes by the same OS
user; external edits can invalidate an object and will fail later inspection.

## Export, import, and refresh the public deployment bundle

```bash
policynim generation export reviewed-generation.zip --format json
policynim generation import reviewed-generation.zip --expect-digest sha256:CHECKSUM --format json
```

Transport uses a deterministic ZIP with exactly the five required flat files.
Import requires a trusted checksum, rejects unsafe/extra/duplicate entries and
oversized content, then runs the same full inspector before activation. Export
refuses to overwrite an existing file. Bundles contain selected source text,
including retained excluded/replaced policies and notices; review their content
before sharing a private profile.

Both Dockerfiles consume `artifacts/public-generation/generation.zip` and its
checked-in `generation.sha256`. Their source-match guard rejects a bundle that
no longer matches packaged policies. Refreshing it requires an explicitly built
or migrated matching generation, offline export, replacing both reviewed bundle
files, and running the public-bundle test:

```bash
uv run pytest -q tests/test_dockerfile_contract.py
```

The initial bundle copies the existing production vectors for the public pack;
it doesn't claim a new model evaluation. License and upstream notice text is
inside `snapshot.json`. Image builds and startup don't call NVIDIA. Query,
reranking, and generation calls remain paid runtime operations where configured.
