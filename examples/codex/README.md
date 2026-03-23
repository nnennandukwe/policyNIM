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
