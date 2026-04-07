# PolicyNIM Workflow Handbook

Use this page for the detailed CLI, MCP, runtime, eval, and troubleshooting
reference that used to live in the root README.

## Public Surface

### CLI

- `policynim ingest`
- `policynim dump-index`
- `policynim search --query "..."`
- `policynim preflight --task "..."`
- `policynim eval --mode offline|live [--headless] [--no-compare-rerank]`
- `policynim mcp --transport stdio|streamable-http`
- `policynim runtime decide --input <path|->`
- `policynim runtime execute --input <path|->`
- `policynim evidence report --session-id <id>`

### MCP Tools

- `policy_preflight(task, domain?, top_k?)`
- `policy_search(query, domain?, top_k?)`

### Operator CLI

- `policynim beta-admin list-accounts`
- `policynim beta-admin suspend --github-login <login>`
- `policynim beta-admin resume --github-login <login>`
- `policynim beta-admin revoke-key --github-login <login>`

### Hosted HTTP

- `GET /healthz` reports local index readiness when using `streamable-http`
- `POLICYNIM_MCP_REQUIRE_AUTH` protects only the HTTP `/mcp` route
- `/beta` stays as the self-serve signup and key-management surface
- hosted `streamable-http` startup fails fast when
  `POLICYNIM_MCP_PUBLIC_BASE_URL` is set but the configured local index is
  missing or empty

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

This is the fastest way to inspect stored chunk IDs, section labels, line spans,
and raw chunk text. Add `| less` if the output is long.

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

If citation validation fails or the grounded answer is too weak, PolicyNIM
returns `insufficient_context=true` instead of bluffing.

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

`--mode live` requires `NVIDIA_API_KEY` and uses an isolated temporary index so
it does not overwrite the normal runtime index.

### 6. Run The MCP Server

```bash
uv run policynim mcp --transport stdio
```

`policynim mcp` without any flags starts the same server with the default
`stdio` transport:

```bash
uv run policynim mcp
```

For HTTP transport:

```bash
uv run policynim mcp --transport streamable-http
```

Use `POLICYNIM_MCP_HOST` and `POLICYNIM_MCP_PORT` if you want something other
than the default development bind `127.0.0.1:8000`.

Hosted HTTP notes:

- `GET /healthz` is a public readiness endpoint. It returns `200` only when the
  configured local index exists and contains rows; otherwise it returns `503`.
- hosted `streamable-http` startup checks the configured local index before
  serving traffic. If the baked index is missing or empty, it attempts one
  rebuild with the runtime `NVIDIA_API_KEY` and then fails fast if readiness is
  still not satisfied.
- to protect only the HTTP MCP route, set:
  - `POLICYNIM_MCP_REQUIRE_AUTH=true`
  - `POLICYNIM_MCP_PUBLIC_BASE_URL=https://your-host`
- for self-serve hosted beta signup, also set:
  - `POLICYNIM_BETA_SIGNUP_ENABLED=true`
  - `POLICYNIM_BETA_AUTH_DB_PATH=/app/state/auth.sqlite3`
  - `POLICYNIM_BETA_SESSION_SECRET=<random-secret>`
  - `POLICYNIM_BETA_GITHUB_CLIENT_ID=<github-oauth-client-id>`
  - `POLICYNIM_BETA_GITHUB_CLIENT_SECRET=<github-oauth-client-secret>`
- `POLICYNIM_MCP_BEARER_TOKENS=token-a,token-b` is optional and reserved for
  operator break-glass access when self-serve auth is enabled
- `POLICYNIM_MCP_PUBLIC_BASE_URL` must be a service origin, not a full `/mcp`
  URL
- `stdio` ignores the hosted auth settings completely
- when `POLICYNIM_ENV=production` and Railway injects `PORT`, hosted MCP
  defaults to `0.0.0.0` unless `POLICYNIM_MCP_HOST` is explicitly set
- the baked-image workflow uses `POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked`
  as the fast path. Hosted startup only falls back to `policynim ingest` when
  that local index is missing or empty
- if that automatic rebuild path runs without a runtime `NVIDIA_API_KEY`, hosted
  startup fails fast with an explicit recovery message instead of serving
  traffic

Example readiness check:

```bash
curl http://localhost:8000/healthz
```

For hosted-first client setup examples, see:

- [../examples/codex/README.md](../examples/codex/README.md)
- [../examples/claude-code/README.md](../examples/claude-code/README.md)

## Runtime Decisions And Evidence

PolicyNIM compiles `runtime_rules` frontmatter from the policy corpus into a
deterministic runtime-rules artifact during ingest.

Runtime-rule authoring contract:

- `runtime_rules` is optional frontmatter
- each rule uses `action`, `effect`, `reason`, and exactly one matcher family
- matcher families are `path_globs`, `command_regexes`, or `url_host_patterns`
- authored `effect` values are `confirm` or `block`
- allow is still a no-match runtime decision outcome, not an authored effect

That compiled artifact powers the runtime decision and execution services:

- `POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH` points at the compiled rule snapshot
- `POLICYNIM_RUNTIME_EVIDENCE_DB_PATH` points at the SQLite evidence store that
  records decision and execution events
- `POLICYNIM_RUNTIME_SHELL_TIMEOUT_SECONDS` sets the default shell timeout for
  runtime execution

The Day 4 runtime CLI flow is JSON-first and file- or stdin-driven:

```bash
# Decide without side effects.
cat <<'JSON' | uv run policynim runtime decide --input -
{
  "kind": "shell_command",
  "task": "Run the unit test suite.",
  "cwd": "/abs/path/to/repo",
  "command": ["make", "test"]
}
JSON

# Execute a file write with policy enforcement and durable evidence.
cat > request.json <<'JSON'
{
  "kind": "file_write",
  "task": "Write a local note with runtime evidence.",
  "cwd": "/abs/path/to/repo",
  "session_id": "runtime-session-1",
  "path": "notes/runtime.txt",
  "content": "hello from PolicyNIM"
}
JSON
uv run policynim runtime execute --input request.json

# Execute an HTTP request from stdin.
cat <<'JSON' | uv run policynim runtime execute --input -
{
  "kind": "http_request",
  "task": "Call the status endpoint.",
  "cwd": "/abs/path/to/repo",
  "method": "GET",
  "url": "https://example.com/healthz"
}
JSON

# Summarize the stored evidence for one execution session.
uv run policynim evidence report --session-id <session-id-from-runtime-execute>
```

Contract notes:

- `runtime decide` and `runtime execute` accept the same `RuntimeActionRequest`
  JSON schema
- supported action payloads are:
  - `shell_command`: `command: list[str]`
  - `file_write`: `path` and `content`
  - `http_request`: `method` and `url`
- `--input -` reads a single JSON object from stdin; otherwise `--input` must
  be a UTF-8 JSON file path
- non-object JSON, empty input, invalid JSON, and schema mismatches fail with
  explicit CLI errors
- `runtime execute` prints the resolved `session_id` in its JSON result
- caller-provided `session_id` is preserved; otherwise `runtime execute`
  generates one before evidence is persisted
- `runtime execute` exits `0` for `allowed` and `confirmed`
- `runtime execute` exits `1` for `blocked`, `refused`, and `failed`
- `evidence report` is summary-only: it aggregates one session from the SQLite
  runtime evidence store rather than dumping raw rows

SQLite runtime evidence notes:

- default runtime rules artifact path: `data/runtime/runtime_rules.json`
- default runtime evidence DB path: `data/runtime/runtime_evidence.sqlite3`
- persisted events live in the `runtime_execution_events` table
- `evidence report` is the supported operator surface for session summaries
- raw SQLite inspection is a debugging aid, not a formal public API

Useful inspection commands:

```bash
sqlite3 data/runtime/runtime_evidence.sqlite3 ".schema runtime_execution_events"
sqlite3 data/runtime/runtime_evidence.sqlite3 \
  "SELECT session_id, execution_id, event_kind, created_at FROM runtime_execution_events ORDER BY rowid DESC LIMIT 20;"
```

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
