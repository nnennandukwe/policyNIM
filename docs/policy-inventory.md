# Policy Inventory And Recovery

Use inventory before configuring a model or rebuilding an installation. It reads
policy sources and the local SQLite index without calling NVIDIA or changing files.

```bash
policynim inventory
policynim inventory --format json
policynim doctor --format json
```

In a source checkout, use `uv run policynim` for each command. An installed copy
can run inventory before `init` and without `NVIDIA_API_KEY`.

## Read The Counts

The `source` section describes the selected Markdown corpus: document count,
unique policy IDs, and chunks that the current loader would produce. An empty
readable corpus has observed zero counts. Missing, unreadable, or invalid sources
have `status: unavailable` and `null` counts.

The `index` section describes the configured local database: actual distinct
policy IDs, actual chunks, stored embedding identity, and readiness state. An
absent or unreadable database has unknown counts, rather than an inferred zero.
Matching source and index counts don't prove identical content. Inventory doesn't
claim generation consistency or remote deployment verification.

Both `inventory --format json` and `doctor --format json` include the same typed
index inspection. Doctor adds configuration, credentials, runtime-rules presence,
and MCP setup hints. Doctor's existing exit behavior stays compatible: inspect
its JSON `status` when using it in automation.

## Index States

| State | Meaning | Next action |
|---|---|---|
| `ready` | Nonempty index with complete, matching embedding identity | Continue to the relevant CLI or MCP checks. |
| `missing` | No index exists at the configured location | Verify sources, then explicitly run ingestion. |
| `directory` | A directory was configured where SQLite needs a file | Preserve the directory and recover any legacy-only content first. |
| `empty` | A readable PolicyNIM database has no policy chunks | Preserve it and build a separate candidate from verified sources. |
| `invalid` | The file isn't a readable PolicyNIM database | Check the path; preserve existing data before rebuilding. |
| `unreadable` | Access, I/O, or lock failure prevented inspection | Correct permissions or resolve contention; don't rebuild to diagnose access. |
| `incompatible` | Identity is missing, invalid, or differs from the selected embedding model | Build a complete candidate with the selected model. |
| `incomplete` | Stored ingestion hasn't completed | Preserve the incomplete candidate and inspect the failed build before retrying elsewhere. |

A readable legacy database can have observed counts while remaining incompatible
with current retrieval. Counts alone don't make an index usable. Search, routing,
runtime decisions, and hosted health share the readiness guard; unusable indexes
are rejected before embedding requests.

## Recover A Legacy Configuration

`POLICYNIM_INDEX_DB_PATH` is the canonical SQLite file setting. The deprecated
`POLICYNIM_LANCEDB_URI` alias still accepts a valid SQLite file; the canonical
setting wins when both are present. A legacy LanceDB directory isn't converted or
deleted by inspection.

1. Preserve the old directory, existing SQLite files, and runtime-rules artifacts.
2. Recover any policies that exist only in the old installation. Verify that
   `POLICYNIM_CORPUS_DIR` contains the authoritative Markdown corpus.
3. Choose two unused candidate destinations: `POLICYNIM_INDEX_DB_PATH` for the
   database and `POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH` for the compiled rules.
4. Explicitly run `policynim ingest` with the required NVIDIA credentials. Run
   inventory and doctor against those same settings before activating the candidate.

Ingestion uses NVIDIA and can incur usage. Diagnostics never trigger it. Read the
[provider recovery runbook](hosted-provider-recovery.md) before changing models or
activating candidates. Atomic multi-file generation activation belongs to the
separate publication work; these commands don't provide that guarantee.

## Hosted Evidence

Running inventory in a checkout reports that checkout's selected inputs and
configured database. To inspect a hosted installation, an authorized operator
must run it inside the actual deployment and separately record the application
revision, source identity, and deployment identity. Public `/healthz` is a cheap
readiness probe; it doesn't expose detailed policy inventory or prove live model
availability.
