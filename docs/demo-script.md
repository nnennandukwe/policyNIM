# PolicyNIM Demo Script

This demo uses the hero task:

`Implement a refresh-token cleanup background job.`

The goal is to show that PolicyNIM can:

1. build a local policy index
2. retrieve relevant policy evidence
3. return grounded preflight guidance with citations
4. expose the same workflow through MCP for agent clients

## Prerequisites

Install the repo and configure runtime credentials:

```bash
uv sync
cp .env.development.example .env
```

Set `NVIDIA_API_KEY` in `.env` or your shell before running the live retrieval
commands below.

## Step 1: Build The Index

Command:

```bash
uv run policynim ingest
```

What to look for:

- a non-zero document count
- a non-zero chunk count
- the configured embedding model
- the local LanceDB path and table name

What this proves:

- the corpus was discovered and parsed
- chunking and embedding completed successfully
- the local index is ready for retrieval

## Step 2: Show Raw Retrieval

Command:

```bash
uv run policynim search \
  --query "refresh token cleanup background job" \
  --top-k 5 | jq
```

Optional tighter version:

```bash
uv run policynim search \
  --query "refresh token cleanup background job" \
  --domain security \
  --top-k 5 | jq
```

What to look for:

- hits from the auth, session, logging, background-job, or tracing policies
- stable `chunk_id`, `path`, `section`, and `lines` metadata
- `insufficient_context: false` when retrieval succeeds

What this proves:

- dense retrieval and reranking are working
- the stored corpus is inspectable and citeable
- the search surface is useful as a debug path before synthesis

## Step 3: Show Grounded Preflight

Command:

```bash
uv run policynim preflight \
  --task "Implement a refresh-token cleanup background job" \
  --top-k 5 | jq
```

Optional tighter version:

```bash
uv run policynim preflight \
  --task "Implement a refresh-token cleanup background job" \
  --domain security \
  --top-k 5 | jq
```

What to look for:

- a concise summary tied to the task
- applicable policies such as auth review, background jobs, token boundaries, and
  logging rules
- implementation guidance, review flags, and required tests
- citations with chunk IDs and source line spans

What this proves:

- retrieval and grounded synthesis are integrated
- the answer is not freeform; it is evidence-backed
- PolicyNIM can return implementation guidance while preserving traceability

## Step 4: Show Fail-Closed Behavior

Use a deliberately vague task:

```bash
uv run policynim preflight --task "Make the system better" --top-k 5 | jq
```

What to look for:

- `insufficient_context: true`, or a much weaker grounded result

What this proves:

- PolicyNIM does not always force an answer
- weak evidence can stop progression instead of generating plausible nonsense

## Step 5: Show Evaluation

Command:

```bash
uv run policynim eval --headless
```

What to look for:

- JSON output that summarizes the suite
- both rerank-enabled and rerank-disabled runs by default
- persisted artifacts under `data/evals/workspace`

What this proves:

- the project has a local regression workflow
- rerank impact is measured rather than assumed

Optional UI launch:

```bash
uv run policynim eval
```

Then open `http://localhost:8001`.

## Step 6: Show MCP Availability

Start the server:

```bash
uv run policynim mcp --transport stdio
```

Then point an MCP-capable client at it using one of the existing examples:

- [examples/codex/README.md](../examples/codex/README.md)
- [examples/claude-code/README.md](../examples/claude-code/README.md)

What to say during the demo:

- `policy_preflight` is the main workflow
- `policy_search` is the raw retrieval and debugging workflow
- both surfaces return the same typed payloads used by the CLI

## Optional Step 7: Show Hosted HTTP Readiness

If you want to demonstrate the hosted HTTP transport instead of `stdio`, start
the server with:

```bash
uv run policynim mcp --transport streamable-http
```

Then check readiness from another terminal:

```bash
curl http://127.0.0.1:8000/healthz
```

What to look for:

- `ready: true` when the local index exists and has rows
- `ready: false` plus a reason when the index is missing or empty
- `mcp_url` when `POLICYNIM_MCP_PUBLIC_BASE_URL` is configured

If you enable hosted auth, set:

- `POLICYNIM_MCP_REQUIRE_AUTH=true`
- `POLICYNIM_MCP_BEARER_TOKENS=token-a,token-b`
- `POLICYNIM_MCP_PUBLIC_BASE_URL=https://your-host`

## Failure Recovery Notes

### Missing Index

If `search`, `preflight`, or an MCP tool call fails with a missing-index error,
recover by running:

```bash
uv run policynim ingest
```

### Missing NVIDIA API Key

If `ingest`, `search`, or `preflight` fails early due to configuration, set
`NVIDIA_API_KEY` and retry.

Offline recovery option:

```bash
uv run policynim eval --headless
```

This still demonstrates the evaluation workflow without live NVIDIA calls.

## Suggested Demo Narrative

1. Build the index to show the corpus becomes a local, inspectable asset.
2. Run `search` to show concrete evidence before any synthesis.
3. Run `preflight` to show grounded implementation guidance with citations.
4. Show one fail-closed case to make the safety behavior visible.
5. End with either eval or MCP, depending on whether the audience cares more about
   local quality checks or agent integration.
