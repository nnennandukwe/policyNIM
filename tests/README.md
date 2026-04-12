# Test Plan

Current automated coverage includes:

- Markdown policy parsing, metadata normalization, and deterministic chunking
- Additional chunking edge cases around blank sections and repeated nested headings
- Ingest orchestration with local LanceDB rebuild behavior
- Search orchestration with domain filters and missing-index handling
- Runtime decision orchestration with compiled runtime rules and evidence-linked citations
- Runtime execution orchestration with confirmation handling, redaction, and durable evidence persistence
- Runtime evidence session-summary reporting over the SQLite evidence store
- Runtime execution SQLite store schema, ordering, reopen, and concurrency behavior
- Runtime hardening for file-write and HTTP execution paths plus confirmation-callback failures
- Real SQLite-backed CLI runtime execution plus `evidence report` coverage
- Runtime docs parity for command forms, env examples, and policy authoring guidance
- Eval orchestration, rerank on/off comparison, and isolated live-eval index handling
- Grounded preflight orchestration, citation validation, and fallback behavior
- Task-aware routing, task-profile inference, selected-policy grouping,
  and weak-evidence fallback behavior
- Policy compilation, compiled constraint citation validation,
  fail-closed handling, and preflight conditioning
- Policy conformance scoring, eval backend selection, preflight trace
  handling, and NVIDIA conformance-response validation
- Policy evidence trace materialization, `preflight --trace` CLI output,
  compact eval artifact trace attachment, and conformance ID preservation
- Policy-backed regeneration, compile-once packet identity, typed retry
  triggers, max-regeneration and insufficient-context stops, provider fail-closed
  behavior, citation-drift rejection, `preflight --regenerate`, and
  `eval --regenerate`
- Phoenix eval reporting, headless UI skipping, workspace-local
  Phoenix startup, deterministic span publishing, and synchronous code
  annotations
- Optional NeMo Evaluator and NeMo Agent Toolkit adapter gating with
  mock-backed import-injected package modules; default CI does not import live optional
  NVIDIA eval packages
- Optional NVIDIA Eval Launcher dependency resolution through
  `uv sync --extra nvidia-eval --extra nvidia-eval-launcher --group test --group dev`
- Internal NeMo Guardrails output-rail wrapper coverage for lazy package
  gating, packaged assets, malformed rail output, blocked output, citation drift,
  regeneration context pass-through, and default factory isolation
- Citation-deduplication and policy-vs-draft citation validation edge cases
- NVIDIA response-validation coverage for malformed grounded-generation,
  policy-compilation, and reranking payloads
- CLI output for `ingest`, JSON-first `search`, JSON-first `route`, JSON-first
  `compile`, and JSON-first `preflight`
- CLI output for `eval`
- CLI output for `eval --backend default|nemo|nemo_evaluator|nat`
- CLI output for `runtime decide`, `runtime execute`, and `evidence report`
- CLI output for `beta-admin` hosted operator commands
- MCP tool parity for `policy_preflight` and `policy_search`
- MCP startup wiring for `stdio` and `streamable-http`
- Hosted HTTP `/healthz` readiness checks and the optional bearer-auth wrapper
- Hosted beta portal routes, signed-session flow, SQLite auth storage, and
  quota-blocking behavior
- Hosted MCP structured logs for auth rejects, tool name, latency, and upstream failure class
- Hosted docs parity for the canonical Codex and Claude hosted MCP commands plus recovery guidance
- Opt-in Docker hosted-image contract coverage for missing `NVIDIA_API_KEY` and a non-empty baked index
- Opt-in live NVIDIA embedding smoke coverage behind `NVIDIA_API_KEY`
- Opt-in live Railway hosted MCP smoke coverage behind:
  - `POLICYNIM_BETA_MCP_URL`
  - `POLICYNIM_BETA_MCP_TOKEN`

Hosted onboarding versus live smoke env vars:

- Beta users follow the docs by visiting `/beta`, generating a key, and exporting
  `POLICYNIM_TOKEN` as the client-side bearer token env var.
- Operators and maintainers use `POLICYNIM_BETA_MCP_URL` and `POLICYNIM_BETA_MCP_TOKEN`
  only for the deployed-service smoke harness.

Hosted onboarding versus Docker build-test env vars:

- Set `POLICYNIM_RUN_DOCKER_TESTS=1` only when you want to run the Docker build
  regression locally against a working Docker daemon.
- Set `NVIDIA_API_KEY` as well if you want the positive baked-index image validation
  to run instead of skip; the Docker harness passes it as a BuildKit secret, not
  as a build arg.
- Do not rely on `-m live` to pick up Docker build checks; they use the dedicated
  `docker_live` marker.

Run the hosted build and deployed-service suites manually with:

```bash
POLICYNIM_RUN_DOCKER_TESTS=1 uv run --group test pytest -q -m docker_live tests/test_docker_build_live.py
uv run --group test pytest -q -m live tests/test_hosted_mcp_live.py
```
