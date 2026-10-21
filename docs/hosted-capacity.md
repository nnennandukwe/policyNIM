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
The report validates the active managed generation without provider calls and
reports its ID only when it's ready. An absent generation is `missing`; damaged
generation metadata is `invalid`. The owned generation reader closes after
inspection. Generation inspection registers a temporary reader lease to protect
retention. The operator needs write access to generation lock/lease metadata;
a read-only mount yields `unreadable` readiness. Canonical generation contents
and operation/job records aren't changed by the report. `job_backlog` reads aggregate durable job state counts from the private
evaluation workspace. If job storage hasn't been created, its value remains `null`
with `job_telemetry_status=unavailable_no_job_store`; the report doesn't create it.
Job worker executions mint a fresh correlation ID for provider work and admission,
independent of the submitting client's connection.

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
Before measurement, the child writes up to four synthetic Markdown policies and
publishes their requested sections through the normal managed-generation build.
Health and search use their normal service factories, generation validation and
reader admission; only provider-component construction is doubled. The child's
normal environment-backed settings point at its owned fixture state, with private
home/config/data paths and dotenv discovery disabled. Bearer tokens use the
supported comma-separated environment contract. It doesn't read personal profiles
or call an external issuer.

Each scenario records the expected published generation ID/count separately from
observed health IDs/counts. Typed health responses must match that publication,
and every successful search response must carry its generation ID, the expected
number of unique hits and the requested domain on each hit. A mismatch
can't count as a successful probe/search or a complete managed measurement. The
report names this scope as `configuration.index_scope=managed_generation`.
Discovery probes must advertise the fixture's eight tools; an empty or changed
tool set can't establish a successful paired probe or complete managed evidence.
Compilation/preflight, concurrent publication/retention and durable job backlog
still need separate evidence. Fixture publication happens before load starts;
it isn't a publication-throughput measurement.

Reports include the source revision, dirty-state flag, harness digest, platform,
CPU count, Python version, provider delay, configured/client concurrency, throughput,
response outcomes, separate accepted/rejected latency percentiles, cheap
discovery/readiness percentiles, sampled active peak,
terminal ledger counts, sampled process RSS and process maximum RSS when available.
RSS sampling uses POSIX `ps` during probes, so the harness supports macOS/Linux.
The samples include setup/ingestion memory that remains resident; they can miss
short peaks. The server captures its process maximum at application lifespan shutdown,
before Uvicorn restores SIGTERM, and names that phase in `peak_server_rss_scope`.
A failed or missing shutdown measurement stays `null`. A
warmup search attempt is reported separately with `warmup_outcome`. A successful
warmup contributes one ledger charge; failed attempts retain their actual ledger
outcome and leave the scenario incomplete.
Provider usage stays unavailable; `live_nvidia_calls` stays zero.

`paired_active_work_probe_observed` records whether one successful discovery and
health probe cycle had active ledger work both before and after its HTTP responses.
Baseline, saturation and slow-provider scenarios require that paired observation
for `measurement_complete=true`. Successful probes only before work starts, after
it finishes, or without active work leave those measurements incomplete. The
locked-database scenario intentionally admits no work and doesn't require this
signal. Disconnect/shutdown also require the existing paired observation before
the intervention; disconnect additionally requires an observed terminal drain.
An incomplete scenario remains in the report and makes the harness exit nonzero.
This overlap check doesn't define a latency SLO or prove continuous peak load.

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
Quiesce admissions and drain or stop workers before preserving a matching database
and owner-directory snapshot. Use SQLite's online backup API rather than copying only
the main file while writers are active. A database-only emergency restore can leave
unknown ownership; preserve those reservations for operator reconciliation.
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
clean source `3838e0d6c220d3664b11da2fda31c3526a4acb9f` on the hardware named
inside each report. Each run used 25 synthetic callers, 10,000 chunks, two requests
per caller and a global cap of ten. Both baseline scenarios completed all 50
requests with ten active operations observed. Uniform baseline accepted-request
p95 was 215.422 ms; the receipt records separate saturation, locking, slow-provider,
disconnect and shutdown outcomes. These short local search measurements don't
establish production latency, sustained throughput, live provider behavior or
capacity for preflight, compilation or evaluation work.

Those historical receipts used the standalone SQLite fixture and replaced its
health/search factories. They also predate the explicit paired-probe completeness
gate. Their recorded timings and revision remain intact; they aren't final-source
proof of managed-generation readiness or cheap probes overlapping active work.
Run the current managed path against the final candidate before using new receipts
for #103 acceptance.

Fault receipts report one configured concurrent client when only one request is
attempted. If a client task already completed before cancellation, the intervention
is labeled `client_task_already_completed`; that run doesn't prove a disconnect.
Unexpected browser callback errors report an unknown stage and the exception class
with fixed recovery guidance for identity-provider reachability and account storage.
They don't label every unknown error as an upstream failure or log raw error text.

Readiness transport failures during intentional shutdown remain counted as
`health_transport_failure`; they don't silently end the sampling series.
Unexpected probe exceptions fail the harness with a sanitized exception class
instead of producing a success receipt with incomplete probes.

The report separates confirmed live-owner work (`active_operations`) from all
reserved rows (`reserved_operations`), abandoned owners awaiting transactional
recovery (`unreconciled_operations`) and unavailable ownership evidence
(`unknown_owner_operations`). Observation acquires only existing kernel leases;
it creates no owner files and doesn't mark rows recovered. Admission still counts
all reserved rows until its own committed recovery, so observation can't free a slot.
Load-generator report reads run in worker threads so SQLite waits don't block HTTP
client timers on the asyncio event loop.

Unexpected tool/callback diagnostics retain at most eight owned package locations
(module, function and line) and four exception classes. They omit filenames,
source lines, locals, exception messages and request/provider bodies. The innermost
owned location helps identify a failure stage without exposing a raw traceback.

The harness validates JSON-RPC version/request identity and typed MCP discovery/tool
results before counting success. HTTP 200 protocol errors, missing results and malformed
search payloads produce explicit error outcomes. A bad warmup leaves its scenario
incomplete while preserving earlier scenarios and subsequent bounded measurements
in the receipt. Initial malformed or mismatched ready responses also stay in the
failing report with `readiness_outcome`, preserving prior scenarios.
`unexpected_probe_failure_counts` retains failed discovery/readiness
classes during measurement; those failures also leave managed evidence incomplete.
Availability failures after the intentional shutdown intervention remain expected.
Responses and validation error text never enter the receipt.

On POSIX hosts, recovery and read-only reporting use compatible shared lease probes,
while workers retain exclusive ownership. A report observer can't delay committed
crash recovery. Destructive retired-file cleanup still requires exclusive proof and
can defer while an observer holds a lease. Other platforms retain conservative probes.

Owner teardown closes its descriptor before removing matching final and temporary
aliases, so a deferred sharing-denial cleanup can complete after release. If SQLite
can't confirm that the owner's accepted rows have drained, shutdown logs a sanitized
class and keeps the lease. Retry close after storage recovers or let process exit
release ownership; an uncertain read never releases a potentially active worker.
