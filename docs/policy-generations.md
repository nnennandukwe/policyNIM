# Complete policy generations

`policynim ingest` captures selected source bytes, embeds the effective policies,
and builds a new complete generation under `POLICYNIM_GENERATIONS_DIR`. It calls
NVIDIA only when explicitly invoked. The default path is `data/generations` in a
source checkout or hosted process; standalone installations use their user data
root's `generations` directory. Absolute paths work for an operator-owned store.

Each object contains `manifest.json`, `snapshot.json`, `catalog.json`,
`index.sqlite3`, and `runtime_rules.json`. Its ID is the SHA256 digest of the
manifest bytes. The snapshot keeps raw policy text and pack manifests, parsed
profile configuration, and license notices. License notices must use UTF-8. A decoding error names the
notice and stops before provider calls, preserving the prior active generation.
Validation binds artifact hashes,
model/provider/endpoint and
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
Missing-policy errors identify the selected catalog scope and, when known, its
generation ID. Source-scope lookup guidance keeps `--source` on the list command.

Readers need writable publication locks and lease directories, even though sealed
artifacts stay immutable. Permission or storage failures during admission produce
bounded generation errors; inventory reports the unavailable generation and
allows inspection and retry. Preserve the active object, repair access to its
locks and leases, then retry. MCP search and preflight return safe generation
inspection and verified-bundle recovery guidance without filesystem paths.

Search, preflight, runtime decisions, health, and live eval expose `generation_id`.
A live eval job pins one generation across all modes, cases, and regeneration
attempts; it doesn't ingest a temporary index. Offline eval uses synthetic fixtures
and reports no generation. An explicit `POLICYNIM_GENERATION_ID` selects a retained
object for repeatable jobs without changing the active pointer.

Source-resource readers can use `GenerationStore.pin_retained(generation_id)`
with an explicit retained ID. It validates the manifest and every artifact
against the generation's sealed embedding identity and holds the same reader
lease, even when the current retrieval model or endpoint has changed. Ordinary
`pin()` and application index factories still require the configured embedding
identity; archival source reads don't authorize queries with mismatched vectors.

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
construction. Neither activate nor rollback creates a missing root or adopts an
unowned directory when the recovery request is refused.

If an owned store has no activation pointer but retains a complete orphan, inspect
it explicitly, then require the missing-pointer state when activating it:

```bash
policynim generation inspect --id RETAINED-ID --format json
policynim generation activate RETAINED-ID --expect-no-active --format json
```

`--expect-no-active` and `--expect-active ID` are mutually exclusive; one is
required. A concurrent activation makes the missing-pointer expectation stale.
A malformed pointer still requires explicit operator recovery.

Builds, migration, and imports that require a source match recheck their inputs
after waiting for writer ownership. Changed source selections stop before
embedding or staging; review the current selection and retry.

Publishers and retention serialize under a stable writer lock. Reader selection
and activation share a separate short lock; embedding and full validation leave
reader admission available. Readers hold kernel-backed leases, so pruning retains
active, previous, recent, and in-flight generations.
Dead processes release their locks; later cleanup can remove their stale markers.
An index reader inherited through POSIX fork retains its lease until all holders
release it. Closing an index reader waits for admitted SQLite reads to close their
connections before releasing its lease. Managed SQLite opens recheck artifact metadata before and after
opening the connection, rejecting observed replacements before provider work.
Runtime readers reuse successful validation only while all five artifact identities,
membership, metadata-change stamps, and embedding settings match. The process cache
holds at most eight snapshots and 32 MiB of serialized source models; larger
snapshots receive full validation. Filesystems without reliable change stamps also
receive full validation. If a change stamp isn't available, pinned reads validate
content instead of weakening the identity check. A restored stamp is accepted only
after content validation. Operator inspection and export always validate all bytes.
Warm health probes return only the validated generation ID and chunk count when
the pointer, artifact identities, and configured embedding space still match.
They don't clone source models or open filesystem reader leases. Cold probes,
changed identities, unavailable change stamps, and entries outside the bounded
cache still use full pinned validation. There's no time-based healthy-state cache.
Pruning deletes only validated owned objects. Changed or unknown entries remain
preserved with warnings. A changed candidate found during reinspection or before
rename gets its own warning while other candidates, trash, and staging cleanup
continue. Pointer changes, expired operation budgets, and directory synchronization
failures still stop retention; a failed durability barrier isn't a local candidate
rejection.
After interruption, cleanup can remove empty folders with
managed stage/trash names; unmatched names and nonempty unowned entries stay
preserved. Dangling staging links stay in place and produce a warning.
An interrupted initial owner record can resume under the publication lock only
when the directory contains reserved owner-record names and prefixes of the
expected marker bytes. Unknown entries stay preserved. Pruning recovers a
partial staging marker only in an otherwise empty managed stage. A partial
trash marker is repaired only after every original artifact passes the complete
generation inspector; changed or incomplete data stays preserved.
Cleanup diagnostics identify the managed relative path
and a safe cause. All processes must use the same store and filesystem locking
semantics. This isn't a sandbox against arbitrary writes by the same OS
user; external edits can invalidate an object and will fail later inspection.

## Export, import, and refresh the public deployment bundle

```bash
policynim generation export reviewed-generation.zip --format json
policynim generation import reviewed-generation.zip --expect-digest sha256:CHECKSUM --format json
```

Transport uses a deterministic ZIP with exactly the five required flat files.
Import requires a trusted checksum, rejects unsafe/extra/duplicate entries and
oversized content, then runs the same full inspector before activation. Export
checks the packaged bytes against the validated manifest before creating the
destination and refuses to overwrite an existing file. Bundles contain selected
source text, including retained excluded/replaced policies and notices; review
their content before sharing a private profile.

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

Git attributes keep the public Markdown, pack TOML and notice text at LF in fresh
checkouts, including Windows checkouts with `core.autocrlf=true`. Raw source bytes
remain part of generation identity. Save local edits before refreshing an older
checkout that already contains CRLF conversions; source-matched import rejects
those changed bytes. Source directories and the profile file must stay outside
the managed generation root, and that root must stay outside source directories.
