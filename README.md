# PolicyNIM

[![Built with NVIDIA NIM](https://img.shields.io/badge/Built%20with-NVIDIA%20NIM-76B900?logo=nvidia&logoColor=white)](https://docs.nvidia.com/nim/)

PolicyNIM is a policy-aware engineering preflight layer for AI coding agents.

The intended workflow is simple:

1. Index a small, grounded policy corpus.
2. Retrieve relevant policy chunks for a task.
3. Synthesize implementation guidance that keeps citations attached and fails
   closed when grounding is weak.

PolicyNIM ships as a small Python-first repo with two public surfaces:

- a JSON-first CLI for local developer workflows
- an MCP server for agent integrations such as Codex and Claude Code

## What Works Today

- Deterministic Markdown ingest with heading-aware chunking and source line spans.
- NVIDIA-hosted embeddings for document and query vectors. See NVIDIA's
  [API Catalog Quickstart Guide](https://docs.api.nvidia.com/nim/docs/api-quickstart)
  and the current
  [`nvidia/llama-nemotron-embed-1b-v2` model reference](https://docs.api.nvidia.com/nim/reference/nvidia-llama-nemotron-embed-1b-v2).
- Local LanceDB storage for the retrievable policy index. If you want more detail
  on what LanceDB is doing here, start with the
  [LanceDB quickstart](https://docs.lancedb.com/quickstart).
- NVIDIA-hosted reranking for better retrieval ordering.
- Grounded preflight synthesis with citation validation and fail-closed fallback.
- JSON-first CLI commands for `ingest`, `dump-index`, `search`, `preflight`,
  `eval`, and `mcp`.
- MCP tools for `policy_preflight` and `policy_search`.
- Hosted HTTP `streamable-http` with a public `/healthz` readiness route and
  optional bearer auth on `/mcp`.
- Offline-first evaluation with rerank on/off comparison and local Evidently UI.

## Repo Guide

- [docs/architecture.md](docs/architecture.md): package boundaries, runtime flow,
  and interface rules
- [docs/architecture-diagram.md](docs/architecture-diagram.md): Mermaid diagram
  of the current package layout and runtime flow
- [docs/demo-script.md](docs/demo-script.md): step-by-step demo for the hero use case
- [docs/limitations.md](docs/limitations.md): current product limits and non-goals
- [docs/public-source-grounding.md](docs/public-source-grounding.md): provenance
  notes for the shipped sample corpus
- [tests/README.md](tests/README.md): current automated coverage
- [examples/codex/README.md](examples/codex/README.md): Codex MCP setup example
- [examples/claude-code/README.md](examples/claude-code/README.md): Claude Code
  MCP setup example

## Public Surface

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

### Hosted HTTP

- `GET /healthz` reports local index readiness when using `streamable-http`.
- `POLICYNIM_MCP_REQUIRE_AUTH` and `POLICYNIM_MCP_BEARER_TOKENS` protect only
  the HTTP `/mcp` route.
- Hosted `streamable-http` startup fails fast when
  `POLICYNIM_MCP_PUBLIC_BASE_URL` is set but the configured local index is
  missing or empty.

## What To Run First

If you want the shortest path from clone to a real preflight run:

```bash
uv sync
cp .env.example .env
uv run policynim ingest
uv run policynim search --query "refresh token cleanup background job"
uv run policynim preflight --task "Implement a refresh-token cleanup background job"
uv run policynim eval --headless
uv run policynim mcp --transport stdio
```

Notes:

- `NVIDIA_API_KEY` must be set before `ingest`, `search`, or `preflight`. NVIDIA's
  official
  [API Catalog Quickstart Guide](https://docs.api.nvidia.com/nim/docs/api-quickstart)
  shows the API-key flow, and the
  [Build catalog](https://build.nvidia.com/) is where developers can browse models
  and use the "Get API Key" flow.
- Copying `.env.example` intentionally leaves `POLICYNIM_CORPUS_DIR` unset, so the
  bundled sample corpus is used by default. Add `POLICYNIM_CORPUS_DIR=/abs/path`
  yourself only if you want to index a different corpus.
- `eval` defaults to offline mode, so it can run without NVIDIA credentials.
- `policynim mcp` works without `--transport`; it defaults to `stdio`.
- Add `--transport streamable-http` only if you want the HTTP transport instead of
  the default `stdio` server.
- `mcp` starts the PolicyNIM server only; an agent client such as Codex or Claude
  Code connects to that server separately.

## Setup

### Runtime Workflow

Install the runtime dependencies:

```bash
uv sync
```

Copy the example environment file:

```bash
cp .env.example .env
```

Then set `NVIDIA_API_KEY` in `.env` or your shell. For the official key-creation
flow, use NVIDIA's
[API Catalog Quickstart Guide](https://docs.api.nvidia.com/nim/docs/api-quickstart)
and [Build catalog](https://build.nvidia.com/).

If you want to index a custom policy directory instead of the bundled sample
corpus, add `POLICYNIM_CORPUS_DIR=/abs/path/to/policies` manually to `.env`.

Important runtime settings:

- `NVIDIA_API_KEY`
- `POLICYNIM_CORPUS_DIR`
- `POLICYNIM_LANCEDB_URI`
- `POLICYNIM_LANCEDB_TABLE`
- `POLICYNIM_DEFAULT_TOP_K`
- `POLICYNIM_MCP_HOST`
- `POLICYNIM_MCP_PORT`
- `POLICYNIM_MCP_REQUIRE_AUTH`
- `POLICYNIM_MCP_BEARER_TOKENS`
- `POLICYNIM_MCP_PUBLIC_BASE_URL`
- `POLICYNIM_EVAL_UI_PORT`

Model references used by the default config in `.env.example`:

- embeddings:
  [`nvidia/llama-nemotron-embed-1b-v2`](https://docs.api.nvidia.com/nim/reference/nvidia-llama-nemotron-embed-1b-v2)
- reranking:
  [`nvidia/llama-nemotron-rerank-1b-v2`](https://docs.api.nvidia.com/nim/reference/nvidia-llama-nemotron-rerank-1b-v2-infer)
- grounded generation:
  [NVIDIA LLM API reference](https://docs.api.nvidia.com/nim/reference/llm-apis)

Leave `POLICYNIM_CORPUS_DIR` unset to use the bundled sample corpus.

### Container Build For Hosted HTTP

Build the production image with a build-time NVIDIA key so the index is baked
into the image:

```bash
docker build --build-arg NVIDIA_API_KEY=$NVIDIA_API_KEY -t policynim-hosted .
```

Important container defaults:

- the image bakes the LanceDB index at `/app/data/lancedb-baked`
- the image sets `POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked`
- the image sets `POLICYNIM_MCP_HOST=0.0.0.0` so hosted HTTP can bind inside the container
- the final image does not store the build-time `NVIDIA_API_KEY`
- runtime `NVIDIA_API_KEY` is still required because live `search` and
  `preflight` call NVIDIA-hosted APIs

Example hosted run:

```bash
docker run --rm -p 8000:8000 \
  -e NVIDIA_API_KEY=$NVIDIA_API_KEY \
  -e POLICYNIM_MCP_REQUIRE_AUTH=true \
  -e POLICYNIM_MCP_BEARER_TOKENS=token-a \
  -e POLICYNIM_MCP_PUBLIC_BASE_URL=http://localhost:8000 \
  policynim-hosted
```

Quick hosted-image test loop:

```bash
docker run --rm -p 8000:8000 \
  -e NVIDIA_API_KEY=$NVIDIA_API_KEY \
  -e POLICYNIM_MCP_PUBLIC_BASE_URL=http://localhost:8000 \
  policynim-hosted
```

Then verify the hosted HTTP surface from another terminal:

```bash
curl http://localhost:8000/healthz
curl -i http://localhost:8000/mcp
curl -i -X POST http://localhost:8000/mcp
```

What to expect:

- `GET /healthz` returns `200` with a JSON payload that includes `ready: true`
  when the baked index is present and non-empty.
- A plain `GET /mcp` returns `406 Not Acceptable` because the client must accept
  `text/event-stream`.
- A plain `POST /mcp` returns `400 Invalid Content-Type header` because the route
  expects a valid MCP HTTP request, not an empty form post.
- If host port `8000` is already in use, publish another host port instead, for
  example `-p 8002:8000`, and update `POLICYNIM_MCP_PUBLIC_BASE_URL` to
  `http://localhost:8002`.

### Railway Beta Deploy

Day 3 adds a repo-owned [`railway.toml`](./railway.toml) so Railway uses the
root `Dockerfile` and probes `GET /healthz`.

Recommended beta setup:

1. Create one Railway service from this GitHub repo.
2. Set the Railway service variable `NVIDIA_API_KEY`.
3. Deploy once so the service becomes healthy on `/healthz`.
4. Generate a Railway public domain for that service.
5. Set these runtime variables and redeploy:
   - `POLICYNIM_MCP_REQUIRE_AUTH=true`
   - `POLICYNIM_MCP_BEARER_TOKENS=<beta-token>`
   - `POLICYNIM_MCP_PUBLIC_BASE_URL=https://<generated-domain>`

Important Day 3 hosted behavior:

- Railway injects `PORT`; PolicyNIM now uses that automatically unless
  `POLICYNIM_MCP_PORT` is explicitly set.
- The public beta MCP URL is always `https://<generated-domain>/mcp`.
- `/healthz` stays public for Railway health checks.
- `/mcp` returns `401 {"error":"Unauthorized."}` for missing or invalid bearer
  tokens.
- Hosted MCP logs now emit one JSON object per line for auth rejects and tool
  calls, including `auth_result`, `tool_name`, `latency_ms`, and
  `upstream_failure_class`.

Opt-in Railway smoke test:

```bash
export POLICYNIM_BETA_MCP_URL=https://<generated-domain>/mcp
export POLICYNIM_BETA_MCP_TOKEN=<beta-token>
uv run --group test pytest -q -m live tests/test_hosted_mcp_live.py
```

### Contributor Workflow

Install the lint and test groups in addition to the runtime dependencies:

```bash
uv sync --group test --group dev
uv run --group dev pre-commit install
```

Run the local quality gates:

```bash
uv run ruff check
uv run pytest -q
```

## Core Workflows

### 1. Build The Local Index

```bash
uv run policynim ingest
```

What this does:

- loads the bundled policy corpus, or the directory from `POLICYNIM_CORPUS_DIR`
- chunks the documents into stable, citeable sections
- embeds those chunks with NVIDIA-hosted embeddings
- rebuilds the local LanceDB table

Typical output includes the chunk count, document count, embedding model, and
index location.

### 2. Inspect The Indexed Corpus

```bash
uv run policynim dump-index
```

This is the fastest way to inspect the stored chunk IDs, section labels, line
spans, and raw chunk text. Add `| less` if the output is long.

### 3. Search The Corpus

```bash
uv run policynim search --query "refresh token cleanup background job" --top-k 5
```

`search` is the debug and discovery path. It returns a JSON `SearchResult` with
retrieved chunks, scores, and citation metadata.

Example with an explicit domain filter:

```bash
uv run policynim search \
  --query "refresh token cleanup background job" \
  --domain security \
  --top-k 5
```

### 4. Run Grounded Preflight

```bash
uv run policynim preflight \
  --task "Implement a refresh-token cleanup background job" \
  --top-k 5
```

`preflight` is the main workflow. It returns a JSON `PreflightResult` with:

- a grounded summary
- applicable policies
- implementation guidance
- review flags
- tests required
- citations
- `insufficient_context`

If citation validation fails or the grounded answer is too weak, PolicyNIM returns
`insufficient_context=true` instead of bluffing.

### 5. Run Evaluations

```bash
uv run policynim eval
```

Default behavior:

- runs the bundled eval suite in `offline` mode
- executes rerank-enabled and rerank-disabled runs
- writes JSON artifacts and HTML reports under `data/evals/workspace`
- starts the local Evidently UI on `http://localhost:8001`

Useful variants:

```bash
uv run policynim eval --headless
uv run policynim eval --no-compare-rerank
uv run policynim eval --mode live
```

`--mode live` requires `NVIDIA_API_KEY` and uses an isolated temporary index so it
does not overwrite the normal runtime index.

### 6. Run The MCP Server

```bash
uv run policynim mcp --transport stdio
```

`policynim mcp` without any flags starts the same server with the default `stdio`
transport:

```bash
uv run policynim mcp
```

For HTTP transport:

```bash
uv run policynim mcp --transport streamable-http
```

Use `POLICYNIM_MCP_HOST` and `POLICYNIM_MCP_PORT` if you want something other than
the default `127.0.0.1:8000`.

Hosted HTTP notes:

- `GET /healthz` is a public readiness endpoint. It returns `200` only when the
  configured local index exists and contains rows; otherwise it returns `503`.
- If `POLICYNIM_MCP_PUBLIC_BASE_URL` is set, hosted `streamable-http` startup
  now fails before serving traffic when the configured local index is missing or
  empty.
- To protect only the HTTP MCP route, set:
  - `POLICYNIM_MCP_REQUIRE_AUTH=true`
  - `POLICYNIM_MCP_BEARER_TOKENS=token-a,token-b`
  - `POLICYNIM_MCP_PUBLIC_BASE_URL=https://your-host`
- `POLICYNIM_MCP_PUBLIC_BASE_URL` must be a service origin, not a full `/mcp`
  URL.
- `stdio` ignores the hosted auth settings completely.
- The baked-image workflow uses `POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked`
  and does not run `policynim ingest` at container startup.

Example readiness check:

```bash
curl http://localhost:8000/healthz
```

For client setup examples, see:

- [examples/codex/README.md](examples/codex/README.md)
- [examples/claude-code/README.md](examples/claude-code/README.md)

## Retrieval And Grounding Model

PolicyNIM keeps the retrieval stack explicit:

1. chunk Markdown policies with stable IDs and line spans
2. embed query and document text with NVIDIA-hosted embeddings
3. retrieve dense candidates from LanceDB
4. rerank candidates with NVIDIA
5. generate grounded guidance from retained evidence only
6. validate every citation against retrieved chunks before returning results

The system is designed to fail closed:

- missing or invalid runtime configuration is surfaced as an explicit error
- missing index state is surfaced as an explicit error
- weak or invalid grounding becomes `insufficient_context=true`

## Troubleshooting

### Search Works But Preflight Falls Back

This usually means retrieval succeeded but grounded answer validation did not.
Inspect the raw search hits first:

```bash
uv run policynim search \
  --query "Implement a refresh-token cleanup background job" \
  --top-k 5 | jq
```

Then compare that with:

```bash
uv run policynim preflight \
  --task "Implement a refresh-token cleanup background job" \
  --domain security \
  --top-k 5 | jq
```

If `search` returns strong hits while `preflight` returns
`insufficient_context=true`, the failure is in grounded answer validation, not
indexing.

### Negative Search Scores

Negative final scores are expected once reranking is enabled. Search results
surface the raw reranker score; treat the score as an ordering signal where
higher is better for that query.

### Missing Index

If `search`, `preflight`, or MCP tool calls fail because the index is missing,
run:

```bash
uv run policynim ingest
```

### Missing NVIDIA Credentials

`ingest`, `search`, `preflight`, and live eval mode require `NVIDIA_API_KEY`.
Offline eval mode does not.

## Sample Corpus

The shipped policy corpus is synthetic internal-style guidance grounded in public
sources such as OWASP cheat sheets, SRE guidance, OpenTelemetry concepts, the
Twelve-Factor App methodology, and public API versioning guidance.

The provenance notes for each shipped policy live in
[docs/public-source-grounding.md](docs/public-source-grounding.md).

## Limitations

Current limitations are intentional and documented:

- the system is local-first and aimed at a single developer workflow
- CI is offline-only and does not run live NVIDIA end-to-end checks
- the sample corpus is narrow and synthetic, not a broad enterprise handbook
- grounded answers may fail closed even when raw retrieval finds useful chunks
- evaluation is deterministic and gold-case driven, not a broad benchmark suite

See [docs/limitations.md](docs/limitations.md) for the full list.
