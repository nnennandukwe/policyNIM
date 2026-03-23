# Claude Code Example

This example connects Claude Code to a local PolicyNIM MCP server over `stdio`.

## Prerequisites

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Set `NVIDIA_API_KEY` in your shell or `.env`.

3. Build the local index once before using preflight:

   ```bash
   uv run policynim ingest
   ```

## Project-Scoped `.mcp.json`

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

If you prefer the Claude Code CLI, add the same server with:

```bash
claude mcp add-json policynim \
  '{"type":"stdio","command":"uv","args":["run","--directory","/ABS/PATH/TO/policyNIM","policynim","mcp","--transport","stdio"],"env":{"NVIDIA_API_KEY":"${NVIDIA_API_KEY}"}}'
```

## Use It In Claude Code

Once the server is available, `policy_preflight` is the main workflow and
`policy_search` is the raw retrieval/debug workflow.

Example prompts:

- `Use policy_preflight for: Implement a refresh-token cleanup background job.`
- `Use policy_search for: refresh token cleanup background job`

## Notes

- Claude Code stores project-scoped MCP configuration in `.mcp.json`.
- This repo tests `stdio` most heavily; `streamable-http` is also supported if you
  prefer an HTTP MCP connection.
- If the index has not been built yet, PolicyNIM returns an explicit recovery step:
  run `policynim ingest` first.
