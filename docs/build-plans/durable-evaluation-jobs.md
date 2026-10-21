# Durable Evaluation Jobs Build Plan

## Summary

Implement #101's application evaluation-job lifecycle with pinned inputs, bounded work, owner isolation and restart evidence. Baseline: the grounded MCP branch plus #119's public per-case evaluator and #98's principal admission. One stacked PR. Native MCP Tasks stays blocked under #102.

## Skills To Use

| Stage | Skill | Responsibility |
|---|---|---|
| Planning | software-build-plan | Define accepted job behavior and review boundaries. |
| Standards | qodo-get-rules | Apply loaded Python/schema/service rules. |
| Durable state | transaction-proof | Commit-point, ownership, failure and recovery model below. |
| Concurrency | sqlite-concurrency-hardening | Transactional claims, deduplication and kernel worker ownership. |
| Workflow | workflow-invariants | Explicit job and case transitions; forbid terminal reexecution. |
| Failure verification | failure-path-testing | Disconnect, restart, cancellation, saturation, deadline and authorization. |
| Interface audit | dx-audit | Match CLI/MCP schemas and recovery documentation. |
| Review | qodo-review | Final full diff against a pushed dependency fixed point, remediation and re-review. |

Qodo Local Review eligibility: eligible repository; use the pushed #119 dependency base. No new runtime dependencies. Provider implementation stays NVIDIA. Phoenix lookup correction from #73 is narrowed to confirmed-404 creation.

## Scope

In scope: durable submit/inspect/result/cancel services and CLI, bounded single-worker topology, optional hosted application-job tools and authenticated report resources, immutable reports and retention, provider call budgets/usage, public live per-case evaluator integration, narrow Phoenix lookup fix.

Out of scope: native Tasks, distributed scheduling, exact provider replay, automatic replay of interrupted provider stages, custom remote policy administration.

## Package Layout / File-to-Task Mapping

- job_types.py: typed immutable submission, state, usage and report contracts.
- storage/evaluation_jobs.py: SQLite transactions, deduplication, queue claims, case/report persistence and expiry.
- services/evaluation_jobs.py: pinned suite/generation capture, principal checks, worker lifecycle, provider execution and recovery.
- interfaces/evaluation_jobs_cli.py: submit, inspect, result, cancel, worker and expiry commands.
- interfaces/evaluation_jobs_mcp.py: authenticated application-job operations/report reads; no Tasks capability.
- operations.py and providers/nvidia.py: enforce and report actual provider attempt limits.
- tests/test_evaluation_jobs.py: storage/service/process lifecycle integration.
- docs/evaluation-jobs.md: topology, authority, budgets, retention and recovery limits.

## Dependencies And Settings

Reuse locked Python 3.11/3.12, SQLite, MCP and existing NVIDIA providers. Job state lives under the existing eval workspace in a private SQLite database; schema version 5 (migrating development versions 1 through 4). Queue limit, max cases, deadline, call budget and retention have bounded service defaults, with explicit CLI flags. Secret settings aren't serialized. Pinned generation archives are content-addressed SQLite blobs, committed with accepted specs and restored only into an owned private generation store.

## Canonical Contracts

States: queued -> running -> completed | failed | cancelled | deadline_exceeded | budget_exhausted | interrupted. queued -> cancelled or deadline_exceeded is allowed without provider execution. Running cancellation is requested first; terminal state is recorded after provider work drains. A transient capacity/admission-lock rejection can return running to queued only before any case or provider attempt starts; a persisted one-second retry never extends the original deadline. Terminal jobs never execute again.

Submission requires an idempotency key scoped to issuer+subject. Identical key+snapshot returns the existing job; different inputs under that key return a conflict. Queue rejection starts no worker/provider work. Inspection/result/cancel always check current caller scope and owner. Unknown and other-owner IDs share a non-disclosing error.

Reports include captured suite/configuration digests, exact generation, provider/model IDs, completed and unfinished cases, actual attempt count, measured token subtotal, unavailable usage markers and artifact hashes. No unsupported token/cost estimate is presented as measured.

## TDD And BDD Implementation Strategy

Verification-first slices: store submit/dedup/owner checks; concurrent queue admission/claims; pinned generation capture and restart restoration; per-case results and immutable reports; cancellation/deadline/call budget; process interruption and no-repeat proof; actual CLI/MCP dispatch; confirmed Phoenix lookup contract. Fake model calls only at provider/evaluator boundaries; real SQLite, generation archives and worker locks remain in integration paths.

## Component Design

One database stores accepted specs, content-addressed generation archive blobs, mutable job state, completed case records and terminal report JSON. Each write uses BEGIN IMMEDIATE and one explicit commit. A process-held worker kernel lock permits one supported worker per database; claims use state compare-and-swap. The evaluator consumes captured EvalSuite and an explicit generation ID through EvalService.evaluate_case, never rereading changed source suites. The worker owns its NVIDIA clients, operation budget, shutdown signal and cleanup.

### State and commit-point model

Canonical state is the SQLite job row plus its referenced archive/spec/case rows. Submission commits archive+snapshot+queued row in one SQLite transaction. Before publishing that job row, a separate quota transaction prepays validated case executions under a permanent owner/store/key reservation. The current interface selects one configuration, so units equal validated cases; interactive calls remain one unit. A submission failure before the job COMMIT leaves no queued job, while a committed quota reservation can remain. Same-key retry reuses that reservation without charging again; a different request digest conflicts. No cross-database atomicity is claimed. Queued reservations consume no active slot; worker activation acquires one slot without another charge. Worker claim commits running state before any provider activity; case completion commits the result before another case starts. Terminal report bytes, bounded transport-part hashes and terminal job state commit together. No external provider effect is claimed atomic with SQLite.

| Transition | Commit operation | Recovery |
|---|---|---|
| absent -> prepaid | Idempotent quota COMMIT after full validation | Retry same owner/store/key and request digest; never recharge. |
| prepaid -> queued | SQLite COMMIT of archive/spec/job plus reservation ID and units | Retry same idempotency key; no queued row on rollback. |
| queued -> running | SQLite COMMIT of guarded claim | Interrupted running work never reexecutes automatically. |
| running case -> completed case | SQLite COMMIT of typed result | Retain completed cases; don't replay uncertain case. |
| running -> terminal | SQLite COMMIT of report+state | Read immutable report and digest. |
| retained -> expired | SQLite COMMIT deleting expired payloads | Metadata/digest remains inspectable; result read reports expiry. |

### Invariants

1. Exactly one accepted job exists per owner/idempotency key and input digest.
2. Queue saturation never starts provider work; terminal jobs never claim again.
3. Only the worker holding the process kernel lock recovers/executes jobs; no two supported workers execute one job concurrently.
4. Every case uses the accepted suite and archived generation; source activation/retention can't change accepted inputs.
5. Every inspection, result and cancellation enforces owner+scope before reading caller-specific payloads.
6. A running interruption preserves completed cases and reports an uncertain active case; no uncertain provider outcome is automatically repeated.
7. A terminal state and report hash commit together; cleanup failure can't rewrite terminal success as a failed provider run.
8. Provider attempts can't exceed the accepted limit; missing token usage remains unavailable.
9. Validated case executions across every selected configuration determine work units before job publication; duplicates don't recharge and over-budget submissions create no queued job.
10. Terminal quota reconciliation uses retained owner/reservation summaries even after payload quarantine or expiry; accepted cancellation and failure don't refund units.

### Fault matrix and lifecycle closure

| Effect or boundary | Injected event | Required state and recovery | Evidence |
|---|---|---|---|
| Directory/create/connect/schema | permission or SQLite failure | No accepted job; fix owned volume and retry key | storage creation test |
| Generation export/read/hash | missing/tampered source generation | No accepted job/provider work | capture rejection test |
| Submission insert/COMMIT | interruption before/after quota or job commits | No partial job row/blob; same-key retry reuses prepaid units and rejects changed inputs | `test_uncertain_job_acceptance_never_recharges_weight` |
| Post-submit response | disconnect | queued work stays inspectable | disconnect test |
| Concurrent submit | same key or saturated queue | one accepted row, no over-admission | concurrent submit test |
| Worker lock/claim | competing process | second worker doesn't execute | process worker test |
| Before provider call | cancellation/deadline/call limit | blocked before next request | budget test |
| During provider call | cancellation/shutdown/crash | drain when alive; retain partial/uncertain evidence after restart | cancellation/process crash tests |
| Case result COMMIT | failure before/after commit | uncommitted response stays uncertain; committed case remains saved; neither replays | `test_case_commit_fault_recovers_only_unconfirmed_provider_result` |
| Quota completion -> job report | actual process exit after quota completion | Running job recovers interrupted with persisted completed cases, unchanged charge and no replay | `test_process_exit_after_quota_completion_never_replays_completed_cases` |
| Terminal report COMMIT | failure before/after commit | rollback leaves recoverable running state; committed terminal report stays terminal; saved cases remain | `test_terminal_report_commit_fault_preserves_cases_and_restart_outcome` |
| Generation restore/hash | corrupted archive | fail without provider call; retain diagnosis | archive tamper test |
| Retention transaction | failure before/after commit | expired payload deletion is atomic; another owner retains its report and shared archive | `test_retention_commit_fault_keeps_owner_payloads_atomic` |
| Phoenix lookup | 401/403/500/transport | propagate; never create project | pinned-client exception tests |

No file replacement or deletion is used as the job commit point. Temporary archive files are transaction-owned and removed by TemporaryDirectory; their cleanup can't remove unrelated paths. Physical database durability depends on SQLite FULL synchronization and the local volume's guarantees; network/distributed filesystems aren't supported.

## Failure And Recovery Rules

Rejected submissions remain absent. A duplicate key returns the accepted job even after client disconnect. Interrupted running jobs become terminal interrupted after a new worker obtains the exclusive lock; queued jobs can start because they haven't invoked providers. Explicit cancellation and deadlines remain separate. A failed case retains completed cases and names unfinished ones. Reports expire after the configured retention; cross-principal or unauthenticated report requests never return content.

## Commit Plan

1. feat(eval): persist immutable job submissions, owner isolation and bounded claims.
2. feat(eval): execute pinned cases with recoverable worker lifecycle and usage evidence.
3. feat(eval): expose application-job CLI/MCP and documented report retention.
4. fix(eval): create Phoenix project only after confirmed absence.

## Branch And PR Flow

Use feat/durable-evaluation-jobs stacked on reviewed #100/#119/#98. Run focused lifecycle tests and full Ruff/Pyright/offline pytest. Review the final complete local diff through Qodo using a pushed dependency base and ticket URLs. Reproduce findings, fix supported issues, rerun focused/full checks and final review. Open/attach a PR, wait for CI and exact-head Qodo, coordinate root before merge. No force push, manual finding status, deployment or Tasks claim.

## Test Plan

Run uv run --no-sync pytest -q tests/test_evaluation_jobs.py plus affected provider/CLI/MCP/eval tests. Required gates: uv run --no-sync ruff check; uv run --no-sync pyright; uv run --no-sync pytest -q -m 'not live and not docker_live'. Exercise real process locks and restart using subprocesses. Actual NVIDIA quality and production rollout remain separate root gates. Qodo reviews the final verified snapshot; fresh fixes require renewed checks/review.

## Definition Of Done

All accepted jobs survive client disconnect/restart with unchanged captured inputs. No supported competing worker executes the same job. Owner isolation, weighted allowance, queue/call/deadline limits, cancellation, interruption and immutable report hashes pass actual boundary tests. CLI and authenticated MCP report paths match docs. Full checks and exact-head reviews pass; no unsupported distributed or Tasks claim is made.

## Assumptions And Defaults

Initial supported topology is one worker per local job database. An accepted job's authorization snapshot contains no bearer token or provider credential; expired delegated authority blocks a queued job before execution. Active provider requests may have incurred usage before cancellation or process interruption, so missing usage remains explicitly unavailable. The full generation archive is captured once per generation and shared by accepted jobs until report retention expires.


## Verification scope

Runtime tests exercise CLI/MCP disconnect recovery, process-kill recovery, generation pruning/restoration, case and terminal commit faults, provider retry bounds, ownership, weighted allowance, queue bounds, bounded report reads, report integrity/expiry and confirmed-404 Phoenix lookup. The PR records commands, tested revisions, review coverage and exact-head CI/Qodo results. Package receipts identify their source revision, artifact hash and public bundle separately. The fault matrix doesn't claim a separate executed test for every filesystem or physical-storage fault. Production volume, live evaluation quality and deployment evidence belong to the release gate.

## Archive reuse and retention coordination

This extension addresses `cd863977` after its real public submit/digest-read/expiry reproduction. It uses the existing capture kernel lock, without another lock framework or schema change. The prior capture-order correction checks principal acquisition before global acquisition.

### Commit and ownership model

Canonical accepted state remains the job database's committed job, immutable snapshot and referenced archive. The job commit point remains `connection.commit()` in the store's submission transaction, after the existing separate quota acceptance. This change doesn't make the two databases atomic or refund accepted units.

An in-flight submission holds the store's existing `.capture.lock` from reusable-archive observation through job publication or abort. Retention can expire job payloads while that lock's held, but it can't delete unreferenced archives until it owns the same lock. Retention takes that lock without waiting before its SQLite transaction, retains it through COMMIT, and skips only archive deletion if another capture owns it. The store initializes the persistent lock inode before archive observation is possible. Normal code never unlinks it; process exit releases the kernel lock.

| State | Transition | Visible result and recovery |
|---|---|---|
| Retained archive, no capture | Capture acquires existing kernel lock | Archive observation has an in-flight owner. |
| In-flight capture, expired old job | Retention COMMIT expires old payloads and defers archive deletion | Old result reports expired; capture can still publish its verified bytes. |
| Prepared new job | Submission COMMIT publishes snapshot/job/archive reference | The real worker and retained-result reader consume the accepted snapshot. |
| Capture abort before publication | Existing context cleanup releases capture lock | No new job or provider execution; later `eval-jobs prune` or worker maintenance can collect the unreferenced archive. |
| No capture and no retained reference | Retention COMMIT removes the archive | Public archive lookup returns absent. |
| Process exit during capture | OS releases the kernel lock | A restarted service's existing prune entry can collect unreferenced bytes. |
| Retention transaction error | Existing SQLite rollback or uncertain postcommit inspection | Preserve the store; inspect and repeat the idempotent prune entry. No accepted job or quota outcome is rewritten. |

### Invariants and enforcement

1. An archive verified for an in-flight capture is never deleted before that capture publishes or aborts; the existing global capture lock enforces ownership.
2. Expired report/case/snapshot payloads are still deleted under the existing retention transaction even when archive GC is deferred.
3. Archive deletion happens only when retention owns the capture lock and no retained job references that generation in the same write transaction.
4. A rejected principal never acquires the global slot; a valid capture keeps it until publication or cleanup.
5. Abort or process death never leaves a durable archive lease. The existing later purge entry reclaims unreferenced bytes after lock release; it doesn't wait on a lock held by the same submission.
6. Capture failure, including an injected storage-full error, doesn't trigger provider replay, quota rewriting or inline recursive GC. A caller repairs volume access, then uses the existing prune/restart entry and same-key submission recovery.

### Fault matrix and lifecycle closure

| Boundary | Injected event or concurrent actor | Required observation and recovery | Test scope |
|---|---|---|---|
| Capture-lock initialization/open | Existing file, unsafe alias or OS open failure | Reuse the existing regular inode; existing typed storage boundary rejects access failures without a new accepted job | Existing lock/access guards plus focused initialization checks |
| Verified archive read -> new job publication | Actual expiry purge removes the final old reference | Old payload expires, archive remains, valid public submit succeeds and real worker/report consumer accepts it | Named retention-between-observation/publication regression |
| Busy capture -> retention COMMIT | Capture owns the real kernel lock | Payload expiry completes without waiting; archive deletion is deferred | Same public race regression |
| Capture abort after deferred GC | Storage-full fault or explicit precommit failure | No new accepted row/charge; lock releases and later public prune collects archive | Named abort/space/recovery regression |
| Capture process exit | Child exits while holding the real capture lock | Parent/restarted service can acquire it and reclaim unreferenced archive | Real process lock/restart regression |
| Retention before/after COMMIT | Existing transaction fault injection | Rollback preserves prior rows; committed expiry remains observable and repeat prune is idempotent | Existing retention commit-fault lifecycle checks |
| Published consumer finishes and expires | Real worker then real retention | Referenced archive remains until the new retained job also expires; later prune removes it | Named publish/consumer/expiry lifecycle regression |

The physical-volume guarantees remain SQLite's and the local filesystem's guarantees. Storage-full injection models the reported OS effect; it doesn't claim a physically exhausted production volume. A cleanup or postcommit fault can't justify rewriting accepted quota history or replaying providers. The proof receipt will bind these tests, the inspector/consumer results, remaining warnings and exact final review/CI head.
