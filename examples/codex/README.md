# Codex Example

This example connects Codex to the hosted PolicyNIM Railway MCP over HTTP. Use
the local `stdio` fallback when you want Codex to launch PolicyNIM from an
installed CLI or source checkout instead of calling the hosted MCP endpoint.

## Hosted Railway MCP

1. Open `https://<railway-domain>/beta`, sign in with GitHub, and generate or
   rotate your hosted API key.

2. Export the generated beta token:

```bash
export POLICYNIM_TOKEN='<generated-beta-token>'
```

3. Add the hosted MCP server:

```bash
codex mcp add policynim --url 'https://<railway-domain>/mcp' --bearer-token-env-var POLICYNIM_TOKEN
```

If you installed the PolicyNIM CLI, generate that hosted Codex command from the
same MCP config contract:

```bash
policynim quickstart --target hosted-mcp --client codex --hosted-url 'https://<railway-domain>/mcp' --format json
policynim mcp-config --target hosted-http --client codex --hosted-url 'https://<railway-domain>/mcp' --bearer-token-env-var POLICYNIM_TOKEN
```

Add `--format json` when you want reviewable setup evidence. If the generated
JSON includes `"hosted_url_placeholder": true`, the command is still using an
example URL. Replace the hosted URL placeholder with the deployed `/mcp` URL
before adding the server to Codex. `quickstart` also emits `hosted_url` and
`beta_portal_url`; with a real `/mcp` URL, the token portal URL is derived from
the same hosted origin. Hosted `mcp-config --format json` includes the same
`beta_portal_url` so setup reports can show both the portal and MCP endpoint.

4. Inspect the saved server entry:

```bash
codex mcp get policynim
```

After the server is added, ask Codex to use the MCP tools directly. The primary
workflow is `policy_preflight`; `policy_search` is the debug path.

Example prompts:

- `Before editing, call policy_preflight for: Implement a refresh-token cleanup background job. Use the cited constraints in your implementation plan. If the result is insufficient_context, stop and call policy_search with a narrower query before changing files.`
- `Use policy_search for: release installer checksum verification. Summarize the relevant cited policy lines before proposing a fix.`

## Recovery

- Invalid token: if Codex gets `401 {"error":"Unauthorized."}`, re-check
  `POLICYNIM_TOKEN` or rotate the hosted key again from `/beta`.
- Temporary upstream NVIDIA failure: retry after a short delay; if it keeps
  failing, the operator should inspect hosted logs for the classified upstream
  failure.
- Insufficient context: use `policy_search` first, narrow the task, or add a
  domain so the hosted service can ground the answer.
- Service unavailable: retry when the hosted service is healthy again; operators
  should check `/healthz` and Railway deploy status.

## Local Fallback

Use this when you want Codex to launch a local `stdio` server. The installed
PolicyNIM CLI path does not require a clone. The source checkout path is useful
when you are changing PolicyNIM itself or want checkout-local `.env` behavior.

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

3. Build the local index once before calling preflight:

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

### Codex CLI

Generate the exact local Codex config from the installed CLI:

```bash
policynim mcp-config --target local-stdio --client codex
```

For reviewable local setup evidence, smoke the generated config before adding
it to Codex:

```bash
policynim mcp-config --target local-stdio --client codex --format json > codex-mcp-config.json
policynim mcp-smoke --mcp-config-file codex-mcp-config.json --format json
```

The generated installed-CLI output includes this Codex CLI command:

```bash
codex mcp add policynim \
  --env NVIDIA_API_KEY=$NVIDIA_API_KEY \
  -- policynim mcp --transport stdio
```

For a source checkout, generate config with the checkout path:

```bash
uv run policynim mcp-config --client codex --repo-root /ABS/PATH/TO/policyNIM
```

The generated source-checkout output includes this Codex CLI command:

```bash
codex mcp add policynim \
  --env NVIDIA_API_KEY=$NVIDIA_API_KEY \
  -- uv run --directory /ABS/PATH/TO/policyNIM policynim mcp --transport stdio
```

### Codex App

The generated `mcp-config --client codex` output also includes the Codex app
fields. In the Codex app, open the custom MCP server form and enter these
values:

For an installed CLI:

- `Name`: `policynim`
- `Transport`: `STDIO`
- `Command to launch`: `policynim`
- `Arguments`: `mcp`, `--transport`, `stdio`

For a source checkout:

- `Name`: `policynim`
- `Transport`: `STDIO`
- `Command to launch`: `uv`
- `Arguments`:
  - `run`
  - `--directory`
  - `/ABS/PATH/TO/policyNIM`
  - `policynim`
  - `mcp`
  - `--transport`
  - `stdio`
- `Working directory`: `/ABS/PATH/TO/policyNIM`

For credentials, use one of these approaches:

- `Environment variables`: key `NVIDIA_API_KEY`, value your actual NVIDIA API key
- `Environment variable passthrough`: `NVIDIA_API_KEY` if the Codex app already
  inherits that variable from your shell or launcher environment

Do not set `env=$NVIDIA_API_KEY`. The variable name must be `NVIDIA_API_KEY`.

Why the repo path appears twice in source-checkout config:

- `--directory /ABS/PATH/TO/policyNIM` tells `uv` which project to run
- `Working directory: /ABS/PATH/TO/policyNIM` makes relative paths such as `.env`
  and `data/lancedb` resolve from the repo root

Using the same repo path in both places is the least error-prone setup for this
project. If you keep `--directory`, the app working directory is mostly
redundant, but keeping both aligned avoids confusion.

### Notes

- This example uses `stdio`, which is the primary tested MCP transport in this repo.
- If Codex cannot find `uv`, use the absolute path to the `uv` executable in the
  source-checkout `codex mcp add` command.
- If `policynim ingest` has not been run yet, PolicyNIM will return an explicit
  missing-index error instead of `insufficient_context`.
- Run `policynim doctor` or `uv run policynim doctor` when Codex cannot launch
  the local server; it prints config, artifact, and MCP hints without calling
  NVIDIA-hosted APIs.
- Run `policynim mcp-smoke` or `uv run policynim mcp-smoke` to verify the local
  `stdio` server can start and list `policy_preflight` and `policy_search`
  before adding it to Codex.
