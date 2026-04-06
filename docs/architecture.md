# PolicyNIM Architecture

## Purpose

PolicyNIM is a policy-aware preflight layer for AI coding agents. It turns a small
Markdown policy corpus into grounded implementation guidance with citations, while
keeping the public system easy to inspect from the terminal.

The architecture stays intentionally small:

- typed contracts shared by CLI and MCP
- explicit provider and storage adapters
- application services for ingest, retrieval, preflight, and evaluation
- application services for runtime decisions, runtime execution, and durable
  evidence capture
- fail-closed grounding rules instead of best-effort freeform answers

## Design Principles

- Keep the runtime path explicit and reviewable.
- Make NVIDIA usage visible instead of hiding it behind generic wrappers.
- Keep CLI and MCP payloads aligned through shared typed models.
- Preserve source line spans and stable chunk IDs so citations are inspectable.
- Fail closed when setup is invalid or evidence is too weak.

## Visual Diagram

See [architecture-diagram.md](architecture-diagram.md) for Mermaid views of the
current package boundaries and runtime flow.

## System Shape

### Corpus And Ingest Flow

The shipped corpus lives under `policies/` as Markdown documents with optional YAML
frontmatter. Frontmatter is preferred, not required.

Ingest flow:

1. discover policy files from the bundled corpus or `POLICYNIM_CORPUS_DIR`
2. parse Markdown into normalized documents
3. infer missing metadata when possible
4. extract heading-aware sections with stable line spans
5. build deterministic chunk IDs
6. embed chunk text through NVIDIA-hosted embeddings
7. replace the local LanceDB table with the new embedded rows

Important ingest rules:

- source line spans are preserved as inclusive 1-based ranges
- chunk IDs are deterministic
- malformed frontmatter and duplicate effective policy IDs are rejected
- off-template but readable Markdown is still accepted when meaningful content
  can be recovered

### Indexing And Retrieval Flow

Indexing and retrieval are local-first except for the NVIDIA-hosted model calls.

Retrieval flow:

1. validate runtime settings
2. embed the user query through NVIDIA-hosted embeddings
3. retrieve dense candidates from the local LanceDB table
4. optionally filter by policy domain
5. rerank candidates through NVIDIA-hosted reranking
6. return a JSON-first `SearchResult`

Current retrieval design choices:

- one local LanceDB table stores all indexed chunks
- ingest replaces the whole table instead of incrementally merging rows
- the same embedding model is used for document and query vectors
- reranking is part of the normal search path, not a separate experimental mode

### Grounded Preflight Flow

`PreflightService` is the main orchestration layer for grounded policy guidance.

Preflight flow:

1. retrieve and rerank relevant evidence
2. send retained evidence to the grounded generator
3. receive a structured draft that cites retrieved chunk IDs
4. validate every cited chunk ID against the retained result set
5. materialize public citations and policy guidance
6. return a JSON-first `PreflightResult`

Fail-closed rules are central here:

- missing configuration remains an explicit error
- missing index remains an explicit error
- malformed generation output is rejected
- invalid citation references invalidate the answer
- weak or invalid grounding becomes `insufficient_context=true`

PolicyNIM intentionally prefers no answer over fabricated guidance.

### Evaluation Flow

`EvalService` provides the local quality workflow.

Evaluation flow:

1. load the bundled eval suite
2. run cases against `search` and `preflight`
3. score results with deterministic checks
4. compare rerank-enabled and rerank-disabled runs
5. persist JSON artifacts and HTML reports
6. optionally start the local Evidently UI

Important evaluation rules:

- offline mode is the default contributor path
- live mode is opt-in and requires `NVIDIA_API_KEY`
- live mode uses an isolated temporary LanceDB path
- scoring is code-based, not LLM-as-judge
- expected chunk recall, policy recall, and insufficient-context accuracy are the
  core tracked metrics

## Package Boundaries

### Repo Root

- `Dockerfile` builds the hosted image and bakes the LanceDB index into it.
- `railway.toml` pins the Day 3 Railway deploy contract to Dockerfile build plus
  `/healthz` health checks.

### `src/policynim/settings.py`

- The only module that reads environment variables directly.
- Exposes validated application settings to the rest of the package, including
  hosted MCP port resolution from explicit app config or Railway `PORT`, plus a
  production-only default bind of `0.0.0.0` when Railway injects `PORT` and
  `POLICYNIM_MCP_HOST` is unset.

### `src/policynim/types.py`

- Shared typed models for requests, results, citations, metadata, and eval output.
- Contains no file I/O, environment access, or transport-specific behavior.

### `src/policynim/contracts.py`

- Defines the provider and storage seams used by services.
- Keeps application orchestration independent from concrete adapters.

### `src/policynim/ingest/`

- Owns document discovery, parsing, metadata normalization, section extraction,
  and chunk assembly.
- Must not import CLI or MCP modules.

### `src/policynim/providers/`

- Owns NVIDIA-specific endpoint construction, auth, retries, timeouts, response
  validation, and lifecycle of internally created clients.
- Must not import CLI or MCP modules.

### `src/policynim/storage/`

- Owns LanceDB persistence, runtime evidence storage, row mapping, replacement,
  and search behavior.
- Must not read environment variables directly.

### `src/policynim/services/`

- Owns application orchestration.
- `IngestService` handles parse, chunk, embed, and rebuild.
- `SearchService` handles query embedding, retrieval, and reranking.
- `PreflightService` handles retrieval, grounded synthesis, and citation
  validation.
- `RuntimeDecisionService` compiles and matches runtime rules against the local
  index by loading the compiled runtime rules artifact, matching actions
  against it, and linking the matched rules back to indexed evidence.
- `RuntimeExecutionService` enforces runtime decisions, optionally executes the
  sanitized action, and persists immutable evidence events.
- `RuntimeEvidenceReportService` reads persisted runtime evidence rows and
  returns a typed session summary for the CLI `evidence report` flow.
- `EvalService` handles gold-case execution, scoring, comparison, and report
  persistence.
- `IndexDumpService` handles terminal-friendly inspection of stored chunks.
- `RuntimeHealthService` handles hosted HTTP readiness checks for the local index.
- `BetaAuthService` handles hosted beta GitHub login, API-key issuance, and
  daily request quota enforcement through the auth SQLite store.
- Services may depend on `settings`, `types`, `contracts`, providers, and storage,
  but they must not import CLI or MCP modules.

### `src/policynim/interfaces/`

- Owns transport-specific entry points only.
- `cli.py` defines terminal-facing commands and help text.
- `mcp.py` defines the MCP tool surface, hosted HTTP auth gate, self-serve
  `/beta` portal routes, readiness route, structured hosted logging, and server
  startup.
- Interface modules call services, not providers directly.

## Import Rules

- `settings.py` imports standard library plus `pydantic-settings`.
- `types.py` imports standard library and `pydantic`.
- `contracts.py` imports only shared types and standard library helpers.
- `ingest/` may import `types.py`, `errors.py`, and parsing dependencies.
- `providers/` may import `settings.py`, `contracts.py`, `errors.py`, and SDK or
  HTTP dependencies.
- `storage/` may import `contracts.py`, `types.py`, and `errors.py`.
- `services/` may import `settings.py`, `types.py`, `contracts.py`, providers,
  storage, and ingest modules.
- `interfaces/` may import `services/`, `settings.py`, and `types.py`.

## Public Interfaces

### CLI

- `policynim ingest`
- `policynim dump-index`
- `policynim search --query ...`
- `policynim preflight --task ...`
- `policynim eval --mode offline|live [--headless] [--no-compare-rerank]`
- `policynim mcp --transport stdio|streamable-http`
- `policynim runtime decide --input <path|->`
- `policynim runtime execute --input <path|->`
- `policynim evidence report --session-id <id>`
- `policynim beta-admin list-accounts|suspend|resume|revoke-key`

### MCP Tools

- `policy_preflight(task, domain?, top_k?)`
- `policy_search(query, domain?, top_k?)`

### Hosted HTTP Endpoint

- `GET /healthz` returns a JSON readiness payload for the hosted HTTP runtime.
- `GET /beta` renders the self-serve hosted beta portal when signup is enabled.
- `GET /auth/github/start` and `GET /auth/github/callback` own the GitHub OAuth
  login flow for the hosted beta portal.
- `POST /beta/api-key/regenerate` rotates the active hosted beta API key.
- `POST /beta/logout` clears the hosted beta session cookie.
- `/healthz` is public even when hosted bearer auth is enabled for `/mcp`.
- Hosted beta deployments on Railway use a generated public domain, and the MCP
  URL is always `<POLICYNIM_MCP_PUBLIC_BASE_URL>/mcp`.
- Hosted self-serve portal deployments on Railway use the same public domain, and
  the beta portal URL is always `<POLICYNIM_MCP_PUBLIC_BASE_URL>/beta`.
- Hosted `streamable-http` startup fails fast when
  `POLICYNIM_MCP_PUBLIC_BASE_URL` is set and the configured local index is
  missing or empty.

Shared interface guarantees:

- CLI `search` and MCP `policy_search` use the same `SearchResult` shape.
- CLI `preflight` and MCP `policy_preflight` use the same `PreflightResult`
  shape.
- CLI `runtime decide` and `runtime execute` use the same `RuntimeActionRequest`
  input shape.
- CLI `evidence report` returns a typed session summary over the SQLite runtime
  evidence store.
- top-k validation is shared and explicit.
- runtime setup failures are not masked as insufficient context.
- hosted HTTP auth applies only to `/mcp`, never to `stdio`.
- hosted beta auth stores one active API key per account in a local SQLite file
  mounted on the Railway auth volume.
- hosted tool logs emit JSON lines with auth result, tool name, latency, and
  classified upstream NVIDIA failure cause.

## Runtime Boundaries

### NVIDIA-Owned Steps

NVIDIA-hosted APIs are used for:

- document embeddings during ingest
- query embeddings during search and preflight
- reranking retrieved candidates
- grounded generation for preflight

These steps require `NVIDIA_API_KEY`.

### Runtime Policy Decisions

PolicyNIM also compiles and enforces runtime policy rules locally:

1. ingest compiles `runtime_rules` frontmatter into a deterministic runtime
   rules artifact
2. `RuntimeDecisionService` loads the local index plus the compiled rules
   artifact to return allow, confirm, or block decisions with citations
3. `RuntimeExecutionService` can execute the sanitized action after any required
   confirmation
4. immutable runtime evidence events are appended to the local SQLite evidence
   store
5. `RuntimeEvidenceReportService` summarizes one stored session for the CLI
   reporting flow

Runtime decisions are intentionally local-first. They depend on the same
indexed policy corpus, but they do not call NVIDIA-hosted APIs directly.

### Local-Only Steps

Local runtime components own:

- policy discovery and parsing
- chunk assembly and citation spans
- compiled runtime rules artifacts
- runtime execution evidence in SQLite
- LanceDB persistence and dense candidate lookup
- baked-index startup validation for hosted HTTP images
- offline eval execution
- local artifact persistence under `data/`

## Failure States That Must Stay Explicit

- missing or invalid `NVIDIA_API_KEY`
- malformed policy frontmatter
- duplicate effective policy IDs
- missing or empty local index
- missing runtime rules artifact
- malformed provider responses
- invalid grounded citation references
- CLI and MCP payload drift

Only weak or invalid grounded evidence should become `insufficient_context=true`.
Setup and runtime failures should remain actionable errors.

## Current Deferrals

- No default CI path for live NVIDIA end-to-end verification.
- No multi-user or shared remote index model.
- No hybrid lexical plus vector retrieval layer.
- No custom evaluation dashboard beyond Evidently OSS.

Those are deferred to keep the repo tutorial-friendly, explicit, and easy to audit.
