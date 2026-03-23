# PolicyNIM

PolicyNIM is a policy-aware engineering preflight layer for AI coding agents.

Before an agent writes code, it should be able to ask:

- What standards apply here?
- What review rules matter for this change?
- What architecture guidance should shape the implementation?

PolicyNIM is being built as a thin, NVIDIA-aligned developer tool:

- NVIDIA-hosted NIM APIs for embeddings, reranking, and grounded answer synthesis
- local OSS vector storage for a simple, public, no-GPU-required MVP
- CLI and MCP surfaces for agent workflows

## Day 1 Status

This branch establishes the foundation only:

- repo scaffold
- canonical CLI and MCP command surfaces
- typed contracts and core models
- initial synthetic policy corpus grounded in public standards

This branch does **not** yet implement retrieval, indexing, reranking, or grounded
generation.

## Why This Repo Exists

AI coding agents are good at generating code that looks plausible. They are much
worse at remembering team-specific policy, review expectations, architecture
constraints, and prior engineering lessons.

PolicyNIM aims to solve that by acting as a preflight layer between the coding task
and the code generator.

## Current Public Surface

### CLI

- `policynim preflight --task "..."`
- `policynim search --query "..."`
- `policynim mcp --transport stdio|streamable-http`

### MCP Tools

- `policy_preflight(task, domain?, top_k?)`
- `policy_search(query, domain?, top_k?)`

## Repo Layout

- `src/policynim/` contains the core package
- `policies/` contains the seed policy corpus
- `docs/architecture.md` explains import rules and package boundaries
- `examples/` will contain Codex and Claude Code MCP setup examples
- `tests/` will contain unit and integration coverage as implementation lands

## Local Workflow

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Copy the environment file and add your NVIDIA API key later:

   ```bash
   cp .env.example .env
   ```

3. Inspect the scaffolded CLI:

   ```bash
   uv run policynim --help
   uv run policynim preflight --help
   uv run policynim search --help
   ```

4. Run the MCP server surface:

   ```bash
   uv run policynim mcp --transport stdio
   ```

## Sample Corpus

The initial policy corpus is synthetic team guidance, but each document is grounded
in public references such as OWASP cheat sheets, SRE guidance, and public API
guidelines. This keeps the repo public-safe while still feeling like a real internal
engineering handbook.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the Day 1 boundary rules and
package layout.

## Planned Next Steps

- Day 2: frontmatter parsing and chunking
- Day 3: embeddings and local vector indexing
- Day 4: reranking and grounded synthesis
- Day 5: full MCP workflow and example clients
- Day 6: evals and tests
- Day 7: README polish, demo flow, and CI

