# PolicyNIM Contributor Guide

Use this guide when you are running PolicyNIM from a local checkout instead of
the hosted beta.

## Local Setup

Install the runtime, test, and dev dependencies:

```bash
uv sync --group test --group dev
```

Copy the development example environment file:

```bash
cp .env.development.example .env
```

Then set `NVIDIA_API_KEY` in `.env` or your shell. For the official key-creation
flow, use NVIDIA's
[API Catalog Quickstart Guide](https://docs.api.nvidia.com/nim/docs/api-quickstart)
and [Build catalog](https://build.nvidia.com/).

If you want to index a custom policy directory instead of the bundled sample
corpus, add `POLICYNIM_CORPUS_DIR=/abs/path/to/policies` manually to `.env`.

Build the local index once before using search, preflight, or the local MCP
server:

```bash
uv run policynim ingest
```

## Environment Templates

The repo ships three related templates:

- `.env.development.example` is the preferred local-development template
- `.env.production.example` is the hosted Railway and Docker reference
- `.env.example` remains a backward-compatible alias for the development defaults

Leave `POLICYNIM_CORPUS_DIR` unset to use the bundled sample corpus.

## Important Runtime Settings

Core retrieval and grounding:

- `NVIDIA_API_KEY`
- `POLICYNIM_CORPUS_DIR`
- `POLICYNIM_LANCEDB_URI`
- `POLICYNIM_LANCEDB_TABLE`
- `POLICYNIM_DEFAULT_TOP_K`

Hosted MCP and beta portal:

- `POLICYNIM_ENV`
- `POLICYNIM_MCP_HOST`
- `POLICYNIM_MCP_PORT`
- `POLICYNIM_MCP_REQUIRE_AUTH`
- `POLICYNIM_MCP_BEARER_TOKENS`
- `POLICYNIM_MCP_PUBLIC_BASE_URL`
- `POLICYNIM_BETA_SIGNUP_ENABLED`
- `POLICYNIM_BETA_AUTH_DB_PATH`
- `POLICYNIM_BETA_SESSION_SECRET`
- `POLICYNIM_BETA_GITHUB_CLIENT_ID`
- `POLICYNIM_BETA_GITHUB_CLIENT_SECRET`
- `POLICYNIM_BETA_DAILY_REQUEST_QUOTA`

Runtime rules, evidence, and eval UI:

- `POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH`
- `POLICYNIM_RUNTIME_EVIDENCE_DB_PATH`
- `POLICYNIM_RUNTIME_SHELL_TIMEOUT_SECONDS`
- `POLICYNIM_EVAL_UI_PORT`

## Default Model References

The default example configs use:

- embeddings:
  [`nvidia/llama-nemotron-embed-1b-v2`](https://docs.api.nvidia.com/nim/reference/nvidia-llama-nemotron-embed-1b-v2)
- reranking:
  [`nvidia/llama-nemotron-rerank-1b-v2`](https://docs.api.nvidia.com/nim/reference/nvidia-llama-nemotron-rerank-1b-v2-infer)
- grounded generation:
  [NVIDIA LLM API reference](https://docs.api.nvidia.com/nim/reference/llm-apis)

## Contributor Workflow

Install pre-commit once:

```bash
uv run --group dev pre-commit install
```

Run the standard quality gates:

```bash
uv run ruff check
uv run pytest -q
```

For live or hosted-only checks, use the coverage notes in
[../tests/README.md](../tests/README.md).

## Client Setup Examples

Use the hosted-first examples unless you specifically need a local MCP server:

- [../examples/codex/README.md](../examples/codex/README.md)
- [../examples/claude-code/README.md](../examples/claude-code/README.md)

Use [workflows.md](workflows.md) for the command handbook after local setup is
done.
