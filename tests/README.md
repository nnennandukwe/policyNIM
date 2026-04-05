# Test Plan

Current automated coverage includes:

- Markdown policy parsing, metadata normalization, and deterministic chunking
- Additional Day 6 chunking edge cases around blank sections and repeated nested headings
- Day 3 ingest orchestration with local LanceDB rebuild behavior
- Day 3 search orchestration with domain filters and missing-index handling
- Day 6 eval orchestration, rerank on/off comparison, and isolated live-eval index handling
- Day 4 grounded preflight orchestration, citation validation, and fallback behavior
- Day 6 citation-deduplication and policy-vs-draft citation validation edge cases
- NVIDIA response-validation coverage for malformed grounded-generation and reranking payloads
- CLI output for `ingest`, JSON-first `search`, and JSON-first `preflight`
- CLI output for `eval`
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
  to run instead of skip.
- Do not rely on `-m live` to pick up Docker build checks; they use the dedicated
  `docker_live` marker.

Run the hosted build and deployed-service suites manually with:

```bash
POLICYNIM_RUN_DOCKER_TESTS=1 uv run --group test pytest -q -m docker_live tests/test_docker_build_live.py
uv run --group test pytest -q -m live tests/test_hosted_mcp_live.py
```
