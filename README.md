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

## Current Status

The repo currently includes the retrieval stack through grounded internal synthesis:

- deterministic Markdown ingest and chunking
- NVIDIA-hosted embeddings for document and query vectors
- NVIDIA-hosted reranking and grounded generation adapters
- local LanceDB storage for the chunk index
- `policynim ingest` to build the local index
- `policynim dump-index` to inspect indexed chunks directly
- reranked JSON-first `policynim search` over the indexed corpus
- an internal grounded preflight pipeline that validates citations before surfacing results

The public `preflight` CLI command and MCP tools are present, but they still return
`NotImplementedYet` until the internal preflight service is wired to those entrypoints.

## Why This Repo Exists

AI coding agents are good at generating code that looks plausible. They are much
worse at remembering team-specific policy, review expectations, architecture
constraints, and prior engineering lessons.

PolicyNIM aims to solve that by acting as a preflight layer between the coding task
and the code generator.

## Current Public Surface

### CLI

- `policynim ingest`
- `policynim dump-index`
- `policynim search --query "..."`
- `policynim preflight --task "..."` is exposed but currently returns not implemented
- `policynim mcp --transport stdio|streamable-http`

### MCP Tools

- `policy_preflight(task, domain?, top_k?)` is registered but currently returns not implemented
- `policy_search(query, domain?, top_k?)` is registered but currently returns not implemented

## Repo Layout

- `src/policynim/` contains the core package
- `policies/` contains the seed policy corpus
- `docs/architecture.md` explains import rules and package boundaries
- `examples/` contains Codex and Claude Code MCP setup examples
- `tests/` contains unit and integration coverage for ingest, search, providers, CLI, and runtime paths

## Local Workflow

1. Install dependencies:

   ```bash
   uv sync
   ```

   For contributor hooks and local linting tools:

   ```bash
   uv sync --group dev
   uv run --group dev pre-commit install
   ```

2. Copy the environment file and add your NVIDIA API key:

   ```bash
   cp .env.example .env
   ```

   `POLICYNIM_CORPUS_DIR` is optional. Leave it unset to use the bundled sample
   corpus, or point it at another directory of policy Markdown files.

3. Build the local index:

   ```bash
   uv run policynim ingest
   ```

4. Query the indexed corpus:

   ```bash
   uv run policynim search --query "refresh token cleanup background job" --top-k 5
   ```

5. Dump all indexed chunks in the terminal:

   ```bash
   uv run policynim dump-index
   ```

   add ` | less` to command for paging large output.

6. Inspect the CLI and MCP surfaces:

   ```bash
   uv run policynim --help
   uv run policynim preflight --help
   uv run policynim search --help
   ```

7. Run the MCP server surface:

   ```bash
   uv run policynim mcp --transport stdio
   ```

8. Run tests and lint:

   ```bash
   uv run pytest -q
   uv run ruff check
   ```

## Commit Hooks

- This repo uses `pre-commit` with Ruff for commit-time linting and formatting.
- Install the hooks once per clone:

  ```bash
  uv sync --group dev
  uv run --group dev pre-commit install
  ```

- Run the hooks manually across the repo:

  ```bash
  uv run --group dev pre-commit run --all-files
  ```

## Retrieval Workflow

- `policynim ingest` loads the shipped `policies/` corpus, chunks the documents,
  or the directory configured by `POLICYNIM_CORPUS_DIR`, sends chunk text to
  NVIDIA embeddings, and rebuilds the local LanceDB table.
- `policynim dump-index` prints every stored chunk from the local LanceDB table in a
  terminal-friendly format so you can inspect the indexed corpus directly.
- `policynim search` embeds the query with the same NVIDIA model, retrieves dense
  candidates, reranks them with NVIDIA, and prints a JSON `SearchResult`.
- Reranking uses `POLICYNIM_NVIDIA_RETRIEVAL_BASE_URL` as the retrieval API root
  and joins the model-specific `reranking` path under that base.
- The internal preflight service uses the same retrieval flow, validates citation
  IDs against retained chunks, and falls back to insufficient context when grounding
  is weak or invalid.
- Both commands require `NVIDIA_API_KEY` because hosted embeddings are still part
  of the retrieval path.
- Provider adapters may accept injected SDK/HTTP clients for tests or advanced
  callers, but internally created clients remain adapter-owned and are closed by
  the adapter.

## Sample Corpus

The initial policy corpus is synthetic team guidance, but each document is grounded
in public references such as OWASP cheat sheets, SRE guidance, and public API
guidelines. This keeps the repo public-safe while still feeling like a real internal
engineering handbook.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the current package boundary
rules, provider ownership notes, and layout.

## Planned Next Steps

- Wire the internal grounded preflight service into the public CLI and MCP surfaces.
- Expand end-to-end verification for live NVIDIA-backed flows in CI.
- Continue polishing examples, demo flow, and deployment guidance for agent integrations.
