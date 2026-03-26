# PolicyNIM Architecture

## Purpose

PolicyNIM is a policy-aware preflight layer for AI coding agents. It turns a small
Markdown policy corpus into grounded implementation guidance with citations, while
keeping the public system easy to inspect from the terminal.

The architecture stays intentionally small:

- typed contracts shared by CLI and MCP
- explicit provider and storage adapters
- application services for ingest, retrieval, preflight, and evaluation
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

### `src/policynim/settings.py`

- The only module that reads environment variables directly.
- Exposes validated application settings to the rest of the package.

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

- Owns LanceDB persistence, row mapping, replacement, and search behavior.
- Must not read environment variables directly.

### `src/policynim/services/`

- Owns application orchestration.
- `IngestService` handles parse, chunk, embed, and rebuild.
- `SearchService` handles query embedding, retrieval, and reranking.
- `PreflightService` handles retrieval, grounded synthesis, and citation
  validation.
- `EvalService` handles gold-case execution, scoring, comparison, and report
  persistence.
- Services may depend on `settings`, `types`, `contracts`, providers, and storage,
  but they must not import CLI or MCP modules.

### `src/policynim/interfaces/`

- Owns transport-specific entry points only.
- `cli.py` defines terminal-facing commands and help text.
- `mcp.py` defines the MCP tool surface and server startup.
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

### MCP Tools

- `policy_preflight(task, domain?, top_k?)`
- `policy_search(query, domain?, top_k?)`

Shared interface guarantees:

- CLI `search` and MCP `policy_search` use the same `SearchResult` shape.
- CLI `preflight` and MCP `policy_preflight` use the same `PreflightResult`
  shape.
- top-k validation is shared and explicit.
- runtime setup failures are not masked as insufficient context.

## Runtime Boundaries

### NVIDIA-Owned Steps

NVIDIA-hosted APIs are used for:

- document embeddings during ingest
- query embeddings during search and preflight
- reranking retrieved candidates
- grounded generation for preflight

These steps require `NVIDIA_API_KEY`.

### Local-Only Steps

Local runtime components own:

- policy discovery and parsing
- chunk assembly and citation spans
- LanceDB persistence and dense candidate lookup
- offline eval execution
- local artifact persistence under `data/`

## Failure States That Must Stay Explicit

- missing or invalid `NVIDIA_API_KEY`
- malformed policy frontmatter
- duplicate effective policy IDs
- missing or empty local index
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
