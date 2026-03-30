# PolicyNIM Limitations

This document captures the current limits of the shipped system. These are product
constraints, not setup mistakes.

## Product Limits

### Local-First, Single-Developer Runtime

- PolicyNIM is designed around a local index stored on disk.
- The current repo assumes one developer or one local environment owns the runtime
  state under `data/`.
- There is no built-in shared index service, remote storage layer, or multi-user
  coordination model.
- The hosted beta still uses a baked local index inside the container image; it
  does not use a shared volume or remote index service.

### Hosted Auth Is Still Beta-Simple

- The hosted HTTP path uses manually issued bearer tokens from configuration.
- There is no built-in token store, self-serve token issuance, or per-user audit
  layer yet.
- The auth wrapper protects only `/mcp`; `/healthz` stays public for readiness
  probing.
- `/healthz` reports local index readiness only; it does not prove upstream NVIDIA
  availability.
- Hosted startup now fails fast when `POLICYNIM_MCP_PUBLIC_BASE_URL` is set but
  the configured local index is missing or empty.

### NVIDIA Dependency For Live Retrieval

- `ingest`, `search`, `preflight`, and live eval mode depend on NVIDIA-hosted APIs.
- There is no offline fallback model path for those live retrieval workflows.
- Runtime failures related to missing credentials or provider access remain
  explicit operator errors.

### CI Is Offline-Only

- Default CI runs lint and offline-safe tests only.
- CI does not exercise live NVIDIA embeddings, reranking, grounded generation, or
  end-to-end MCP flows against hosted services.
- Live-provider verification remains a manual or opt-in local workflow.

### Retrieval Is Still Narrow

- The current system uses Markdown chunking, vector retrieval, and reranking.
- There is no hybrid lexical-plus-vector search layer.
- There is no document freshness scoring, tenant isolation, or large-corpus tuning
  strategy in this repo yet.

### Corpus Breadth Is Intentionally Small

- The bundled corpus is synthetic and narrow by design.
- It covers a coherent backend and security story rather than a broad engineering
  handbook.
- Retrieval quality on topics outside the shipped corpus will fall off quickly.

### Grounded Answers May Fail Closed

- PolicyNIM can return `insufficient_context=true` even when `search` finds useful
  chunks.
- This happens when the grounded answer does not survive citation validation or the
  retained evidence is too weak for a trustworthy result.
- The system intentionally prefers no grounded answer over a fabricated one.

### Evaluation Is Gold-Case Driven

- The built-in eval suite is deterministic and small.
- It is useful for regression detection, not for benchmarking broad model quality.
- Eval scoring checks recall and insufficient-context behavior; it does not attempt
  to judge prose quality or policy nuance beyond those coded expectations.

### No Separate Review Or Approval Layer

- PolicyNIM produces guidance and citations, but it does not enforce a workflow in
  GitHub, GitLab, or another review system.
- Teams still need a human review process and repo-specific enforcement around the
  guidance it returns.

## Not Limitations

The following are prerequisites or workflow choices, not product limitations:

- building the index before running `search` or `preflight`
- setting `NVIDIA_API_KEY` before live retrieval workflows
- using `--headless` to skip the local eval UI
- choosing `stdio` or `streamable-http` for MCP transport
- setting hosted MCP auth env vars only when you actually want HTTP auth enabled

## Likely Future Expansion Areas

- live CI smoke coverage for hosted-provider paths
- broader corpus coverage and richer provenance notes
- more retrieval modes for larger corpora
- stronger shared-runtime or multi-user deployment stories
- more extensive eval suites and reporting dimensions
