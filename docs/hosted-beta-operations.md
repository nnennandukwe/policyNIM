# PolicyNIM Hosted Beta Operations

Use this guide when you want the longer version of the hosted beta flow,
recovery notes, or operator deployment checklist.

## Self-Serve Hosted Beta

The hosted beta is the fastest path for a new user:

1. Open `https://<railway-domain>/beta`.
2. Sign in with GitHub.
3. Mint or rotate an API key.
4. Export the generated bearer token in your client shell.
5. Add the hosted MCP server to Codex or Claude Code.

```bash
export POLICYNIM_TOKEN=<generated-beta-token>
codex mcp add policynim --url https://<railway-domain>/mcp --bearer-token-env-var POLICYNIM_TOKEN
claude mcp add --transport http policynim https://<railway-domain>/mcp --header "Authorization: Bearer $POLICYNIM_TOKEN"
```

Then ask your client to call the MCP tools directly:

- `Use policy_preflight for: Implement a refresh-token cleanup background job.`
- `Use policy_search for: refresh token cleanup background job`

Hosted beta notes:

- replace `https://<railway-domain>/mcp` with the deployed Railway beta URL
- self-serve users should start from `https://<railway-domain>/beta`, not from
  an operator-issued secret
- `POLICYNIM_TOKEN` is a client-side shell variable only. It is not a
  PolicyNIM app setting
- `POLICYNIM_MCP_BEARER_TOKENS` is optional and reserved for operator
  break-glass access on `/mcp`
- if you run the opt-in live smoke test locally, export the same deployed values
  as `POLICYNIM_BETA_MCP_URL` and `POLICYNIM_BETA_MCP_TOKEN`

## Hosted Beta Recovery

### Invalid Token

- expect `401 {"error":"Unauthorized."}` from `/mcp` for a missing or invalid
  bearer token
- re-check `POLICYNIM_TOKEN`, then revisit `/beta` and rotate the hosted API key
  if needed

### Temporary Upstream NVIDIA Failure

- hosted MCP can stay healthy on `/healthz` while an individual tool call fails
  because NVIDIA embeddings, reranking, or grounded generation is temporarily
  unavailable
- retry after a short delay first
- if the failure persists, the operator should inspect hosted MCP logs for
  `upstream_failure_class` such as `timeout`, `connection`, or `rate_limit`

### Insufficient Context

- `insufficient_context=true` is a grounded no-answer, not an auth or
  availability failure
- recover by narrowing the task, adding a domain, or calling `policy_search`
  first to inspect the retrieved evidence

### Service Unavailable

- if the hosted MCP URL does not respond, or `/healthz` returns `503`, the
  hosted service or baked local index is not ready yet
- retry after the service becomes healthy. If you operate the service, check the
  Railway deploy state and `/healthz` first

## Container Build For Hosted HTTP

Build the production image with a BuildKit secret for the bake-time NVIDIA key
so the index is baked into the image:

```bash
DOCKER_BUILDKIT=1 docker build \
  --secret id=nvidia_api_key,env=NVIDIA_API_KEY \
  -t policynim-hosted .
```

Important container defaults:

- the image bakes the LanceDB index at `/app/data/lancedb-baked`
- the image sets `POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked`
- the image sets `POLICYNIM_MCP_HOST=0.0.0.0` so hosted HTTP can bind inside the
  container
- the builder stage reads the bake-time key from the temporary BuildKit secret
  mounted at `/run/secrets/nvidia_api_key`
- if that secret is missing or empty, `docker build` fails while
  `policynim ingest` tries to bake the index
- the final image does not store the build-time `NVIDIA_API_KEY`
- runtime `NVIDIA_API_KEY` is still required because live `search` and
  `preflight` call NVIDIA-hosted APIs

Example hosted run:

```bash
docker run --rm -p 8000:8000 \
  -e NVIDIA_API_KEY=$NVIDIA_API_KEY \
  -e POLICYNIM_MCP_REQUIRE_AUTH=true \
  -e POLICYNIM_MCP_BEARER_TOKENS=token-a \
  -e POLICYNIM_MCP_PUBLIC_BASE_URL=http://localhost:8000 \
  policynim-hosted
```

Quick hosted-image test loop:

```bash
docker run --rm -p 8000:8000 \
  -e NVIDIA_API_KEY=$NVIDIA_API_KEY \
  -e POLICYNIM_MCP_PUBLIC_BASE_URL=http://localhost:8000 \
  policynim-hosted
```

Then verify the hosted HTTP surface from another terminal:

```bash
curl http://localhost:8000/healthz
curl -i http://localhost:8000/mcp
curl -i -X POST http://localhost:8000/mcp
```

What to expect:

- `GET /healthz` returns `200` with a JSON payload that includes `ready: true`
  when the baked index is present and non-empty
- a plain `GET /mcp` returns `406 Not Acceptable` because the client must accept
  `text/event-stream`
- a plain `POST /mcp` returns `400 Invalid Content-Type header` because the
  route expects a valid MCP HTTP request, not an empty form post
- if host port `8000` is already in use, publish another host port instead, for
  example `-p 8002:8000`, and update `POLICYNIM_MCP_PUBLIC_BASE_URL` to
  `http://localhost:8002`

## Railway Beta Deploy

The repo ships a root [`railway.toml`](../railway.toml) so Railway uses the root
`Dockerfile` and probes `GET /healthz`.

Recommended beta setup:

1. Create one Railway service from this GitHub repo.
2. Start from `.env.production.example` when translating settings into Railway
   service variables.
3. Set at least these Railway service variables:
   - `NVIDIA_API_KEY`
   - `POLICYNIM_ENV=production`
   - `POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked`
   - `POLICYNIM_MCP_HOST=0.0.0.0`
4. Deploy once so the service becomes healthy on `/healthz`.
5. Generate a Railway public domain for that service.
6. Mount one Railway volume at `/app/state` for the hosted auth SQLite database.
7. Set these runtime variables and redeploy:
   - `POLICYNIM_MCP_REQUIRE_AUTH=true`
   - `POLICYNIM_MCP_PUBLIC_BASE_URL=https://<generated-domain>`
   - `POLICYNIM_BETA_SIGNUP_ENABLED=true`
   - `POLICYNIM_BETA_AUTH_DB_PATH=/app/state/auth.sqlite3`
   - `POLICYNIM_BETA_SESSION_SECRET=<random-secret>`
   - `POLICYNIM_BETA_GITHUB_CLIENT_ID=<github-oauth-client-id>`
   - `POLICYNIM_BETA_GITHUB_CLIENT_SECRET=<github-oauth-client-secret>`
   - `POLICYNIM_BETA_DAILY_REQUEST_QUOTA=500`
- optionally set `POLICYNIM_MCP_BEARER_TOKENS=<break-glass-token>` if you want
  one operator-only fallback token on `/mcp`
- leave `POLICYNIM_MCP_PORT` unset on Railway unless you intentionally want to
  override Railway's injected `PORT`

Operator and client env mapping:

- Railway service vars:
  - `POLICYNIM_MCP_REQUIRE_AUTH=true`
  - `POLICYNIM_MCP_PUBLIC_BASE_URL=https://<generated-domain>`
  - `POLICYNIM_BETA_SIGNUP_ENABLED=true`
  - `POLICYNIM_BETA_AUTH_DB_PATH=/app/state/auth.sqlite3`
  - `POLICYNIM_BETA_SESSION_SECRET=<random-secret>`
  - `POLICYNIM_BETA_GITHUB_CLIENT_ID=<github-oauth-client-id>`
  - `POLICYNIM_BETA_GITHUB_CLIENT_SECRET=<github-oauth-client-secret>`
- client setup docs use:
  - `POLICYNIM_TOKEN=<generated portal token>`
- live smoke tests use:
  - `POLICYNIM_BETA_MCP_URL=https://<generated-domain>/mcp`
  - `POLICYNIM_BETA_MCP_TOKEN=<beta-token>`

Important hosted behavior:

- Railway injects `PORT`; PolicyNIM uses that automatically unless
  `POLICYNIM_MCP_PORT` is explicitly set
- when `POLICYNIM_ENV=production` and `POLICYNIM_MCP_HOST` is unset, PolicyNIM
  defaults hosted MCP binding to `0.0.0.0` so Railway health checks can reach
  the process
- the public beta MCP URL is always `https://<generated-domain>/mcp`
- the public self-serve portal URL is always `https://<generated-domain>/beta`
- `/healthz` stays public for Railway health checks
- `/mcp` returns:
  - `401 {"error":"Unauthorized."}` for missing, invalid, or revoked bearer tokens
  - `403 {"error":"Account suspended."}` for suspended self-serve accounts
  - `429 {"error":"Quota exceeded."}` when the UTC-day request quota is exhausted
- hosted MCP logs emit one JSON object per line for auth rejects and tool calls,
  including `auth_result`, `tool_name`, `latency_ms`, and
  `upstream_failure_class`

Opt-in Railway smoke test:

```bash
export POLICYNIM_BETA_MCP_URL=https://<generated-domain>/mcp
export POLICYNIM_BETA_MCP_TOKEN=<beta-token>
uv run --group test pytest -q -m live tests/test_hosted_mcp_live.py
```
