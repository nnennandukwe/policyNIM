# PolicyNIM Hosted Beta Operations

Use this guide when you want the longer version of the hosted beta flow,
recovery notes, or operator deployment checklist.

## Self-Serve Hosted Beta

This guide describes the existing bearer-key deployment. For the optional
external issuer foundation, migration, and remaining interoperability gate, see
[hosted authorization](hosted-authorization.md). GitHub portal sign-in doesn't
obtain an MCP OAuth token.

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

If you want PolicyNIM to print the exact hosted setup command for one client,
run `uv run policynim quickstart --target hosted-mcp --client codex` or
`uv run policynim quickstart --target hosted-mcp --client claude-code`.

Then ask your client to call the MCP tools directly:

- `Use policy_preflight for: Implement a refresh-token cleanup background job.`
- `Use policy_search for: refresh token cleanup background job`

Hosted beta notes:

- The server uses Python MCP SDK `2.2.0`, serving MCP `2026-07-28` and legacy
  clients over stateless HTTP. Beta API keys remain the authorization mechanism;
  the SDK upgrade doesn't introduce standards-based MCP OAuth.
- Set `POLICYNIM_MCP_PUBLIC_BASE_URL` to the deployed origin so MCP Host and
  Origin checks allow the public endpoint. Reverse proxies must preserve an
  allowed Host header; clients without an Origin header remain supported.
- Deploy at the service root. ASGI `root_path` and mount prefixes are rejected
  so generated MCP, beta, and OAuth links always address the served routes.
- `POLICYNIM_MCP_MAX_CONCURRENT_OPERATIONS` defaults to `10` per server process.
  Saturated calls return a retry-later tool error before provider work starts.
  Cancellation retains capacity until the operation and its cleanup finish.
- A server process admits one key-regeneration request per account at a time.
  An overlapping request receives HTTP `409`; wait for the first request and
  refresh `/beta` before trying again. Later deliberate rotations still revoke
  the previous key. This guard doesn't coordinate independent server processes.
- replace `https://<railway-domain>/mcp` with the deployed Railway beta URL
- self-serve users should start from `https://<railway-domain>/beta`, not from
  an operator-issued secret
- `POLICYNIM_TOKEN` is a client-side shell variable only. It'sn't a
  PolicyNIM app setting
- `POLICYNIM_MCP_BEARER_TOKENS` is optional and reserved for operator
  break-glass access on `/mcp`
- if you run the opt-in live smoke test locally, export the same deployed values
  as `POLICYNIM_BETA_MCP_URL` and `POLICYNIM_BETA_MCP_TOKEN`

## 60-Day Readiness Verification

Use this checklist before calling the hosted beta ready for a release window.
The normal CI suite stays offline; this smoke path intentionally calls the
deployed Railway service and NVIDIA-backed MCP tools.

Required deployed-service checks:

```bash
curl -fsS https://<railway-domain>/healthz

export POLICYNIM_BETA_MCP_URL=https://<railway-domain>/mcp
export POLICYNIM_BETA_MCP_TOKEN=<beta-token>
uv run --group test pytest -q -m live tests/test_hosted_mcp_live.py
```

Expected evidence:

- `/healthz` returns `200` with `ready: true`, `row_count` greater than `0`, and
  `mcp_url` matching `https://<railway-domain>/mcp`
- invalid bearer-token smoke returns `401 {"error":"Unauthorized."}`
- authenticated MCP smoke lists exactly `policy_preflight` and `policy_search`
- `policy_search` returns at least one cited hit for the smoke query
- `policy_preflight` returns a grounded summary, citations, and
  `insufficient_context: false`

GitHub Actions smoke:

- run the manual `Hosted Beta Smoke` workflow only when the deployed Railway
  service and a beta token are available
- set repository secrets `POLICYNIM_BETA_MCP_URL` and
  `POLICYNIM_BETA_MCP_TOKEN` before dispatching the workflow
- the regular `CI` workflow remains offline and runs pytest with
  `-m "not live and not docker_live"`

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
- for transient timeout, connection, rate-limit, or server errors, retry after a short delay
- HTTP 410 is classified as `endpoint_unavailable` and isn't retried; verify
  the operation's model and endpoint configuration against the current NVIDIA
  catalog and follow [provider recovery](hosted-provider-recovery.md)
- if the failure persists, the operator should inspect hosted MCP logs for
  `upstream_failure_class` such as `timeout`, `connection`, or `rate_limit`

### Insufficient Context

- `insufficient_context=true` is a grounded no-answer, not an auth or
  availability failure
- recover by narrowing the task, adding a domain, or calling `policy_search`
  first to inspect the retrieved evidence

### Service Unavailable

- if the hosted MCP URL doesn't respond, or `/healthz` returns `503`, the
  hosted service or baked local index isn't ready yet
- retry after the service becomes healthy. If you operate the service, check the
  Railway deploy state and `/healthz` first
- if `/healthz` includes `Local index readiness could not be inspected`, use the
  sanitized exception class in the payload, then check the Railway deploy logs
  for detailed index path, volume, or file-permission failures
- if `/healthz` reports a missing or invalid generation, check
  `POLICYNIM_GENERATIONS_DIR` and run `policynim generation inspect`. Import a
  verified bundle or explicitly migrate a matching standalone pair before startup.
  Startup never ingests or calls NVIDIA.
- if the live smoke passes `/healthz` but fails `policy_search` or
  `policy_preflight`, inspect hosted MCP logs for `upstream_failure_class`
  before rotating auth tokens or rebuilding the image

## Container Build For Hosted HTTP

Build the production image from the checked-in verified public generation:

```bash
docker build -t policynim-hosted .
```

Both `Dockerfile` and `Dockerfile.railway` run `generation import` with the trusted
bundle checksum and `--require-source-match`. Builds install locked dependencies,
then validate and activate source text, catalog, SQLite vectors, runtime rules,
model identity, and notices without model credentials. Changed policy sources
require an explicitly refreshed bundle; a stale artifact fails the build.

The runtime reads `/app/data/generations` through `POLICYNIM_GENERATIONS_DIR`.
Health and MCP discovery work without an NVIDIA key. Live search and preflight
still require runtime `NVIDIA_API_KEY`. Each request retains its selected generation
until cleanup; a new activation affects subsequent requests.

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
  when the complete active generation validates
- a plain `GET /mcp` returns `406 Not Acceptable` because the client must accept
  `text/event-stream`
- a plain `POST /mcp` returns `400 Invalid Content-Type header` because the
  route expects a valid MCP HTTP request, not an empty form post
- if host port `8000` is already in use, publish another host port instead, for
  example `-p 8002:8000`, and update `POLICYNIM_MCP_PUBLIC_BASE_URL` to
  `http://localhost:8002`

## Railway Beta Deploy

The repo ships a root [`railway.toml`](../railway.toml) so Railway uses
[`Dockerfile.railway`](../Dockerfile.railway) and probes `GET /healthz`.

Railway uses the same offline artifact-import contract as the standard
[`Dockerfile`](../Dockerfile). Neither image path accepts an embedding key during
build. Set `NVIDIA_API_KEY` as a runtime service variable for live retrieval.
Embedding model and endpoint settings must match the imported generation, even
when another model has the same vector dimension. See
[policy generations](policy-generations.md) for migration, rollback, retention,
and bundle refresh instructions.

Recommended beta setup:

1. Create one Railway service from this GitHub repo.
2. Start from `.env.production.example` when translating settings into Railway
   service variables.
3. Set at least these Railway service variables:
   - `NVIDIA_API_KEY`
   - `POLICYNIM_ENV=production`
   - `POLICYNIM_INDEX_DB_PATH=/app/data/index.sqlite3`
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
  - MCP tool error `quota_exceeded` when accepted-operation allowance is exhausted;
    discovery remains usable, and the tool message gives the UTC reset time
- hosted MCP logs emit one JSON object per line for auth rejects and tool calls,
  including `auth_result`, `tool_name`, `latency_ms`, and
  `upstream_failure_class`

## 90-Day Operator Beta Release Gate

Use this gate before tagging a hosted beta release or handing a deployment to an
operator. The default checks stay offline and deterministic:

```bash
uv run ruff check .
uv run pyright
uv run pytest -q -m "not live and not docker_live"
git diff --check
```

Capture runtime evidence for any local release workflow that uses
`policynim runtime execute`:

```bash
uv run policynim evidence report \
  --session-id <id> \
  --format markdown \
  --output reports/<id>.md
```

Inspect hosted beta account and key activity from the operator CLI:

```bash
uv run policynim beta-admin audit-log --limit 50
uv run policynim beta-admin audit-log --github-login <login>
uv run policynim beta-admin audit-log --event-type api_key_rotated --limit 10
```

The audit log output is JSON, newest-first, and redacts secret-bearing detail
keys. It may show API key prefixes because those are stored as recovery metadata;
it mustn't expose full API keys, bearer tokens, or key hashes.

Optional checks stay explicitly opt-in:

- run the deployed-service smoke only when hosted secrets are configured:
  `uv run --group test pytest -q -m live tests/test_hosted_mcp_live.py`
- run Docker hosted-image checks only when Docker is running and the local opt-in
  flag is set:
  `POLICYNIM_RUN_DOCKER_TESTS=1 uv run --group test pytest -q -m docker_live tests/test_docker_build_live.py`

Opt-in Railway smoke test:

```bash
export POLICYNIM_BETA_MCP_URL=https://<generated-domain>/mcp
export POLICYNIM_BETA_MCP_TOKEN=<beta-token>
uv run --group test pytest -q -m live tests/test_hosted_mcp_live.py
```

## Operation deadlines and NVIDIA client ownership

Each search, route, compile, preflight, or regeneration operation gets one monotonic
budget. Nested stages share it, including conformance and regeneration attempts.
Evaluation gives each case its own budget, including optional conformance; ingestion
gives each embedding batch a fresh budget, unless a caller explicitly supplies an
outer budget. A large corpus doesn't have to finish within one batch's deadline.
CLI and MCP use the same checks. Index readiness and citation validation still run;
a deadline never turns missing evidence into a successful result.

| Setting | Default | Meaning |
| --- | --- | --- |
| `POLICYNIM_OPERATION_TIMEOUT_SECONDS` | `120` | Shared budget for one operation or evaluation case. |
| `POLICYNIM_NVIDIA_TIMEOUT_SECONDS` | `30` | Per-attempt HTTP timeout, reduced to the remaining budget. |
| `POLICYNIM_NVIDIA_MAX_RETRIES` | `2` | Additional attempts after the first; allowed range is 0–10. |
| `POLICYNIM_NVIDIA_RETRY_INITIAL_SECONDS` | `0.5` | Initial exponential-backoff ceiling. |
| `POLICYNIM_NVIDIA_RETRY_MAX_SECONDS` | `8` | Maximum retry wait. |

Timeouts and delay settings must be finite and positive. Settings use the normal
precedence: explicit constructor values, process environment, configured dotenv,
then defaults. No new storage format or optional dependency is required.

429, 5xx, network failures, and HTTP timeouts can retry. Backoff grows exponentially,
with jitter between half the ceiling and the ceiling, capped by the maximum wait.
A valid `Retry-After` delta or HTTP date sets a minimum wait. A wait that exceeds
the cap while an operation still has enough time fails with `retry_after_limit`.
A wait that leaves no remaining operation budget fails with `deadline_exhausted`.
Neither condition retries early. Invalid headers fall back to jitter.
Authentication, rejected requests, retired endpoints, and malformed responses don't
retry. Retry exhaustion retains the provider failure class, such as `rate_limit`
or `timeout`.

MCP owns reusable SDK and HTTP clients for one server lifespan. Operations borrow
clients with matching endpoint, credential, and timeout settings; they don't mutate
client defaults or close a borrowed client. The HTTPX transport supports use from
multiple threads. Each operation still builds its own service/index graph, so
requests don't share mutable retrieval state. CLI adapters own their clients and
close them through the command's service cleanup.

Cancellation signals the shared budget and interrupts retry waits. A synchronous
HTTP call that's already running drains before its worker slot is released;
HTTPX timeouts bound individual I/O phases rather than an absolute wall-clock
interrupt. Budget checks reject late results and prevent another stage or attempt.
Shutdown rejects new operations, signals active budgets, drains workers and service
cleanup, then closes pooled clients. An upstream request may already have incurred
usage when cancellation or a deadline occurs. Neither state proves zero usage.

Hosted JSON events from `policynim.operations` contain stage latency, attempt number,
provider failure class, and measured token usage or an explicit `unknown` marker.
A `kind` field separates stage events from attempts, and service totals use
`compile_pipeline` / `conformance_pipeline` names distinct from provider stages.
They don't include task text, policy text, credentials, response bodies, or exception
messages. Token counts describe responses that supplied usage; they aren't a bill
or proof that failed attempts were free. Stage events and attempt events are separate
measurements and shouldn't be added together as total latency.


## Principal operation limits

Hosted authentication doesn't consume operation allowance. Accepted expensive work
(search and preflight) charges once when its admission transaction
commits, before providers start. Later provider failure, cancellation or process loss
keeps that charge: upstream usage may already exist. Invalid credentials, missing
scopes, saturation and exhausted allowance start no provider and add no charge.
A cancelled/expired worker is rejected before reservation, and cancellation is
checked again with the write-locked snapshot; neither check charges usage.
Initialization, discovery, public health, resources and job polling aren't expensive
operations. Durable evaluation submission deduplication and worker integration belong
to #101; polling or returning an existing submission must never create another charge.

| Setting | Default | Meaning |
| --- | --- | --- |
| `POLICYNIM_MCP_MAX_CONCURRENT_OPERATIONS` | `10` | Global active work, held until worker cleanup finishes |
| `POLICYNIM_MCP_MAX_CONCURRENT_OPERATIONS_PER_PRINCIPAL` | `2` | Active work per authenticated issuer/subject |
| `POLICYNIM_MCP_PRINCIPAL_DAILY_OPERATION_QUOTA` | `500` | Accepted operations per OAuth/operator principal per UTC day |
| `POLICYNIM_BETA_DAILY_REQUEST_QUOTA` | `500` | Accepted operations per beta account per UTC day; historical name retained |
| `POLICYNIM_BETA_AUTH_DB_PATH` | `data/runtime/auth.sqlite3` | Durable hosted account and operation state, including OAuth-only usage |

All numeric limits must be positive. Settings retain constructor, environment and
configured dotenv/default precedence. OAuth principal identity uses issuer plus
subject, so switching clients doesn't reset usage. Beta identity uses the account,
so key rotation doesn't reset it. Operator key replacement creates a new principal;
operators must rotate keys deliberately and keep their aggregate provider budget.
The portal's `accepted operations` count and remaining allowance use the new
operation rows. Historical HTTP authentication counters remain preserved separately
and aren't represented as provider work. This migration starts accepted-work usage
from zero; it doesn't rewrite audit history.

The service serializes key/account revalidation, daily quota and global/principal
active limits with SQLite `BEGIN IMMEDIATE`. Authentication alone grants no future
admission: key rotation, revocation and suspension are checked again at admission.
No token or raw task/policy content is stored in operation rows. Rows contain a
hashed principal identifier, random operation ID, operation name, timestamps, local
process ID and outcome. Outcomes are completed, failed, cancelled or interrupted;
accepted rows remain active until cleanup. Provider token usage remains `unknown`
in this ledger; existing stage events report measured provider usage when supplied.
Neither accepted-operation counts nor estimated usage imply financial billing.

The supported topology is one instance with its persistent writable state volume.
Each service holds a private kernel-backed owner lease alongside the database in
`<database>.owners/`. Process exit releases that lease even when a restarted
container reuses its PID. Startup marks accepted rows from unlocked prior owners
interrupted without refunding usage. It preserves live owners and unknown/missing
lease files. Back up the database and owner directory together; don't delete or
replace lease files while the service runs. If ownership can't be proven, stop the
service, preserve state and inspect abandoned rows before repairing them. These
local filesystem locks aren't cross-host coordination; don't add replicas.
Terminal writes retry SQLite lock contention for a bounded 1.5 seconds before
reporting incomplete accounting. Active capacity stays reserved during that retry.

MCP errors `server_busy` and `principal_busy` tell callers to retry after active work
finishes. `quota_exceeded` tells them to retry after 00:00 UTC. Database lock/open
failures return `admission_unavailable` before providers start: check the volume and
writer locks before retrying. If recording a terminal outcome fails after accepted
work, `accounting_incomplete` warns that work may have completed and advises against
blind retries. The accepted row and charge stay intact; inspect and recover state.
Keep the hosted SQLite file on the persistent volume, include it in backups, and
monitor its size. This release doesn't establish a retention/capacity envelope;
#103 owns measured operating limits and retention policy.
