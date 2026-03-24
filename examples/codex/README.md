# Codex Example

This example connects Codex to a local PolicyNIM MCP server over `stdio`.

## Prerequisites

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Set `NVIDIA_API_KEY` in your shell or `.env`.

3. Build the local index once before calling preflight:

   ```bash
   uv run policynim ingest
   ```

## Add PolicyNIM To Codex

### Codex CLI

Run this from anywhere on your machine, replacing `/ABS/PATH/TO/policyNIM` with
the absolute path to this repo:

```bash
codex mcp add policynim \
  --env NVIDIA_API_KEY=$NVIDIA_API_KEY \
  -- uv run --directory /ABS/PATH/TO/policyNIM policynim mcp --transport stdio
```

Inspect the saved server entry:

```bash
codex mcp get policynim
```

### Codex App

In the Codex app, open the custom MCP server form and enter these values:

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

Why the repo path appears twice:

- `--directory /ABS/PATH/TO/policyNIM` tells `uv` which project to run
- `Working directory: /ABS/PATH/TO/policyNIM` makes relative paths such as `.env`
  and `data/lancedb` resolve from the repo root

Using the same repo path in both places is the least error-prone setup for this
project. If you keep `--directory`, the app working directory is mostly
redundant, but keeping both aligned avoids confusion.

## Use It In Codex

After the server is added, ask Codex to use the MCP tools directly. The primary
workflow is `policy_preflight`; `policy_search` is the debug path.

Example prompts:

- `Use policy_preflight for: Implement a refresh-token cleanup background job.`
- `Use policy_search for: refresh token cleanup background job`

## Notes

- This example uses `stdio`, which is the primary tested MCP transport in this repo.
- If Codex cannot find `uv`, use the absolute path to the `uv` executable in the
  `codex mcp add` command.
- If `policynim ingest` has not been run yet, PolicyNIM will return an explicit
  missing-index error instead of `insufficient_context`.
