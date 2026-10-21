# PolicyNIM Demo Script

This demo uses the hero task:

`Implement a refresh-token cleanup background job.`

The goal is to show that PolicyNIM can:

1. build a local policy index
2. retrieve relevant policy evidence
3. return selected whole-policy quotations with citations
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
- the local SQLite index path and table name

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
- the search surface exposes discovery evidence before whole-source compilation

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

- a fixed service summary explaining that selected sources are quoted in full
- applicable policies such as auth review, background jobs, token boundaries, and
  logging rules
- whole policy bodies in `plan_steps`, including their conditions and exceptions
- empty `implementation_guidance`, `review_flags` and `tests_required` lists
- citations with chunk IDs and source line spans

What this proves:

- retrieval and source-extractive preflight share pinned policy evidence
- whole-source quotations retain canonical citations and owner metadata

Review applicability and source trust separately. The
[source-extractive guide](source-extractive-guidance.md) explains field semantics,
budgets and the limits of source fidelity.

## Step 4: Show Fail-Closed Behavior

Use a deliberately vague task:

```bash
uv run policynim preflight --task "Make the system better" --top-k 5 | jq
```

What to look for:

- `insufficient_context: true`, or a much weaker grounded result

What this proves:

- PolicyNIM doesn't always force an answer
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

## Step 6: Show Hosted MCP Availability

If you have an issued beta token and Railway domain, connect your client to the
hosted MCP first:

```bash
export POLICYNIM_TOKEN=<issued-beta-token>
codex mcp add policynim --url https://<railway-domain>/mcp --bearer-token-env-var POLICYNIM_TOKEN
claude mcp add --transport http policynim https://<railway-domain>/mcp --header "Authorization: Bearer $POLICYNIM_TOKEN"
```

Then point the demo prompts at the hosted service using one of the existing examples:

- [examples/codex/README.md](../examples/codex/README.md)
- [examples/claude-code/README.md](../examples/claude-code/README.md)

What to say during the demo:

- `policy_preflight` is the main workflow
- `policy_search` is the raw retrieval and debugging workflow
- both surfaces return the same typed payloads used by the CLI

## Optional Step 7: Show Local MCP And Hosted HTTP Readiness

If you want to demonstrate the local repo path instead of the hosted beta, start
the local server with:

```bash
uv run policynim mcp --transport stdio
```

If you want to demonstrate the local hosted HTTP transport instead of `stdio`,
start the server with:

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

### Invalid Token

If the hosted MCP returns `401 {"error":"Unauthorized."}`, re-check
`POLICYNIM_TOKEN` or ask the beta operator for a new token.

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

### Temporary Upstream NVIDIA Failure

If a hosted tool call fails even though `/healthz` is healthy, retry after a
short delay first. If the issue persists, inspect the hosted logs for
`upstream_failure_class`.

### Insufficient Context

A response with `insufficient_context: true` can reflect weak retrieval, missing
complete-source ownership or rejected source selection/metadata. Inspect `--trace`,
narrow the task or domain, or use `policy_search` to inspect discovery evidence.
This result alone doesn't identify the failed boundary.

### Service Unavailable

If the hosted service doesn't respond or `/healthz` returns `503`, the hosted
service or baked index isn't ready yet. Retry after the service becomes
healthy.

## Suggested Demo Narrative

1. Build the index to show the corpus becomes a local, inspectable asset.
2. Run `search` to inspect retrieved policy evidence.
3. Run `preflight` to inspect whole policy quotations with canonical citations.
4. Show one fail-closed case to make the safety behavior visible.
5. End with either eval or MCP, depending on whether the audience cares more about
   local quality checks or agent integration.
