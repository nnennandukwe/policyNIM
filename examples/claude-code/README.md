# Claude Code Example

This example connects Claude Code to the hosted PolicyNIM Railway MCP over HTTP.
Use the local `stdio` fallback when you want Claude Code to launch PolicyNIM
from an installed CLI or source checkout instead of calling the hosted MCP
endpoint.

## Hosted Railway MCP

1. Open `https://<railway-domain>/beta`, sign in with GitHub, and generate or
   rotate your hosted API key.

2. Export the generated beta token:

```bash
export POLICYNIM_TOKEN='<generated-beta-token>'
```

3. Add the hosted MCP server:

```bash
claude mcp add --transport http policynim 'https://<railway-domain>/mcp' --header "Authorization: Bearer $POLICYNIM_TOKEN"
```

If you installed the PolicyNIM CLI, generate that hosted Claude Code command
from the same MCP config contract:

```bash
policynim quickstart --target hosted-mcp --client claude-code --hosted-url 'https://<railway-domain>/mcp' --format json
policynim mcp-config --target hosted-http --client claude-code --hosted-url 'https://<railway-domain>/mcp' --bearer-token-env-var POLICYNIM_TOKEN
```

Add `--format json` when you want reviewable setup evidence. If the generated
JSON includes `"hosted_url_placeholder": true`, the command is still using an
example URL. Replace the hosted URL placeholder with the deployed `/mcp` URL
before adding the server to Claude Code. `quickstart` also emits `hosted_url`
and `beta_portal_url`; with a real `/mcp` URL, the token portal URL is derived
from the same hosted origin. Hosted `mcp-config --format json` includes the
same `beta_portal_url` so setup reports can show both the portal and MCP
endpoint.

Once the server is available, `policy_preflight` is the main workflow and
`policy_search` is the raw retrieval/debug workflow.

Example prompts:

- `Before editing, call policy_preflight for: Implement a refresh-token cleanup background job. Use the cited constraints in your implementation plan. If the result is insufficient_context, stop and call policy_search with a narrower query before changing files.`
- `Use policy_search for: release installer checksum verification. Summarize the relevant cited policy lines before proposing a fix.`

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

Use this when you want Claude Code to launch a local `stdio` server. The
installed PolicyNIM CLI path does not require a clone. The source checkout path
is useful when you are changing PolicyNIM itself or want checkout-local `.env`
behavior.

### Prerequisites

1. Install the CLI or sync a source checkout:

   ```bash
   policynim --help
   ```

   For source checkout work:

   ```bash
   uv sync
   ```

2. Set `NVIDIA_API_KEY` in your shell or `.env`.

3. Build the local index once before using preflight:

   For an installed CLI:

   ```bash
   policynim doctor
   policynim init
   policynim ingest
   policynim mcp-smoke
   ```

   For a source checkout:

   ```bash
   uv run policynim doctor
   uv run policynim ingest
   uv run policynim mcp-smoke
   ```

### Project-Scoped `.mcp.json`

Generate the exact project-scoped Claude Code MCP config from the installed CLI:

```bash
policynim mcp-config --target local-stdio --client claude-code
```

For reviewable local setup evidence, smoke the generated config before adding
it to Claude Code:

```bash
policynim mcp-config --target local-stdio --client claude-code --format json > claude-mcp-config.json
policynim mcp-smoke --mcp-config-file claude-mcp-config.json --format json
```

The generated installed-CLI output includes this `.mcp.json` shape:

```json
{
  "mcpServers": {
    "policynim": {
      "type": "stdio",
      "command": "policynim",
      "args": [
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

For a source checkout, generate config with the checkout path:

```bash
uv run policynim mcp-config --client claude-code --repo-root /ABS/PATH/TO/policyNIM
```

The generated source-checkout output includes this `.mcp.json` shape:

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

Installed CLI:

```bash
claude mcp add-json policynim \
  '{"type":"stdio","command":"policynim","args":["mcp","--transport","stdio"],"env":{"NVIDIA_API_KEY":"${NVIDIA_API_KEY}"}}'
```

Source checkout:

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
- Run `policynim doctor` or `uv run policynim doctor` when Claude Code cannot
  launch the local server; it prints config, artifact, and MCP hints without
  calling NVIDIA-hosted APIs.
- Run `policynim mcp-smoke` or `uv run policynim mcp-smoke` to verify the local
  `stdio` server can start and list `policy_preflight` and `policy_search`
  before adding it to Claude Code.
