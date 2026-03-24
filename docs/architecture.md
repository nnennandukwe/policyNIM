# PolicyNIM Architecture Notes

## Purpose

PolicyNIM is a policy-aware preflight layer for AI coding agents. The Day 1 goal is
to lock the repo boundaries and public surfaces before retrieval logic exists. Day 2
adds a format-aware ingest foundation for tolerant Markdown parsing and deterministic
chunk generation. Day 3 adds hosted embeddings plus a local vector index. Day 4 adds
reranking and grounded synthesis internally while keeping public `preflight`
surfaces deferred. Day 5 wires those public CLI and MCP surfaces to the grounded
service layer without changing the underlying retrieval architecture. Day 6 adds
an offline-first eval harness, rerank on/off comparison, and local results
inspection through Evidently OSS.

## Design Principles

- Keep the code path explicit and readable.
- Make NVIDIA usage visible in the architecture and docs.
- Prefer a few real seams over speculative abstraction layers.
- Use shared typed contracts so the CLI and MCP surfaces do not drift.

## Package Boundaries

### `src/policynim/settings.py`

- The only module allowed to read environment variables.
- Exposes validated application settings to the rest of the package.

### `src/policynim/types.py`

- Shared typed models for requests, results, citations, and policy metadata.
- Contains no file I/O, environment access, or transport-specific code.

### `src/policynim/contracts.py`

- Defines the real external seams for later implementation:
  - embeddings
  - reranking
  - generation
  - index storage
- Keeps service orchestration independent from provider-specific details.

### `src/policynim/ingest/`

- Owns document discovery, parsing, metadata normalization, and chunk assembly.
- Defines the parser seam used to support Markdown now and other source formats
  later.
- Must not import CLI or MCP modules.

### `src/policynim/services/`

- Application-layer orchestration lives here.
- Services may depend on `types`, `contracts`, and `settings`.
- `IngestService` owns policy loading, chunking, embedding, and index rebuild.
- `SearchService` owns query embedding, dense candidate retrieval, and reranking.
- `PreflightService` owns dense retrieval, reranking, grounded synthesis, citation
  validation, and insufficient-context fallback.
- `EvalService` owns gold-case loading, search/preflight scoring, rerank
  comparison, and report persistence.
- Services must not import CLI or MCP modules.

### `src/policynim/providers/`

- Provider-specific API adapters live here.
- `nvidia.py` owns NVIDIA auth, retries, timeouts, endpoint construction, response
  validation, and lifecycle of internally created SDK/HTTP clients.
- Day 4 extends `nvidia.py` to own reranking and grounded chat-generation adapters.
- Provider adapters may accept injected SDK/HTTP clients for tests or advanced
  callers, but they only close clients they create themselves.
- Providers must not import CLI or MCP modules.

### `src/policynim/storage/`

- Local persistence adapters live here.
- `lancedb.py` owns table replacement, row mapping, and vector search behavior.
- Storage adapters must not read environment variables directly.

### `src/policynim/interfaces/`

- Transport-specific entry points live here.
- `cli.py` owns terminal-facing command definitions and help text.
- `mcp.py` owns MCP server and tool registration.
- Interface modules may call services, but not providers directly.
- `eval-ui` remains a CLI-only transport helper; there is no MCP eval surface.

## Import Rules

- `settings.py` imports standard library plus `pydantic-settings`.
- `types.py` imports standard library and `pydantic`.
- `contracts.py` imports `types.py` and the standard library only.
- `services/` may import `settings.py`, `types.py`, and `contracts.py`.
- `providers/` may import `settings.py`, `contracts.py`, `errors.py`, and SDK clients.
- `storage/` may import `contracts.py`, `types.py`, and `errors.py`.
- `ingest/` may import `types.py` and `errors.py`, plus format-specific parsing
  dependencies.
- `interfaces/` may import `services/`, `settings.py`, and `types.py`.
- Future provider and storage adapters must not import `interfaces/`.

## Public Surface Locked On Day 1

### CLI

- `policynim ingest`
- `policynim preflight --task ...`
- `policynim search --query ...`
- `policynim mcp --transport stdio|streamable-http`

### MCP Tools

- `policy_preflight(task, domain?, top_k?)`
- `policy_search(query, domain?, top_k?)`

## Failure States To Design For Early

- Missing NVIDIA API key.
- Invalid NVIDIA API key.
- Invalid policy frontmatter.
- Duplicate effective policy IDs in the corpus.
- Off-template documents with missing metadata.
- Empty or missing local index.
- Weak retrieval evidence once retrieval exists.
- Public-surface drift between CLI JSON, MCP payloads, and docs.

## Day 2 Ingest Rules

- Frontmatter is preferred, not required.
- The Markdown parser must tolerate imperfect but readable Markdown instead of
  assuming strict template compliance.
- Citation spans must remain stable and map to original source line numbers.
- Section extraction should preserve heading ancestry so chunk labels are useful to
  humans and downstream retrieval.
- Future non-Markdown formats should plug into the parser seam without forcing a
  rewrite of metadata normalization or chunk assembly.

## Day 4 Retrieval Rules

- `ingest` rebuilds the local LanceDB table on every run instead of incrementally
  merging rows.
- The NVIDIA API key is required for both document embedding and query embedding.
- The same embedding model must be used for documents and queries so vector
  dimensions always match.
- `search` retrieves dense candidates, reranks them with NVIDIA, and returns
  JSON-first `SearchResult` payloads.
- The internal preflight flow must validate citation IDs against retained chunks
  before surfacing grounded guidance.
- If grounding is weak or invalid, the service should fall back to
  `insufficient_context=true` instead of fabricating guidance.
- The public `preflight` CLI and MCP tools remain deferred to Day 5.

## Day 5 Public Interface Rules

- `cli.py` and `mcp.py` stay thin transport adapters over the service layer.
- Public CLI preflight output is JSON-first and uses the exact `PreflightResult`
  shape produced by the shared typed models.
- `policy_preflight` returns the same `PreflightResult` shape as the CLI JSON.
- `policy_search` returns the same `SearchResult` shape as the CLI JSON.
- MCP tool handlers construct services per call from current settings instead of
  holding long-lived provider or service state in the interface layer.
- Missing index and configuration failures remain explicit public errors; only
  weak grounded evidence becomes `insufficient_context=true`.

## Day 6 Evaluation Rules

- `policynim eval` is the public evaluation entrypoint.
- Offline mode is the default contributor workflow and must not require
  `NVIDIA_API_KEY`.
- Live mode must use an isolated temporary LanceDB path instead of mutating the
  caller's normal runtime index.
- Eval scoring stays deterministic and code-based:
  - `search` cases score expected `chunk_id` recall
  - `preflight` cases score expected `policy_id` recall
  - `insufficient_context` mismatches fail the case
- Rerank comparison runs the same suite with reranking enabled and disabled, then
  persists both results for side-by-side comparison.
- Evidently integration belongs in the eval service and CLI workflow, not in
  provider or storage adapters.

## Current Deferrals

- No CI-level live end-to-end MCP verification yet.
- No custom eval dashboard beyond Evidently OSS.

Those land later so the repo foundation stays small, reviewable, and easy to
teach from.
