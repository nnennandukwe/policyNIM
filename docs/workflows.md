# PolicyNIM Workflow Handbook

Use this page for the detailed CLI, MCP, runtime, eval, and troubleshooting
reference that used to live in the root README.

## Public Surface

### CLI

- `policynim init`
- `policynim ingest`
- `policynim dump-index`
- `policynim search --query "..."`
- `policynim route --task "..."`
- `policynim compile --task "..."`
- `policynim preflight --task "..."`
- `policynim preflight --task "..." [--trace] [--regenerate] [--max-regenerations 1] [--backend nemo|nemo_evaluator|nat]`
- `policynim eval --mode offline|live [--backend default|nemo|nemo_evaluator|nat] [--regenerate] [--max-regenerations 1] [--headless] [--no-compare-rerank]`
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

### 0. Initialize A Standalone Install

```bash
policynim init
policynim ingest
```

Use this when you installed PolicyNIM instead of running from a source
checkout. It prompts for `NVIDIA_API_KEY` and an optional custom corpus
directory, then writes the standalone config file with user-owned data-path
defaults before building the local index.

Source checkouts can keep using `.env.development.example`, skip this step, and
use the later `uv run policynim ...` examples inside the uv-managed project
environment. Installed copies should keep using the direct `policynim ...`
entrypoint.

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

### 4. Route Task-Aware Policy Selection

```bash
uv run policynim route \
  --task "Implement a refresh-token cleanup background job" \
  --top-k 5
```

`route` is the inspection path between raw search and grounded preflight. It
returns a JSON `PolicySelectionPacket` with:

- the inferred or explicitly provided task type
- selected policies
- selection reasons
- supporting chunk IDs, source paths, line spans, and evidence text
- `insufficient_context`

Use `--task-type` when the task text is intentionally terse or mixed:

```bash
uv run policynim route \
  --task "token cleanup" \
  --task-type refactor \
  --domain security \
  --top-k 5
```

### 5. Compile Policy Constraints

```bash
uv run policynim compile \
  --task "Implement a refresh-token cleanup background job" \
  --top-k 5
```

`compile` is the inspection path between task-aware routing and grounded
preflight. It returns a JSON `CompiledPolicyPacket` with:

- selected policies
- required steps
- forbidden patterns
- architectural expectations
- test expectations
- style constraints
- citations
- `insufficient_context`

Policy compilation reuses the existing NVIDIA chat model setting and adds no
new environment variable or artifact directory.

### 6. Run Grounded Preflight

```bash
uv run policynim preflight \
  --task "Implement a refresh-token cleanup background job" \
  --top-k 5
```

`preflight` is the main workflow. It returns a JSON `PreflightResult` with:

- a grounded summary
- applicable policies
- plan steps
- implementation guidance
- review flags
- tests required
- citations
- `insufficient_context`

If citation validation fails or the grounded answer is too weak, PolicyNIM
returns `insufficient_context=true` instead of bluffing.

`preflight` compiles routed policy evidence before generation and uses the
compiled packet to condition plan steps, implementation guidance, review flags,
and test expectations.

Add `--trace` when you need to inspect the full policy evidence path:

```bash
uv run policynim preflight \
  --task "Implement a refresh-token cleanup background job" \
  --top-k 5 \
  --trace
```

`preflight --trace` returns a JSON `PreflightEvidenceTraceResult` with the public
`PreflightResult` plus a `PolicyEvidenceTrace` containing retained chunks,
selected policies, compiled constraints, generated output links, and trace steps.
Interactive preflight traces include retained chunk text. They do not add a new
artifact directory or call NVIDIA beyond the normal preflight route.

Add `--regenerate` when you want a bounded policy-backed retry loop:

```bash
uv run policynim preflight \
  --task "Implement a refresh-token cleanup background job" \
  --top-k 5 \
  --regenerate \
  --max-regenerations 1 \
  --backend nemo
```

`preflight --regenerate` returns a `PreflightRegenerationResult`. The loop
compiles policy evidence once, keeps the same retained chunk set and
`compiled_packet_id`, evaluates conformance, and retries only from typed failed
metrics such as `required_steps`, `test_expectations`, or `citation_support`.
`--max-regenerations` accepts `1..3`; the default is one retry after the initial
generation.

`--backend nemo` uses the existing direct NVIDIA/Nemotron conformance path.
`--backend nemo_evaluator` and `--backend nat` are optional package-gated paths:

```bash
uv sync --extra nvidia-eval
```

The optional `nvidia-eval` extra installs the pinned NeMo Evaluator SDK,
`nvidia-simple-evals`, and `nvidia-nat-eval` packages used to gate those adapter
paths. If the packages are not installed, the command fails with a
`ConfigurationError` that names the missing extra.

Install the in-project launcher stack only when you need launcher-managed
evaluator runs:

```bash
uv sync --extra nvidia-eval --extra nvidia-eval-launcher --group test --group dev
```

The launcher extra pins `nemo-evaluator-launcher==0.2.4` and
`nvidia-nat[eval]==1.6.0`. PolicyNIM keeps `httpx==0.27.2` to match that stack,
and default CI does not sync the launcher extra.

Internal Guardrails-backed preflight generation is package-gated separately:

```bash
uv sync --extra nvidia-guardrails
```

The `nvidia-guardrails` extra installs the pinned NeMo Guardrails NVIDIA-hosted
model integration for the internal `NeMoGuardrailsPreflightGenerator` wrapper.
There is no CLI flag, MCP tool, eval backend, or default-on switch for this path.
When selected by tests or internal factory code, it wraps the normal generator,
runs output rails over the generated draft JSON, and fails closed on blocked,
malformed, or unsupported-citation output.

### 7. Run Evaluations

```bash
uv run policynim eval
```

Default behavior:

- runs the bundled eval suite in `offline` mode
- uses the `default` eval backend unless another backend is provided
- executes rerank-enabled and rerank-disabled runs
- writes JSON artifacts and HTML reports under `data/evals/workspace`
- embeds compact `PolicyEvidenceTrace` records in preflight eval case JSON
  artifacts
- starts the local Phoenix UI on `http://localhost:8001` and publishes one
  synthetic root span per eval case with code annotations

Phoenix stores its working data under `data/evals/workspace/phoenix` by default.
Startup logs go to `data/evals/workspace/ui/phoenix.log`. Set
`POLICYNIM_EVAL_WORKSPACE_DIR` or `POLICYNIM_EVAL_UI_PORT` to change those
locations without adding Phoenix-specific settings.

Useful variants:

```bash
uv run policynim eval --headless
uv run policynim eval --no-compare-rerank
uv run policynim eval --backend nemo --headless
uv run policynim eval --backend nemo --regenerate --headless
uv run policynim eval --mode live
uv run policynim eval --mode live --backend nemo --headless
```

`--mode live` requires `NVIDIA_API_KEY` and uses an isolated temporary index so
it does not overwrite the normal runtime index.

`--backend nemo` adds policy-conformance scoring for preflight eval cases. In
offline mode it uses deterministic local fixtures; in live mode it reuses the
configured NVIDIA chat model for final-adherence judgment. `nemo_evaluator` and
`nat` are accepted backend values for package-gated evaluator paths; offline eval
keeps those paths deterministic and does not import or call live NVIDIA
evaluation packages.

Search eval cases keep `evidence_trace=null`; preflight eval cases include the
trace used for conformance and debugging. Eval traces keep chunk IDs, paths,
sections, line spans, scores, selected policies, constraints, and output links,
but omit retained chunk text to keep artifacts compact by default.

`eval --regenerate` applies only to preflight cases. Search cases keep
`regeneration_result=null`; preflight cases record the full
`PreflightRegenerationResult` with attempts, stop reason, final result,
conformance result, and evidence traces.

### 8. Run The MCP Server

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

The runtime CLI flow is JSON-first and file- or stdin-driven:

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
5. route retained evidence into selected-policy packets
6. compile selected policy evidence into constraint packets
7. generate grounded guidance from routed evidence and compiled constraints
8. validate every citation against retrieved chunks before returning results
9. optionally score policy conformance during `eval --backend nemo`
10. optionally regenerate preflight output from typed conformance failures

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

Then inspect routed policy selection:

```bash
uv run policynim route \
  --task "Implement a refresh-token cleanup background job" \
  --domain security \
  --top-k 5 | jq
```

Then inspect compiled constraints:

```bash
uv run policynim compile \
  --task "Implement a refresh-token cleanup background job" \
  --domain security \
  --top-k 5 | jq
```

Then compare that with:

```bash
uv run policynim preflight \
  --task "Implement a refresh-token cleanup background job" \
  --domain security \
  --top-k 5 | jq
```

If `search` returns strong hits, `route` returns selected policies, and
`compile` returns constraints while `preflight` returns
`insufficient_context=true`, the failure is in grounded answer validation, not
indexing. If `route` or `compile` also returns `insufficient_context=true`,
inspect the task type, domain filter, and indexed policy coverage first.

### Negative Search Scores

Negative final scores are expected once reranking is enabled. Search results
surface the raw reranker score; treat the score as an ordering signal where
higher is better for that query.

### Missing Index

If `search`, `route`, `compile`, `preflight`, or MCP tool calls fail because the
index is missing, run:

```bash
uv run policynim ingest
```

### Missing NVIDIA Credentials

`ingest`, `search`, `route`, `compile`, `preflight`, live eval mode, and live
`eval --backend nemo` require `NVIDIA_API_KEY`. Offline eval mode does not.
