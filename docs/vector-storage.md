# Choose your vector database

Use `policynim setup` to select SQLite, Qdrant, or pgvector in a local browser. The
same settings drive the UI, CLI, and MCP services. SQLite stays the default and
doesn't require external services or a database extra.

## Data ownership

A sealed PolicyNIM generation still contains the authoritative policy sources,
catalog, SQLite index, and runtime rules. The SQLite snapshot retains vectors for
identity checks and offline recovery. External storage receives logical chunk
identifiers, policy domains, a generation binding, and vectors. Retrieval resolves
remote identifiers back to that held source generation; remote payloads can't
replace policy text or citations.

Embedding and retrieval workflows still use the configured NVIDIA services. Read
the [profile privacy boundary](policy-profiles.md#provider-and-privacy-boundary)
before selecting private sources. Free database software doesn't cover your
infrastructure or model-provider costs.

## Configure a backend

| Setting | Default | Meaning |
| --- | --- | --- |
| `POLICYNIM_VECTOR_BACKEND` | `sqlite` | `sqlite`, `qdrant`, or `pgvector` |
| `POLICYNIM_VECTOR_URL` | Unset | Credential-free backend URL; required for external storage |
| `POLICYNIM_VECTOR_NAMESPACE` | `policynim` | Dedicated installation prefix, 1-16 lowercase letters/digits/underscores, starting with a letter |
| `POLICYNIM_VECTOR_API_KEY` | Unset | Qdrant secret; never include it in the URL |
| `POLICYNIM_VECTOR_PASSWORD` | Unset | Postgres password; never include it in the URL |
| `POLICYNIM_VECTOR_TIMEOUT_SECONDS` | `10` | Finite connection/request timeout, greater than 0 and at most 120 seconds |

Normal configuration precedence applies: direct settings, process environment,
discovered env files, and defaults. A URL with the SQLite backend is rejected.
An empty URL isn't an alternate backend selector. Existing `INDEX_DB_PATH` and
legacy path aliases keep their standalone migration meaning.

### Qdrant

Set these values in your private config file:

```dotenv
POLICYNIM_VECTOR_BACKEND=qdrant
POLICYNIM_VECTOR_URL=https://vectors.example.com
POLICYNIM_VECTOR_NAMESPACE=team_backend
```

Set `POLICYNIM_VECTOR_API_KEY` through your secret store or private environment.
The endpoint must use HTTPS, except that loopback development can use
`http://127.0.0.1:6333`. URL credentials, query strings, fragments, and path prefixes
are rejected. Redirects and ambient HTTP proxies aren't used. Configure database
authentication, TLS, and network access for your installation.

The adapter creates a generation-specific collection with the model's actual
dimension and Euclidean distance, uploads records with completed writes, and reads
back the complete index before activation. Queries use exact search and server-side
generation/domain filters. A request's JSON response is limited to 16 MiB.

### PostgreSQL + pgvector

Install the optional pinned driver in a Python-managed PolicyNIM installation.
These commands need a source checkout or wheel containing the new storage support.
From a source checkout:

```bash
uv sync --locked --group test --group dev --extra vector-postgres
```

Ask the database owner to install the `vector` extension and provide a dedicated
database/runtime role. PolicyNIM won't install the extension or change database
roles. Publishing needs table creation and insert permissions in `public`;
read-only retrieval needs select access to the generation tables. Use different
installation prefixes for independent PolicyNIM roots. The initial adapter uses
exact L2 search, avoiding the lower dimension limits of approximate indexes.

```dotenv
POLICYNIM_VECTOR_BACKEND=pgvector
POLICYNIM_VECTOR_URL=postgresql://policy_app@db.example.com/policies?sslmode=verify-full
POLICYNIM_VECTOR_NAMESPACE=team_backend
```

Set `POLICYNIM_VECTOR_PASSWORD` separately. Use one database-name path segment without a trailing slash. Percent-encode
special characters in database names. Only the
`sslmode` query option is accepted; it defaults to `verify-full`.
`sslmode=disable` is available only for loopback test databases. Standard libpq
certificate configuration applies. A new table is created, populated, and
validated within one database transaction. Existing tables aren't overwritten.
Standalone binaries don't acquire optional Python extras at runtime; use a
Python-managed installation for pgvector.

## Probe, build, inspect, and recover

1. Run `policynim vector probe --format json`. `connected` proves authenticated
   discovery or extension availability; it doesn't prove a generation is ready.
2. Run `policynim ingest --sync-vectors` to approve provider work and external
   publication. Without the flag, an external-backend build is refused before
   provider work. SQLite keeps ordinary `policynim ingest` behavior.
3. Run `policynim vector inspect --format json` to validate the selected source
   generation and compare every external vector with its canonical source snapshot.
4. To use an already complete local/imported generation with another backend,
   configure that backend and run `policynim vector sync`. This publishes a fresh
   derived namespace without embedding or changing the active generation.
5. If a Qdrant upload was interrupted, preserve its partial collection. Choose a
   fresh namespace prefix or restore the owned collection through your database's
   operational process, then retry synchronization. An existing namespace is
   reused only when all records match the canonical generation.

Normal ingestion validates the external replica before replacing `active.json`.
Failures before that commit preserve the prior active pointer. Retained namespaces
allow rollback to previously synchronized generations. Offline import, activation,
and bundle commands retain their existing no-network behavior; after selecting a
generation whose replica is absent, run `vector sync` before retrieval. No fallback
silently queries SQLite when an external backend is selected.

Local pruning doesn't delete external collections/tables. Database retention stays
operator-owned. Use a dedicated database and credentials: administrators or clients
with write access can change remote data concurrently. Validation detects observed
record mismatch; it doesn't isolate against a hostile database administrator.

## Adapter extension contract

`storage/vectors.py` defines the `VectorBackend` protocol: read-only probe,
fresh-namespace preparation, complete record validation, filtered search, and
resource cleanup. `services/vector_storage.py` owns configuration/composition.
`ExternalVectorIndexStore` retains the local generation lease and canonical source
reads while replacing vector search. Another adapter must satisfy the same refusal,
source-ownership, lifecycle, and real-backend tests before its configuration value
appears in setup.

Publication and explicit `vector inspect` compare every external record with the
sealed snapshot. Routine readiness reads schema, generation binding, and counts;
queries read only their returned vectors and compare those matches with canonical
vectors before hydrating source text. Unreturned vector contents are checked by
full inspection. Treat published namespaces as immutable, and rerun inspection
after operator changes or restore.

Public health probes share one in-flight external check and cache its result for
one second. Changing the active pointer invalidates that cache immediately.
Health is a short-lived readiness signal; explicit inspection provides the full
replica comparison. Exact search and database-side counts still cost more as the
index grows. Large-corpus tuning, approximate search, distributed source storage,
and hosted per-tenant private indexes need separate design and measurement.
