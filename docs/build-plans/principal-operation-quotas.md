# Principal Operation Quotas Build Plan

## Summary

Implement #98 against main ba2c19d. #97's merged OAuth foundation supplies immutable principals; external issuer acceptance remains open. One review unit adds accepted-work accounting under one hosted instance. Authentication never charges operation usage.

## Skills To Use

| Stage | Skill | Responsibility |
| --- | --- | --- |
| Standards | qodo-get-rules | Retrieved Python docstrings, English identifiers, layer boundaries, valid typed defaults and pinned dependency rules. No dependencies change. |
| Storage | sqlite-concurrency-hardening, transaction-proof | Immediate transactions serialize admission, revocation, limits and outcome transitions. |
| Behavior | failure-path-testing | Reject saturation and revoked authority before provider work; drain cancellation before releasing active capacity. |
| Interface | dx-audit | Check recovery messages, configuration and portal accounting agreement. |
| Review | qodo-review, qodo-review-resolver | Eligible Git repository with pushed origin/main; inspect final local diff and final pushed head. |

## Scope

In scope: principal/global active limits, UTC-day accepted operations, persisted completed/failed/cancelled/interrupted outcomes, beta migration and portal totals. Out of scope: external issuer deployment (#97), durable job submission deduplication (#101), capacity claims (#103), cross-replica coordination and billing.

## Package Layout / File-to-Task Mapping

- `storage/operation_store.py`: transactional admission rows, authority checks and outcome summaries.
- `services/authorization.py`: canonical issuer/subject fields and principal identity serialization.
- `services/operation_admission.py`: limits, admission lifecycle and recovery errors.
- `interfaces/mcp.py`: pass principal and operation name into the owned worker lifecycle.
- `services/beta_auth.py`: authenticate without charging; show accepted-operation usage.
- `settings.py`, `.env.example`, hosted docs: configuration and accounting semantics.

## Dependencies And Settings

No dependency changes. Add principal concurrency (2) and daily allowance (500) settings; beta keys retain their existing configured daily allowance. Reuse the hosted auth SQLite path for atomic beta revocation and admission, including OAuth-only operation rows. Local CLI/stdio calls remain outside hosted quotas. Single instance only.

## Canonical Contracts

Accepted search/preflight/compilation/evaluation execution charges once when its admission row commits, including later provider failure or cancellation. Discovery, health, resource reads and polling never enter operation admission. Principal identity hashes issuer plus subject; changing OAuth client or beta key doesn't reset usage. Overload returns retry guidance; daily exhaustion resets at the next UTC midnight. Provider usage stays unavailable unless actually measured. #101 owns submission idempotency before worker admission.

## TDD And BDD Implementation Strategy

Public seams are admission service, beta authentication/portal and MCP executor. Cover atomic quota boundaries, principal isolation, global saturation, revoked beta keys between authentication/admission, failed provider work and cancellation drain. Use deterministic real SQLite concurrency and blocked worker tests; no NVIDIA calls.

## Component Design

Storage returns a typed accounting/credential snapshot under its write lock; the service owns authority and quota decisions before storage reserves the row. No HTTP objects enter storage. Service owns scope validation and maps storage rejection to sanitized caller guidance. Executor owns the active worker and finishes the persisted outcome only after provider cleanup. SQLite COMMIT of an accepted row is the charge point. Rows transition accepted -> completed/failed/cancelled; process loss releases a private kernel-backed owner lease and startup and each reservation recover its accepted rows to interrupted. Recovery never refunds accepted work.

## Failure And Recovery Rules

| Phase or event | Durable state and recovery |
| --- | --- |
| Open/create/schema/read/BEGIN/insert/COMMIT failure | No provider work; reject storage-unavailable, preserve prior rows, operator repairs volume/lock then retries. SQLite handles transaction rollback. |
| Concurrent quota or authority mutation | BEGIN IMMEDIATE rechecks active key/account and limits; exactly one serialized result; rejected calls create no accepted row. |
| Provider failure | Accepted charge retained, outcome failed; caller retries under allowance. |
| Cancellation/shutdown | Worker drains; outcome cancelled; active capacity releases after cleanup. |
| Outcome persistence failure | Preserve accepted row and charge, reject further admission until operator recovery; don't misreport provider success as refunded work. |
| Process loss | Startup and reservation-time recovery mark previous accepted rows interrupted; no refund. Kernel-backed owner leases prove workers can no longer run, including reused container PIDs. |

Invariants: accepted totals never exceed configured daily limits; active rows never exceed principal/global limits; rejected/unauthorized work never starts a provider; rotation never resets an account's usage; only accepted rows transition outcomes. Filesystem durability follows SQLite's supported guarantees; disk failure after a commit can't establish whether a client observed the response.

Lifecycle evidence: create/admit/read/reopen, concurrent admission, finish twice, revocation before commit, locked database, rollback insertion failure, interrupted recovery, cancellation drain, provider failure. No separate file publication/rollback protocol is introduced.

## Commit Plan

1. Add atomic principal operation admission and lifecycle tests.
2. Integrate MCP/beta accounting, migration docs and HTTP regressions.

## Branch And PR Flow

Use clean isolated `codex/principal-operation-quotas` from origin/main. Run focused/full checks, final local Qodo review against pinned remote base, reproduce findings, fix and reverify. Open a PR linking #98 and #97; close #98 only when integrated job accounting acceptance also holds. Wait for Qodo review completed at final pushed SHA plus CI. Root coordinates merge/release.

## Test Plan

Run `uv sync --locked --group test --group dev`; `uv run --no-sync ruff check`; `uv run --no-sync pyright`; `uv run --no-sync pytest -q -m 'not live and not docker_live'`. Focused tests cover SQLite/admission, beta auth and real HTTP MCP requests. External issuer, deployment and live provider traffic remain separate evidence. Qodo Local Review eligible; base is pushed ba2c19d.

## Definition Of Done

Hosted authentication charges zero operations. Only admitted work charges accepted usage. Concurrency, cancellation, revocation, failure and retry tests pass. Portal/docs/settings agree. Final local and pushed-head reviews plus required offline gates pass. No external OAuth or production capacity claim is inferred.

## Assumptions And Defaults

One hosted instance; no billing; provider usage unknown unless measured. Existing historical HTTP counters remain historical and aren't silently reinterpreted as accepted-operation counts. Root owns release integration and external issuer decisions.

Auth-disabled HTTP remains a trusted local development mode with only the global
worker cap. There's no principal/daily accounting without authentication. Production
requires `POLICYNIM_MCP_REQUIRE_AUTH=true`; a non-loopback unprotected bind warns at
startup. This preserves existing open development compatibility without inventing
an authenticated identity.
