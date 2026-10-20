# Hosted operational evidence and capacity checks

PolicyNIM supports one hosted instance with its persistent SQLite state volume.
The concurrency target is ten active expensive operations for roughly 10–25 users
and up to 10,000 chunks. That target isn't a measured production capacity promise.
The load harness measures local HTTP dispatch, admission and SQLite retrieval with
deterministic provider doubles. Live NVIDIA latency, usage, quality and production
behavior require separate evidence against the deployed revision.

## Inspect local operational state

Run this on the host with its normal state-volume settings:

```bash
policynim beta-admin operations
```

The read-only JSON report contains active operations, retained completed/failed/
cancelled/interrupted outcomes and nearest-rank p50/p95/p99 duration over the most
recent 1,000 completed, failed or cancelled operations. Interrupted outcomes stay
counted but don't contribute latency: their recovery timestamp includes unknown
downtime. The duration uses persisted UTC wall timestamps from admission through worker
cleanup; it isn't an end-to-end client latency measurement. The report names
that clock. Rows with reversed timestamps are excluded from latency samples;
clock adjustments can still affect historical durations. Use the monotonic
provider/tool logs and harness timings for latency diagnosis. The report names that
sample window and count. It also reports local index state/build identity and
configured active/retention limits. It doesn't create or migrate absent state.
An existing auth database with no operation table reports zero usage without
creating that table. A missing or locked database produces a nonzero exit with state-volume recovery
guidance, without disclosing paths or database error text.

Principal identifiers, client tokens, request bodies, task text, source text and
individual operation IDs aren't aggregate report labels. Provider usage/retry
totals remain `null` in this ledger. Existing provider stage/attempt logs retain
actual token usage when supplied, with an explicit unknown marker otherwise.
Generation identity and durable job backlog remain unavailable in this slice;
#93/#101 integration must supply those fields before #103 can close.

`POLICYNIM_MCP_OPERATION_RETENTION_DAYS` defaults to `30` and accepts 1–365.
Each admission prunes at most 1,000 older terminal rows inside its write transaction. It never
removes active rows or today's usage, even if timestamps are inconsistent. Locked
or failed cleanup rolls back with admission, preserving prior accounting. Run at
successive admissions after changing retention to clear a larger eligible backlog.
Each transaction bounds deletion work; retention debt can remain between admissions.
Retained outcome counts describe the retained window; they aren't lifetime totals.
Back up the database and its `.owners` directory together. Active/recovery owner lease files stay preserved. Failed initialization and a
drained service remove their unused lease files. Monitor remaining recovery
metadata and remove only proven abandoned files during a stopped-service
maintenance window.

## Query content-free events

Hosted `mcp.tool` events contain tool name, latency, authorization result, stable
failure class and an internally generated correlation ID. Provider stage/attempt
events use the same ID across worker threads. Client JSON-RPC IDs are hashed before
logging; raw caller-controlled IDs can't become telemetry text. Admission rows keep
the same opaque correlation ID for operator-side incident inspection. Correlation
is evidence of the request path, not a user identity or a quality score.

Examples for an exported JSON-lines log stream:

```bash
jq -s '[.[] | select(.event == "mcp.tool")]
  | group_by(.upstream_failure_class // "ok")
  | map({outcome: (.[0].upstream_failure_class // "ok"), count: length})' hosted.jsonl
jq -s '[.[] | select(.event == "provider.operation" and .kind == "attempt")]
  | map({stage, attempt, failure_class, usage_status, usage_tokens, correlation_id})' hosted.jsonl
```

Keep stage and attempt timings separate; summing nested stages double-counts work.
A token count from one successful response doesn't prove failed attempts were free.
The application never enables raw-query analytics or full-environment capture.
Route logs through an operator-controlled sink with a documented retention policy;
use 30 days or less for this release and avoid unbounded indexed correlation labels.
Uvicorn raw access logs are disabled so OAuth callback query codes aren't logged.
Raw exception payloads/tracebacks aren't emitted for beta callback or auth-storage
fallback failures. Unexpected tool failures become a sanitized `internal_error`
with an opaque correlation ID before the SDK can log the original traceback.
Structured events retain the error class for diagnosis. Public readiness remains sanitized and doesn't expose this report.

## Run reproducible synthetic measurements

From a locked source checkout:

```bash
uv sync --locked --group test --group dev
uv run --no-sync python scripts/measure_hosted_capacity.py \
  --output /tmp/policynim-capacity-uniform.json \
  --users 25 --chunks 10000 --requests-per-user 2 --distribution uniform
uv run --no-sync python scripts/measure_hosted_capacity.py \
  --output /tmp/policynim-capacity-skewed.json \
  --users 25 --chunks 10000 --requests-per-user 2 --distribution skewed
```

The harness starts a new real loopback HTTP process per scenario and uses the
actual MCP app, authorization boundary, operation ledger and sqlite-vec retrieval.
It doesn't connect to an external issuer, NVIDIA or production. Fixture embeddings
are four-dimensional orthogonal vectors; reranking is disabled. The uniform corpus
spreads chunks over four domains. The skewed corpus places 90% in one domain and
spreads the remainder across the other three. Requests use domain filters and
record returned hit counts. Synthetic vectors don't test retrieval quality.

Reports include the source revision, dirty-state flag, harness digest, platform,
CPU count, Python version, provider delay, configured/client concurrency, throughput,
response outcomes, separate accepted/rejected latency percentiles, cheap
discovery/readiness percentiles, sampled active peak,
terminal ledger counts, sampled process RSS and process maximum RSS when available.
RSS sampling uses POSIX `ps` during probes, so the harness supports macOS/Linux.
The samples include setup/ingestion memory that remains resident; they can miss
short peaks. A missing shutdown-hook maximum stays `null`. A
warmup search contributes one explicitly identified ledger charge per scenario.
Provider usage stays unavailable; `live_nvidia_calls` stays zero.

The six named scenarios cover bounded baseline load, saturation above ten active
operations, a slow provider double, a write-locked quota database, client disconnect
and SIGTERM during accepted work. The harness observes an active ledger row before injecting disconnect or shutdown,
and records whether active work was observed. A disconnected client may leave synchronous work
to finish. The harness polls its ledger for up to ten seconds; it records either an
observed terminal outcome or an explicit drain deadline with active work remaining.
Report that evidence instead of assuming cancellation.
SIGTERM can drain accepted work successfully before shutdown. Transport failures
during intentional shutdown are identified separately from normal operation results.

## Operating limits and recovery

Keep one replica. Configure per-principal active/daily limits before opening access.
Mount persistent storage before enabling hosted usage. Container filesystem state
isn't durable across deployments. Set `POLICYNIM_BETA_AUTH_DB_PATH` to
`/app/state/auth.sqlite3` on the mounted volume; beta accounts, keys and operation
accounting share that database. Preserve its sibling `auth.sqlite3.owners/` directory
across restarts so interrupted-operation recovery can inspect old owner leases.
Use SQLite's online backup API for a running database and retain matching ownership
metadata; don't copy only the main database file while writers are active.
For durable evaluations, set `POLICYNIM_EVAL_WORKSPACE_DIR` to
`/app/state/evals/workspace`; the job database lives under `jobs/jobs.sqlite3`.
Back up evaluation artifacts and job state together, and verify restored job recovery
before reopening access. The retention setting removes eligible terminal operation
rows inside the auth database; it doesn't relocate files or provide volume durability.

Start with the documented global cap of ten and per-principal cap of two; these
protect admission and don't establish an end-to-end latency promise. For overload,
wait for active work to drain before retrying. For `admission_unavailable`, inspect
state-volume health and writer locks. For `accounting_incomplete`, preserve the
accepted charge and inspect ownership/outcome before retrying. Terminal updates
retry short writer contention for at most about 1.5 seconds.

Before claiming the target is supported in production, bind a report to the exact
release/deployment, generation, host, NVIDIA model settings and authorized request
budget. Include representative preflight/compile/evaluation work and provider
failures; verify job backlog/outcomes, shutdown recovery and memory over a sustained
window. Record hardware and workload mix. The local harness and offline tests can't
supply that production evidence, and this change doesn't authorize more replicas.

## Recorded local receipt

The [uniform receipt](evidence/hosted-capacity/uniform-synthetic.json) and
[skewed receipt](evidence/hosted-capacity/skewed-synthetic.json) were generated from
clean source `aba5d40452cf68f7f467d56faec8980867ef66b0` on the hardware named
inside each report. Each run used 25 synthetic callers, 10,000 chunks, two requests
per caller and a global cap of ten. Both baseline scenarios completed all 50
requests with ten active operations observed. Uniform baseline accepted-request
p95 was 210.707 ms; the receipt records separate saturation, locking, slow-provider,
disconnect and shutdown outcomes. These short local search measurements don't
establish production latency, sustained throughput, live provider behavior or
capacity for preflight, compilation or evaluation work.
