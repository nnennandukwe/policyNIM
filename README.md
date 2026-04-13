# PolicyNIM

<p align="center">
  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="src/policynim/assets/beta/policynim_darkmode.jpg"
    >
    <img
      src="src/policynim/assets/beta/policynim_lightmode.png"
      alt="PolicyNIM logo"
      width="460"
    >
  </picture>
</p>

[![Built with NVIDIA NIM](https://img.shields.io/badge/Built%20with-NVIDIA%20NIM-76B900?logo=nvidia&logoColor=white)](https://docs.nvidia.com/nim/)

PolicyNIM is a policy-aware engineering preflight layer for AI coding agents.

It helps an agent retrieve grounded policy evidence, generate implementation
guidance with citations attached, and fail closed when the available grounding
is too weak to trust.

PolicyNIM currently ships with two main user-facing surfaces:

- a JSON-first CLI for local developer workflows
- an MCP server for integrations such as Codex and Claude Code

## What Works Today

- Deterministic Markdown ingest with heading-aware chunking and source line spans.
- NVIDIA-hosted embeddings and reranking for retrieval.
- Local LanceDB storage for the retrievable policy index.
- Task-aware policy routing with citation-preserving selected-policy packets.
- Policy compilation into citation-backed planning and generation constraints.
- Grounded preflight synthesis with compiled plan steps, citation validation, and
  fail-closed fallback.
- Opt-in preflight evidence traces that link chunks, selected policies, compiled
  constraints, generated guidance, and conformance checks.
- Opt-in policy-backed regeneration for preflight and eval preflight cases,
  reusing the same compiled packet and typed conformance failures as retry
  triggers.
- Eval backend selection with optional policy-conformance scoring for compiled
  plans and preflight outputs, with compact traces embedded in eval result
  artifacts and local Phoenix reporting for non-headless runs.
- Runtime-rule decisions plus SQLite-backed evidence for allowed, confirmed,
  blocked, and failed runtime actions.
- JSON-first CLI commands for `init`, `ingest`, `dump-index`, `search`, `route`,
  `compile`, `preflight`, `eval`, `mcp`, `runtime`, and `evidence`.
- MCP tools for `policy_preflight` and `policy_search`.
- Hosted HTTP `streamable-http` with `/healthz`, a self-serve `/beta` portal,
  and bearer auth on `/mcp`.

## What To Run First

If you want the shortest path to a real preflight run, start with the hosted
beta instead of cloning the repo.

### Self-Serve Hosted Beta

<p align="center">
  <img
    src="docs/assets/readme/policynim-beta-dark-landing-preview.png"
    alt="PolicyNIM hosted beta landing page in dark mode"
    width="1100"
  >
</p>

1. Open `https://<railway-domain>/beta`.
2. Sign in with GitHub.
3. Generate or rotate your hosted API key.
4. Export the token and add the hosted MCP server to your client.

```bash
export POLICYNIM_TOKEN=<generated-beta-token>
codex mcp add policynim --url https://<railway-domain>/mcp --bearer-token-env-var POLICYNIM_TOKEN
claude mcp add --transport http policynim https://<railway-domain>/mcp --header "Authorization: Bearer $POLICYNIM_TOKEN"
```

Then ask your client to call the MCP tools directly:

- `Use policy_preflight for: Implement a refresh-token cleanup background job.`
- `Use policy_search for: refresh token cleanup background job`

Use [docs/hosted-beta-operations.md](docs/hosted-beta-operations.md) for:

- hosted beta recovery topics
- container build and local hosted-image checks
- Railway deploy setup and smoke-test notes

## Local Contributor Setup

Use this path only if you want to run PolicyNIM from a local checkout.

```bash
uv sync --group test --group dev
cp .env.development.example .env
export NVIDIA_API_KEY=<your-nvidia-api-key>
uv run policynim ingest
uv run pytest -q
```

After the index is built, the fastest local sanity checks are:

```bash
uv run policynim search --query "refresh token cleanup background job" --top-k 5
uv run policynim route --task "Implement a refresh-token cleanup background job" --top-k 5
uv run policynim compile --task "Implement a refresh-token cleanup background job" --top-k 5
uv run policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5
uv run policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5 --trace
uv run policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5 --regenerate --backend nemo
```

Use [docs/contributor-guide.md](docs/contributor-guide.md) for environment
templates, runtime settings, optional NVIDIA eval and Guardrails extras, and
contributor quality gates. The launcher path is installable in-project with
`uv sync --extra nvidia-eval --extra nvidia-eval-launcher --group test --group dev`;
the internal Guardrails output-rail wrapper uses `uv sync --extra nvidia-guardrails`.

If you are using an installed copy instead of a source checkout, run
`uv run policynim init` once first so PolicyNIM can write the standalone config
file and data-path defaults before `policynim ingest`.

Use [docs/workflows.md](docs/workflows.md) for the CLI, MCP, runtime, eval, and
troubleshooting handbook.

## Docs Map

Start here when you want the longer version of a specific path:

- [docs/index.md](docs/index.md): documentation hub by audience and task
- [docs/contributor-guide.md](docs/contributor-guide.md): local setup, env vars,
  model references, and quality gates
- [docs/workflows.md](docs/workflows.md): CLI surfaces,
  ingest/search/route/compile/preflight, eval, MCP, runtime/evidence, and
  troubleshooting
- [docs/hosted-beta-operations.md](docs/hosted-beta-operations.md): hosted beta
  quickstart, recovery, container build flow, and Railway deploy notes
- [docs/architecture.md](docs/architecture.md): package boundaries, runtime flow,
  and interface rules
- [docs/architecture-diagram.md](docs/architecture-diagram.md): Mermaid diagram
  of the current package layout and runtime flow
- [docs/demo-script.md](docs/demo-script.md): step-by-step demo for the hero use case
- [docs/limitations.md](docs/limitations.md): current product limits and non-goals
- [docs/public-source-grounding.md](docs/public-source-grounding.md): provenance
  notes for the shipped sample corpus
- [tests/README.md](tests/README.md): current automated coverage
- [examples/codex/README.md](examples/codex/README.md): Codex MCP setup example
- [examples/claude-code/README.md](examples/claude-code/README.md): Claude Code
  MCP setup example

## Limits And Scope

Current limitations are intentional:

- the system is local-first and aimed at a single developer workflow
- CI is offline-only and does not run live NVIDIA end-to-end checks by default
- the sample corpus is narrow and synthetic, not a broad enterprise handbook
- grounded answers may fail closed even when raw retrieval finds useful chunks

See [docs/limitations.md](docs/limitations.md) for the full list and future
expansion areas.
