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

The repo currently includes the retrieval stack through public grounded preflight:

- deterministic Markdown ingest and chunking
- NVIDIA-hosted embeddings for document and query vectors
- NVIDIA-hosted reranking and grounded generation adapters
- local LanceDB storage for the chunk index
- `policynim ingest` to build the local index
- `policynim dump-index` to inspect indexed chunks directly
- reranked JSON-first `policynim search` over the indexed corpus
- grounded JSON-first `policynim preflight` with citation validation and
  insufficient-context fallback
- live MCP tools for `policy_preflight` and `policy_search`
- offline-first `policynim eval` with rerank on/off comparison
- local Evidently-backed results viewing on `localhost` from `policynim eval`

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
- `policynim preflight --task "..."`
- `policynim eval --mode offline|live [--headless] [--no-compare-rerank]`
- `policynim mcp --transport stdio|streamable-http`

### MCP Tools

- `policy_preflight(task, domain?, top_k?)`
- `policy_search(query, domain?, top_k?)`

## Repo Layout

- `src/policynim/` contains the core package
- `policies/` contains the seed policy corpus
- `evals/` contains the bundled gold eval suite
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

5. Run grounded preflight:

   ```bash
   uv run policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5
   ```

6. Dump all indexed chunks in the terminal:

   ```bash
   uv run policynim dump-index
   ```

   add ` | less` to command for paging large output.

7. Inspect the CLI and MCP surfaces:

   ```bash
   uv run policynim --help
   uv run policynim preflight --help
   uv run policynim search --help
   ```

8. Run the eval suite and start the local UI:

   ```bash
   uv run policynim eval
   ```

   To skip the default rerank comparison:

   ```bash
   uv run policynim eval --no-compare-rerank
   ```

   To run evals without starting the UI:

   ```bash
   uv run policynim eval --headless
   ```

9. Open `http://localhost:8001` in your browser after `policynim eval` starts the
   local Evidently UI. If you do not want the UI to start automatically, use
   `--headless`.

10. Run the MCP server surface:

   ```bash
   uv run policynim mcp --transport stdio
   ```

   For streamable HTTP, set `POLICYNIM_MCP_HOST` and `POLICYNIM_MCP_PORT` if you
   do not want the default `127.0.0.1:8000`, then run:

   ```bash
   uv run policynim mcp --transport streamable-http
   ```

11. Run tests and lint:

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
- `policynim preflight` and `policy_preflight` use the same retrieval flow,
  validate citation IDs against retained chunks, and fall back to
  `insufficient_context=true` when grounding is weak or invalid.
- `policy_search` returns the same JSON-first `SearchResult` shape used by the CLI.
- Both commands require `NVIDIA_API_KEY` because hosted embeddings are still part
  of the retrieval path.
- Provider adapters may accept injected SDK/HTTP clients for tests or advanced
  callers, but internally created clients remain adapter-owned and are closed by
  the adapter.

## Evaluation Workflow

- `policynim eval` runs the bundled `evals/default_cases.json` suite by default.
- Offline mode is the default and uses deterministic service doubles, so it does
  not require `NVIDIA_API_KEY`.
- Live mode uses the real ingest, search, and grounded preflight stack and writes
  to an isolated temporary LanceDB path so your normal runtime index is not
  mutated.
- Each eval run records two result sets by default:
  - rerank enabled
  - rerank disabled
- `--no-compare-rerank` keeps only the default rerank-enabled run.
- The CLI prints JSON `EvalRunResult` output and also saves:
  - one JSON artifact per rerank mode under the eval workspace
  - one HTML report per rerank mode under the eval workspace
- `policynim eval` starts the local Evidently UI by default unless you pass
  `--headless`.

## Troubleshooting Retrieval

- Negative `score` values in `policynim search` are expected once reranking is on.
  LanceDB dense-search scores are non-negative, but final search results expose
  the raw NVIDIA rerank score. Treat the score as a per-query ordering signal:
  higher is better, even if all returned values are negative.
- `policynim search` can return useful hits while `policynim preflight` still
  returns `insufficient_context=true`. That means retrieval worked, but the
  grounded generation result did not survive citation validation against the
  retained chunks, so PolicyNIM intentionally suppressed the answer instead of
  fabricating guidance.
- If a broad task falls back to `insufficient_context=true`, retry with a focused
  task description or an explicit `--domain` such as `security` or `backend`.
  Example:

  ```bash
  uv run policynim preflight --task "Implement a refresh-token cleanup background job" --domain security --top-k 5
  ```

- A quick debugging sequence is:

  ```bash
  uv run policynim search --query "Implement a refresh-token cleanup background job" --top-k 5 | jq
  uv run policynim preflight --task "Implement a refresh-token cleanup background job" --domain security --top-k 5 | jq
  ```

  If `search` returns hits but `preflight` falls back, the issue is in grounded
  answer validation rather than indexing.

## Sample Corpus

The initial policy corpus is synthetic team guidance, but each document is grounded
in public references such as OWASP cheat sheets, SRE guidance, and public API
guidelines. This keeps the repo public-safe while still feeling like a real internal
engineering handbook.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the current package boundary
rules, provider ownership notes, and layout.

## Planned Next Steps

- Expand end-to-end verification for live NVIDIA-backed flows in CI.
- Continue polishing the demo flow and deployment guidance for agent integrations.
- Grow the bundled eval suite beyond the initial Day 6 gold cases.
