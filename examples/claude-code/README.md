# Claude Code Example

This example connects Claude Code to the hosted PolicyNIM Railway MCP over HTTP.
Use the local `stdio` fallback only if you need to run PolicyNIM from a clone.

## Hosted Railway MCP

1. Open `https://<railway-domain>/beta`, sign in with GitHub, and generate or
   rotate your hosted API key.

2. Export the generated beta token:

```bash
export POLICYNIM_TOKEN=<generated-beta-token>
```

3. Add the hosted MCP server:

```bash
claude mcp add --transport http policynim https://<railway-domain>/mcp --header "Authorization: Bearer $POLICYNIM_TOKEN"
```

Once the server is available, `policy_preflight` is the main workflow and
`policy_search` is the raw retrieval/debug workflow.

Example prompts:

- `Use policy_preflight for: Implement a refresh-token cleanup background job.`
- `Use policy_search for: refresh token cleanup background job`

## Recovery

- Invalid token: if Claude Code gets `401 {"error":"Unauthorized."}`, re-check
  `POLICYNIM_TOKEN` or rotate the hosted key again from `/beta`.
- Temporary upstream NVIDIA failure: retry after a short delay; if it keeps
  failing, the operator should inspect hosted logs for the classified upstream
  failure.
- Insufficient context: use `policy_search` first, narrow the task, or add a
  domain so the hosted service can ground the answer.
- Service unavailable: retry when the hosted service is healthy again; operators
  should check `/healthz` and Railway deploy status.

## Local Fallback

Use this only if you want Claude Code to launch a local `stdio` server from this
repo.

### Prerequisites

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Set `NVIDIA_API_KEY` in your shell or `.env`.

3. Build the local index once before using preflight:

   ```bash
   uv run policynim ingest
   ```

### Project-Scoped `.mcp.json`

Create a project-scoped Claude Code MCP config in the repo root:

```json
{
  "mcpServers": {
    "policynim": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/ABS/PATH/TO/policyNIM",
        "policynim",
        "mcp",
        "--transport",
        "stdio"
      ],
      "env": {
        "NVIDIA_API_KEY": "${NVIDIA_API_KEY}"
      }
    }
  }
}
```

If you prefer the Claude Code CLI, add the same local server with:

```bash
claude mcp add-json policynim \
  '{"type":"stdio","command":"uv","args":["run","--directory","/ABS/PATH/TO/policyNIM","policynim","mcp","--transport","stdio"],"env":{"NVIDIA_API_KEY":"${NVIDIA_API_KEY}"}}'
```

### Notes

- Claude Code stores project-scoped MCP configuration in `.mcp.json`.
- This repo tests `stdio` most heavily; `streamable-http` is also supported if you
  prefer an HTTP MCP connection.
- If the index has not been built yet, PolicyNIM returns an explicit recovery step:
  run `policynim ingest` first.
