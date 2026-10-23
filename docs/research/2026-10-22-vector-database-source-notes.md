# Bring-your-own vector storage for PolicyNIM

Research date: October 22, 2026. Scope: source review and product recommendations. No database adapter was implemented or exercised. Repository observations use `origin/main` at `0956cca74a7d83091839a03b0d4d1838d54b825f`; the working checkout's older revision wasn't treated as current main.

## Recommendation

Keep SQLite as the default and add Qdrant as the first external backend. Follow with PostgreSQL + pgvector for teams that already operate Postgres. Treat Chroma as a later adapter candidate. This ordering's a product judgment based on implementation scope and deployment choices; it isn't a performance ranking.

The product promise should be: **Choose where your policy index lives, keep ownership of your policy sources, and get the same policy checks across supported backends.** Publish a supported-backend contract so another adapter can be added without rewriting policy selection, compilation, conformance, or runtime decisions.

Free, open-source database software can support this promise. Budget for the user's infrastructure and the configured embedding/generation services separately. A vector-store choice alone doesn't make the complete application free or local.

## Candidate comparison

| Backend | Open-source license and deployment | Integration and filtering | PolicyNIM fit and operating needs |
| --- | --- | --- | --- |
| SQLite + sqlite-vec | MIT/Apache-2.0; SQLite extension embedded in the application, with Python bindings and no separate database server. [Project](https://alexgarcia.xyz/sqlite-vec/) | SQL vector search; `vec0` metadata columns support filtering during KNN. Its operators and metadata types have documented limits. [Filtering](https://alexgarcia.xyz/sqlite-vec/features/vec0.html) | Default for an individual user's first run. PolicyNIM owns local file paths, backup/recovery, compatibility checks, and publication. Keep the existing adapter while widening its contracts. |
| Qdrant | Apache-2.0 server. The Python client supports persistent local mode and a remote server URL; local mode's documented use includes development and testing. [Server](https://github.com/qdrant/qdrant), [Python client](https://github.com/qdrant/qdrant-client) | Collections define vector dimensions and distance metrics; named vectors can have separate configurations. Payload filters can scope retrieval. [Collections](https://qdrant.tech/documentation/manage-data/collections/), [Multitenancy example](https://qdrant.tech/documentation/examples/multitenancy/) | First external adapter candidate. Expose endpoint, collection, credential reference, and transport settings. Self-hosted deployments need explicitly configured authentication, network exposure, and TLS. [Security](https://qdrant.tech/documentation/operations/security/) |
| PostgreSQL + pgvector | PostgreSQL-style permissive license; an extension installed in a Postgres server. [License](https://github.com/pgvector/pgvector/blob/master/LICENSE), [Extension](https://github.com/pgvector/pgvector) | SQL predicates and exact/approximate vector search; Python integration supports Psycopg, asyncpg, and common ORMs. [Python integration](https://github.com/pgvector/pgvector-python) | Strong candidate for users with existing Postgres operations. Requires extension availability, credentials, schema ownership, migrations, connection limits, and database administration. Use dedicated runtime roles and an explicit isolation design. |
| Chroma | Apache-2.0. Python has local persistence and HTTP clients; a server can run separately. [Project](https://github.com/chroma-core/chroma), [Python client reference](https://docs.trychroma.com/reference/python), [Server mode](https://docs.trychroma.com/docs/run-chroma/client-server) | Accepts precomputed embeddings, requires matching query dimensions, and supports `where` metadata filters plus document filters. [Query API](https://docs.trychroma.com/docs/querying-collections/query-and-get) | Useful later for users already running Chroma. Pin and validate the deployment's authentication model before advertising secure shared use. The reviewed material includes version-specific legacy-auth guidance; Cloud credentials can't simply be assumed to secure every self-hosted release. [Legacy auth guidance](https://cookbook.chromadb.dev/security/legacy-auth/) |

### Compatibility details that affect the choice

For pgvector, storage capacity and approximate-index limits differ. The current README documents `vector` HNSW indexing up to 2,000 dimensions and `halfvec` up to 4,000. Approximate searches apply filters after scanning; iterative scans can search further when filters leave too few results. Onboarding should check the actual embedding output and chosen indexing strategy before accepting the connection. Don't silently truncate vectors or cast precision to satisfy an index. [pgvector README](https://github.com/pgvector/pgvector)

Qdrant collections specify the distance metric and vector size. A shared payload field for tenants works only when every relevant operation enforces its scope; PolicyNIM should derive that scope from authenticated configuration. Collection separation and per-collection credentials provide other isolation tools. [Collection configuration](https://qdrant.tech/documentation/manage-data/collections/), [Security controls](https://qdrant.tech/documentation/operations/security/)

Postgres row-level security can enforce per-row access. Superusers and roles with `BYPASSRLS` bypass it, and table owners normally do too. A future adapter should verify its runtime role and test cross-tenant access through the same role the service uses. [Postgres row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)

## Existing extension seams and gaps

These observations come from current-main source inspection:

1. `src/policynim/contracts.py` already defines `IndexStore`, including search, identity validation, whole-policy reads, and replacement. That's a useful seam to retain.
2. `src/policynim/services/index_store.py` still returns `SQLiteVecIndexStore`. Its normal path pins a `GenerationStore` snapshot and opens the SQLite artifact in that snapshot. A backend selector has to preserve that generation ownership.
3. `src/policynim/services/ingest.py` expects its store URI to be a `Path` and calls filesystem methods. Remote locators need a typed representation and store-owned publication behavior.
4. `src/policynim/settings.py` exposes SQLite paths, generation paths, and NVIDIA embedding settings. `src/policynim/storage/index_readiness.py` includes SQLite-specific readiness reasons. Remote connection, credential, capability, and health states need intentional contracts.
5. Index publication, runtime rules, source references, and reader lifetime have to describe one compatible generation. Keeping those guarantees across stores is part of PolicyNIM's business behavior.

Proposal: add the smallest backend configuration and factory seam needed for SQLite and Qdrant. Keep policy logic in services. Let adapters own query translation, transport, storage identifiers, and backend-specific publication. Add a versioned adapter contract once the two implementations establish which behavior actually varies.

## Proposed onboarding flow

1. **Choose where PolicyNIM runs.** Local or self-hosted server. Explain where policy text and credentials will be processed. A hosted service's `localhost` refers to its own machine; a laptop database needs a local PolicyNIM process or an explicitly reachable private connection.
2. **Choose storage.** Show “Local SQLite — recommended” and “Connect Qdrant.” Expose pgvector when its adapter passes the shared contract. Ask for backend-specific fields only after selection.
3. **Check the connection.** Inspect reachability, authentication, permissions, server compatibility, namespace ownership, and embedding configuration. A successful network connection should be labeled “Connected”; readiness requires the later checks.
4. **Import policy sources.** Preview source IDs, citations, policy domains, and runtime rules. In a fresh owned namespace, build a candidate generation with the configured embeddings. Show the software, infrastructure, and provider cost boundaries before starting provider work.
5. **Verify the first useful result.** Retrieve a known policy, inspect its source span, review the complete selected policy bodies, and show a supported runtime decision. Under the proposed adapter lifecycle, activate the generation only after validation. Save the resulting nonsecret configuration and recovery instructions.

Keep advanced controls collapsed: metric, index parameters, namespace mapping, timeouts, and connection pooling. Derive embedding dimensions from the configured provider's actual output. Record the embedding identity, normalization behavior, chunking/schema version, corpus/profile identity, and generation ID; equal vector lengths alone don't establish compatible embeddings.

## Acceptance gates before a backend appears as supported

1. **Configuration and isolation:** UI and CLI resolve the same settings; secrets stay server-side or in user-owned secret storage; invalid credentials, unowned namespaces, forbidden cross-tenant requests, and unsafe endpoints fail with actionable errors.
2. **Retrieval contract:** a shared fixture preserves IDs, complete selected-policy bodies, citation spans, metadata filters, bounded reads, and deterministic tie handling. Preserve current source-extractive compile/preflight output and empty advisory fields. Compare retrieval against labeled policy relevance rather than requiring identical approximate-neighbor ordering.
3. **Compatibility:** mismatched model, dimensions, metric, schema, profile, generation, or incomplete publication refuse retrieval before paid provider work. Existing arbitrary collections require a validated import/mapping path.
4. **Publication and recovery:** interrupted ingest, cancellation, concurrent readers, failed activation, retry, rollback, and backup/restore preserve a usable prior generation and matching runtime rules. Test backend behavior through real service boundaries.
5. **Onboarding proof:** a fresh user can select the backend, establish a validated index, run the first policy-backed workflow, restart, and resume with the same configuration. Track completion and failure causes; establish latency and recall measurements on representative policy corpora before publishing performance claims.

## Evidence limits

This review used official project documentation, repositories, license text, Context7 documentation retrieval, and current-main source inspection. No candidate database was started, no migration was run, and no runtime equivalence, recall, throughput, hosted readiness, or adoption result was measured. Chroma's exact supported server/auth version remains an adapter-scoping question. Milvus wasn't examined because the four candidates already cover embedded storage, a dedicated vector server, and an existing relational database.
