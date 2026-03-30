# Hosted MCP Beta Day 2 Execution Plan

## Summary
- Day 2 converts the finished Day 1 hosted runtime into a Railway-buildable container image with a baked LanceDB index.
- Public MCP tools, CLI flags, and Day 1 hosted auth and `/healthz` behavior stay unchanged.
- The deliverable is image packaging plus hosted startup fail-fast for missing or empty baked index state. Railway deployment, public beta URL, and hosted-first onboarding stay in later days.

## Skills Used
- `audit-boundaries` is primary. Keep Docker and hosted bootstrap concerns at the repo root and MCP startup boundary; do not leak image-build logic into retrieval or provider services.
- `audit-abstractions` is primary. Do not add deployment managers, image builder wrappers, or hosted bootstrap coordinators; use one `Dockerfile`, one `.dockerignore`, and one small hosted startup guard.
- `failure-path-testing` is primary. Day 2 is mostly about blocked startup states, so tests must prove the hosted server refuses to boot with a missing or empty baked index and that local behavior still works.
- `audit-errors` is primary. Build and startup failures must preserve useful cause and explicit operator recovery text; avoid silent fallback and generic hosted boot errors.
- `dx-audit` is primary. The build arg flow, baked index path, runtime env expectations, and failure messages are operator-facing and must be obvious from commands and docs.
- `cli-interactive-parity` applies secondarily. Keep README and container examples aligned with the real `policynim mcp --transport streamable-http` contract and existing env names.
- `python-pydantic-pyright` is situational only. Use it if the hosted startup validation adds new typed helpers or settings-derived runtime checks.
- Do not use `guidelines-audit` for Day 2. No repo-local guideline files were found in this repo.
- Do not use `workflow-invariants`, `sqlite-concurrency-hardening`, `temporal-scoping-guards`, `cloudflare-deploy`, or GitHub CI/debug skills for this slice.

## Scope
- Sync local repo with remote `main`.
- Create the feature branch `feat/hosted-day2-image-build`.
- Add a production multi-stage `Dockerfile`.
- Add a repo-root `.dockerignore`.
- Build the policy index during image build by running `policynim ingest`.
- Bake the generated LanceDB directory into the final image at `/app/data/lancedb-baked`.
- Reuse `POLICYNIM_LANCEDB_URI` by setting it inside the image to `/app/data/lancedb-baked`.
- Add hosted startup fail-fast behavior for `streamable-http` when the configured index is missing or empty.
- Keep `stdio` behavior unchanged.
- Keep Day 1 hosted auth, `/healthz`, and MCP tool signatures unchanged.
- Add targeted tests and incremental docs for the baked-image workflow.
- Do not add Railway service config, health-check wiring, structured logs, public-domain docs, hosted smoke tests, or client onboarding rewrite on Day 2.

## Package Layout
- Repo root: `Dockerfile`, `.dockerignore`
- Hosted startup validation: `src/policynim/interfaces/mcp.py`, `src/policynim/services/health.py`
- Tests: `tests/test_mcp.py`, `tests/test_health_service.py`
- Docs: `README.md`, `docs/architecture.md`, `docs/limitations.md`

## Dependencies And Settings
- Add no new Python package dependency on Day 2.
- Use existing Python 3.11 and `uv.lock` as the source of truth for the image build.
- Use `NVIDIA_API_KEY` as a Docker build-time `ARG` only for `policynim ingest`.
- Do not persist the build-time `NVIDIA_API_KEY` into the final image layers or runtime env.
- Final image must still accept runtime `NVIDIA_API_KEY` from Railway because live `search` and `preflight` call NVIDIA after startup.
- Add no new app settings on Day 2.
- Hosted startup fail-fast is enabled only when both conditions hold:
  - transport is `streamable-http`
  - `POLICYNIM_MCP_PUBLIC_BASE_URL` is set
- If `POLICYNIM_MCP_PUBLIC_BASE_URL` is unset, keep the Day 1 local HTTP behavior and let `/healthz` report readiness instead of aborting startup.

## Canonical Contracts
- Public MCP tools remain exactly:
  - `policy_preflight(task, domain?, top_k?)`
  - `policy_search(query, domain?, top_k?)`
- CLI command shape remains exactly: `policynim mcp --transport stdio|streamable-http`
- The final image command is the console script, not `uv run`: `policynim mcp --transport streamable-http`
- The baked in-image index path is fixed at `/app/data/lancedb-baked`
- The final image sets `POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked`
- Hosted startup fail-fast raises `ConfigurationError` before serving HTTP when the configured index is missing or empty
- `stdio` ignores hosted startup validation completely

## Image And Startup Design
- Use a multi-stage Dockerfile with `python:3.11-slim` as both stages.
- Builder stage:
  - install `uv`
  - set `WORKDIR /app`
  - copy only files needed to install and ingest: `pyproject.toml`, `uv.lock`, `src/`, `policies/`, `evals/`, `README.md`
  - set `ARG NVIDIA_API_KEY`
  - set `ENV POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked`
  - run `uv sync --frozen`
  - run `uv run policynim ingest`
- Final stage:
  - set `WORKDIR /app`
  - copy the built virtualenv from the builder stage
  - copy `src/`, `policies/`, `evals/`, `pyproject.toml`, and `README.md`
  - set `ENV PATH=/app/.venv/bin:$PATH`
  - set `ENV POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked`
  - copy `/app/data/lancedb-baked` from the builder stage
  - use `CMD ["policynim", "mcp", "--transport", "streamable-http"]`
- Add `.dockerignore` entries for `.env`, `.git`, `.venv`, `.pytest_cache`, `.ruff_cache`, `.plan`, `data/`, `dist/`, and local build artifacts so the image always uses the build-generated index, not a copied local one.
- Add one small hosted startup validator in `health.py` that reuses the Day 1 readiness check and raises `ConfigurationError` with an actionable message when the configured index is not ready for hosted boot.
- Call that validator from `run_server()` in `mcp.py` only for hosted `streamable-http` startup.
- Do not run `policynim ingest` at container startup.
- Do not add a Railway volume or mutable shared index path on Day 2.

## Failure And Recovery Rules
- Missing `NVIDIA_API_KEY` during image build must fail the Docker build where `policynim ingest` runs; do not catch or downgrade that error.
- Hosted startup with missing baked index must raise `ConfigurationError` with a recovery hint that the image must be rebuilt or `POLICYNIM_LANCEDB_URI` must point at a populated path.
- Hosted startup with empty baked index must raise `ConfigurationError` with the same recovery direction.
- Local `streamable-http` without `POLICYNIM_MCP_PUBLIC_BASE_URL` must still start and rely on `/healthz`.
- `/healthz` remains a readiness endpoint only. It does not replace Day 2 hosted fail-fast.
- No fallback to runtime ingest is allowed on Day 2.

## Commit Plan
1. `build(docker): add day-2 production image and dockerignore`
2. `feat(hosted): fail fast on hosted startup when baked index is missing or empty`
3. `test(hosted): cover baked-index startup validation and local-vs-hosted behavior`
4. `docs(hosted): add baked-image build and run guidance`

## Branch And PR Flow
1. `git fetch origin` and confirm Day 1 hosted runtime work is on `origin/main`.
2. Fast-forward local `main` to `origin/main`.
3. Create `feat/hosted-day2-image-build` from updated `main`.
4. Land the Dockerfile and `.dockerignore` first, then hosted startup validation, then tests and docs.
5. Open the PR only after the targeted Day 2 tests pass and one real local Docker build succeeds.

## Test Plan
- Unit test that hosted startup validation succeeds when the configured index exists and has rows.
- Unit test that hosted startup validation raises `ConfigurationError` when the configured index does not exist.
- Unit test that hosted startup validation raises `ConfigurationError` when the configured index exists but is empty.
- Unit test that `run_server("stdio")` does not invoke hosted startup validation.
- Unit test that `run_server("streamable-http")` without `POLICYNIM_MCP_PUBLIC_BASE_URL` preserves Day 1 behavior.
- Unit test that hosted startup error text includes an explicit recovery step.
- Manual build verification command:
  - `docker build --build-arg NVIDIA_API_KEY=$NVIDIA_API_KEY -t policynim-hosted:day2 .`
- Manual runtime verification command:
  - run the image with runtime `NVIDIA_API_KEY`, `POLICYNIM_MCP_REQUIRE_AUTH`, `POLICYNIM_MCP_BEARER_TOKENS`, and `POLICYNIM_MCP_PUBLIC_BASE_URL`, then confirm `/healthz` returns `200`
- Manual negative verification command:
  - run the image overriding `POLICYNIM_LANCEDB_URI` to a missing path and confirm startup exits non-zero before serving HTTP
- Verification commands after implementation:
  - `uv run pytest -q tests/test_health_service.py tests/test_mcp.py tests/test_cli.py`
  - `uv run pytest -q`
  - `uv run ruff check`

## Definition Of Done
- `feat/hosted-day2-image-build` exists off updated `main`.
- `Dockerfile` and `.dockerignore` implement the baked-index strategy.
- A local Docker build produces an image with a non-empty index at `/app/data/lancedb-baked`.
- Hosted `streamable-http` startup fails fast when the configured baked index is missing or empty.
- Local `stdio` and non-hosted local HTTP behavior remain unchanged.
- Public MCP tool names, args, and payload shapes are unchanged.
- README, architecture notes, and limitations docs explain the build-time key, runtime key, baked index path, and no-volume assumption.
- No Railway deployment config or Day 3 hosted beta work leaks into this slice.

## Assumptions And Defaults
- Day 1 hosted runtime and auth work is already present on `main`.
- The image continues to run from the source checkout plus installed virtualenv; Day 2 does not switch the project to wheel-only runtime packaging.
- `POLICYNIM_MCP_PUBLIC_BASE_URL` is the hosted-mode signal for startup fail-fast.
- Railway will provide the same NVIDIA secret at build time and runtime, but the final image must not embed it.
- No Railway volume is used in v1.
- Railway deployment, health-check configuration, public domain setup, and live hosted smoke tests stay in Day 3.
