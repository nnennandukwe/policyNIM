# PolicyNIM Architecture Notes

## Purpose

PolicyNIM is a policy-aware preflight layer for AI coding agents. The Day 1 goal is
to lock the repo boundaries and public surfaces before retrieval logic exists. Day 2
adds a format-aware ingest foundation for tolerant Markdown parsing and deterministic
chunk generation.

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
- Services must not import CLI or MCP modules.

### `src/policynim/interfaces/`

- Transport-specific entry points live here.
- `cli.py` owns terminal-facing command definitions and help text.
- `mcp.py` owns MCP server and tool registration.
- Interface modules may call services, but not providers directly.

## Import Rules

- `settings.py` imports standard library plus `pydantic-settings`.
- `types.py` imports standard library and `pydantic`.
- `contracts.py` imports `types.py` and the standard library only.
- `services/` may import `settings.py`, `types.py`, and `contracts.py`.
- `ingest/` may import `types.py` and `errors.py`, plus format-specific parsing
  dependencies.
- `interfaces/` may import `services/`, `settings.py`, and `types.py`.
- Future provider and storage adapters must not import `interfaces/`.

## Public Surface Locked On Day 1

### CLI

- `policynim preflight --task ...`
- `policynim search --query ...`
- `policynim mcp --transport stdio|streamable-http`

### MCP Tools

- `policy_preflight(task, domain?, top_k?)`
- `policy_search(query, domain?, top_k?)`

## Failure States To Design For Early

- Missing NVIDIA API key.
- Invalid policy frontmatter.
- Duplicate effective policy IDs in the corpus.
- Off-template documents with missing metadata.
- Empty or missing local index.
- Weak retrieval evidence once retrieval exists.
- Unimplemented workflow surfaces during Day 1 and Day 2.

## Day 2 Ingest Rules

- Frontmatter is preferred, not required.
- The Markdown parser must tolerate imperfect but readable Markdown instead of
  assuming strict template compliance.
- Citation spans must remain stable and map to original source line numbers.
- Section extraction should preserve heading ancestry so chunk labels are useful to
  humans and downstream retrieval.
- Future non-Markdown formats should plug into the parser seam without forcing a
  rewrite of metadata normalization or chunk assembly.

## Current Deferrals

- No retrieval pipeline.
- No vector store integration.
- No answer synthesis.
- No evaluation harness.

Those land later so the repo foundation stays small, reviewable, and easy to
teach from.
