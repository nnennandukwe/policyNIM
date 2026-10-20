# Durable evaluation jobs

A live evaluation can outlast a client connection. PolicyNIM's application jobs accept a suite, archive its policy generation and persist progress so the same principal can inspect the outcome after disconnect or process restart. These jobs use ordinary MCP tools; they don't advertise the optional MCP Tasks extension.

## Submit and retrieve

The suite must pin its catalog digest or policy revisions. Submission captures its JSON, provider/model configuration, limits and complete generation archive in one SQLite transaction. Provider credentials and bearer tokens aren't stored. Changing source files, activating another generation or pruning the original generation can't change an accepted job's inputs.

```console
policynim eval-jobs submit suite.json --idempotency-key demo-2026 \
  --deadline-seconds 600 --max-provider-calls 100
policynim eval-jobs inspect JOB_ID
policynim eval-jobs worker --once
policynim eval-jobs result JOB_ID
policynim eval-jobs cancel JOB_ID
policynim eval-jobs prune
```

`submit` returns a queued job handle without running an evaluation. Run `worker` continuously to process the local queue, or use `worker --once` for at most one job. The local CLI identifies its operator through the operating-system user ID. It doesn't accept an arbitrary job owner argument.

Use the same idempotency key to retry an uncertain submission. Identical inputs return the original job, including after it finishes or its report expires. Changed inputs under an existing key return `idempotency_conflict`. Use a new key when you intentionally want another run. A request without an explicit generation ID retains its original resolved generation on duplicate submission.

Authenticated hosted MCP adds these tools:

| Tool | Behavior |
|---|---|
| `eval_submit(suite, idempotency_key, deadline_seconds?, max_provider_calls?)` | Accept immutable inputs and start the managed worker |
| `eval_inspect(job_id)` | Read accepted hashes, state and progress |
| `eval_cancel(job_id)` | Cancel queued work or request cancellation of active work |
| `eval_result(job_id)` | Read the terminal report manifest and bounded resource links |

Every job read, cancellation and report resource checks current caller scope and ownership. Ownership hashes issuer plus subject; another client for the same principal can reconnect. Unknown and other-owner IDs return the same `job_unavailable` error. Every application job operation needs both `policies:read` and `policies:evaluate`. An authenticated server resumes queued work during startup. Unauthenticated MCP sessions don't expose these application job tools.

MCP results reference `policynim://evaluation-jobs/{job_id}/report` and numbered `/report/{part}` resources. Each part contains at most 128 KiB of canonical ASCII JSON text plus its SHA256 digest. Concatenate `content` in manifest order to recover the complete report, then verify `report_sha256`. The manifest and each part require fresh authorization. This keeps individual events below the SDK's default transport limit. Part digests commit with the terminal report; each download reads one bounded slice from that canonical report through SQLite's readonly incremental BLOB API and checks its saved digest. There's no duplicate fragment payload to drift. Schema 5 migrates earlier job stores; retained schema 1–4 reports gain part digests once after full verification, keeping their original report bytes and digest.

## Bounds and usage

The default queue accepts at most eight nonterminal jobs globally and two per owner. One kernel-locked worker processes one job at a time per database. Queue rejection doesn't start provider work. A nonblocking kernel lock bounds generation archive capture to one concurrent submission per principal, including across processes. Overlapping captures return `principal_busy`; retry with the same key after the first capture finishes. Already accepted duplicates return their existing handle before capture. A noncharging keyed allowance check rejects exhausted callers after sealed-suite validation and before archive capture; it still permits matching prepaid retries after a job transaction rolls back. That check's advisory: submission rechecks and reserves validated work units atomically through the shared principal admission service before the queued job is published. A unit is one case execution in one selected configuration. The current job interface selects one configuration, including its rerank flag, so a 20-case job consumes 20 units. Interactive expensive calls consume one unit. Accepted-operation counts and active slots remain separate: one job is one accepted operation and holds one active slot during execution. Queued jobs hold no active slot.

An over-budget submission creates no queued job or provider work. Identical accepted duplicates and polling consume no additional units. Worker activation checks current authority and capacity without charging again. Accepted cancellation, failure or interruption retains its prepaid units. Provider attempts, retries, measured tokens and any monetary cost are separate from work units; the daily allowance doesn't claim to cap provider calls or spending. If submission confirmation is lost between the quota and job commits, retrying the same key reuses the original reservation. Changed inputs under that reservation key are rejected. The quota and job databases don't share one transaction: a quota commit followed by a job rollback can retain prepaid units without a published job. That accepted charge isn't refunded; retry the same key and inputs to recover the reservation. Inactive accepted-operation counts can therefore include unpublished reservations and aren't queue depth.

Default limits are 100 cases, 3,600 seconds, 512 provider attempts and seven days of report retention. The CLI can set up to 1,000 cases, 86,400 seconds, 10,000 attempts and 30 days of retention. Hosted MCP restricts submission to at most 3,600 seconds and 512 attempts. Suite JSON is limited to 1 MiB, and each case ID is limited to 256 UTF-8 bytes. The deadline starts when the job's accepted, including queue time, and survives restart. A pre-provider `server_busy`, `principal_busy` or `admission_busy` rejection returns the job to its durable queue for a one-second retry, capped by that deadline; inspection exposes `next_attempt_at`. Archive loading, hashing and restoration happen only after capacity admission, so waiting retries read the accepted claims without repeatedly unpacking the archive. Started provider work never enters this retry path. Each provider attempt, including retries, reserves its budget durably before dispatch. Cancellation, deadline or persistence failures that prevent dispatch correct the known-unsent reservation; sent or transport-uncertain attempts retain their recorded usage. Zero-attempt reports say `not_performed`. Cancellation, deadline and call-budget checks run before the next provider stage.

Reports record the exact generation, suite/configuration/snapshot hashes, completed and unfinished cases, uncertain active case, provider attempts, measured token subtotal and unavailable usage attempts. Missing provider usage isn't zero usage. Token or monetary cost isn't inferred when the provider hasn't returned evidence.

## Recovery and retention

States progress from `queued` to `running`, then `completed`, `failed`, `cancelled`, `deadline_exceeded`, `budget_exhausted` or `interrupted`. Queued cancellation and deadline expiry can finish without any provider call. A running cancellation first sets a durable request; synchronous in-flight HTTP work drains before cleanup and terminal state. Worker shutdown follows the same cleanup boundary.

Each completed case commits before the next case begins. A crashed worker's running job becomes `interrupted` on the next worker's recovery pass. Completed cases remain in the report; an active case is marked uncertain. The worker doesn't replay terminal or uncertain provider work automatically. Inspect its evidence, then submit a new key if another evaluation's needed. A provider may have accepted a request whose response wasn't persisted, so interruption doesn't prove the provider performed no work.

Terminal state and report hash commit together. If stored inputs or case artifacts fail integrity validation, the job becomes `failed` with `job_artifact_invalid`, its unverified artifacts remain for the accepted retention window starting at detection, and no verified report's fabricated. Other jobs can continue. A storage failure can leave the last transition uncertain; preserve the volume, repair access and inspect before retrying. Submission retries must retain the same key. If quota accounting returns `accounting_incomplete`, or a retained job's bound accounting record is missing or mismatched, the worker pauses before claiming another queued job. Repair and inspect accounting, then restart the hosted process; creating another per-job admission service doesn't bypass that pause.

Expired reports return `result_expired`. Worker maintenance or `eval-jobs prune` removes expired input/report payloads and unreferenced generation archives. Job summaries, original hashes and idempotency records remain. Keyed quota reservations also retain compact permanent tombstones so a historical key can't silently recharge after ordinary accounting retention. Retention isn't an indefinite evidence archive; export required reports before expiry.

## Supported topology

Job state lives at `<eval_workspace_dir>/jobs/jobs.sqlite3`. Mount the existing evaluation workspace on a private persistent local volume. SQLite uses WAL and FULL synchronization; database creation uses mode 0600 and newly created parent directories use mode 0700. Restrict access to the whole workspace, including SQLite sidecars and restored generation artifacts. Operators must protect and back up this caller-specific evidence.

One local filesystem and one worker per database are supported. A background worker claims its kernel file lock before startup returns and retains it through shutdown. A competing CLI or hosted worker receives `worker_busy` at startup. The lock excludes competing workers, and database transactions guard queue claims and idempotency. Distributed workers and network filesystems aren't supported. Physical durability still depends on the mounted volume. A hosted process owns its background worker, drains it on shutdown and resumes queued jobs on startup. Provider credentials come from the current worker environment; accepted authorization claims are rechecked before execution, including expiry and beta-key revocation. Terminal quota reconciliation binds the stored owner and operation; it doesn't require an expired token to become valid again. A worker crash after quota completion and before the job report commits still leaves the running job interrupted on recovery, with its persisted cases retained and no provider replay.

Phoenix project lookup creates a project only after a confirmed HTTP 404. Authentication, authorization, server and transport failures propagate without attempting creation.

## Tasks compatibility

The project's pinned MCP Python SDK 2.2.0 doesn't implement the optional Tasks extension. Upstream 2.3.0 still lists it as unimplemented in its [versioned roadmap](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/ROADMAP.md). Native Tasks discovery and client interoperability remain blocked under #102. No application job handle should be described as native Tasks support.

Offline tests cover real SQLite transactions, generation archive restoration after pruning, process-kill recovery, cancellation drain, retry budgets through the NVIDIA SDK HTTP boundary, owner isolation, CLI commands and an authenticated MCP disconnect/reconnect flow. These checks don't establish live model quality, hosted volume configuration or production capacity; release evidence must verify those separately.

Operator reports retain `active_generation` for the observed activation pointer and add `served_generation` for the compatible generation selected by configured retrieval. `generation_selection` is `active` or `pinned`; index/generation readiness describes that serving selection. A retained pin can be ready without an active pointer. Unavailable identity validation returns null identifiers rather than claiming a generation is ready. Late cancellation observed by terminal quota accounting also marks the job cancelled after cleanup, while retaining completed case evidence.
